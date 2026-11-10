"""JavaScript / TypeScript (@playwright/test) test discovery.

A small, string/comment/template/regex-aware scanner (no external parser) that
finds every ``test()`` declaration (including ``test.describe`` groups,
``test.only/skip/fixme``, hooks and ``test.step``) and slices each one into its
OWN self-contained native test file by removing the OTHER declarations from the
file text. Nothing is rewritten, so the native source is lossless.
"""
from __future__ import annotations

import re

from Core.script_import.models import (
    ExtractedTest, FORMAT_JAVASCRIPT, FORMAT_TYPESCRIPT, RUNTIME_NODE,
    STATUS_NEEDS_REVIEW, STATUS_INVALID, STATUS_VALID,
    SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO, find_test_id,
)

_REGEX_PRECEDERS = set("(,=:[!&|?{};+-*%<>~^") | {""}
_KEYWORD_BEFORE_REGEX = ("return", "typeof", "case", "do", "else", "in", "of", "delete", "void", "throw", "new")


def code_mask(src: str) -> list[bool]:
    """mask[i] is True when src[i] is real code (not string/comment/regex/template text)."""
    n = len(src)
    mask = [True] * n
    i = 0
    tmpl_stack: list[int] = []   # brace depth markers for `${` inside templates
    brace = 0
    last_sig = ""                 # last significant code char
    last_word = ""
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            j = n if j == -1 else j
            for k in range(i, j):
                mask[k] = False
            i = j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            j = n if j == -1 else j + 2
            for k in range(i, j):
                mask[k] = False
            i = j
            continue
        if c in ("'", '"'):
            j = i + 1
            while j < n and src[j] != c and src[j] != "\n":
                j += 2 if src[j] == "\\" else 1
            end = min(j + 1, n)
            for k in range(i, end):
                mask[k] = False
            i = end
            last_sig, last_word = c, ""
            continue
        if c == "`":
            mask[i] = False
            i += 1
            while i < n:
                if src[i] == "\\":
                    mask[i] = False
                    if i + 1 < n:
                        mask[i + 1] = False
                    i += 2
                    continue
                if src[i] == "`":
                    mask[i] = False
                    i += 1
                    break
                if src[i] == "$" and i + 1 < n and src[i + 1] == "{":
                    mask[i] = mask[i + 1] = False
                    # scan the interpolation as code until its matching brace
                    depth = 1
                    i += 2
                    start = i
                    while i < n and depth:
                        if src[i] == "{":
                            depth += 1
                        elif src[i] == "}":
                            depth -= 1
                            if depth == 0:
                                break
                        i += 1
                    # interpolation content stays "code"=False for simplicity (treated as text)
                    for k in range(start, min(i + 1, n)):
                        mask[k] = False
                    i += 1
                    continue
                mask[i] = False
                i += 1
            last_sig, last_word = "`", ""
            continue
        if c == "/":
            is_regex = last_sig in _REGEX_PRECEDERS or last_word in _KEYWORD_BEFORE_REGEX
            if is_regex:
                j = i + 1
                in_class = False
                while j < n and src[j] != "\n":
                    if src[j] == "\\":
                        j += 2
                        continue
                    if src[j] == "[":
                        in_class = True
                    elif src[j] == "]":
                        in_class = False
                    elif src[j] == "/" and not in_class:
                        break
                    j += 1
                end = min(j + 1, n)
                while end < n and src[end].isalpha():
                    end += 1
                for k in range(i, end):
                    mask[k] = False
                i = end
                last_sig, last_word = "/", ""
                continue
        if c.isalnum() or c in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            last_word = src[i:j]
            last_sig = "a"
            i = j
            continue
        if not c.isspace():
            last_sig, last_word = c, ""
        i += 1
    return mask


def match_close(src: str, mask: list[bool], open_idx: int) -> int:
    pairs = {"(": ")", "[": "]", "{": "}"}
    opener = src[open_idx]
    closer = pairs[opener]
    depth = 0
    for i in range(open_idx, len(src)):
        if not mask[i]:
            continue
        if src[i] == opener:
            depth += 1
        elif src[i] == closer:
            depth -= 1
            if depth == 0:
                return i
    return -1


def split_args(src: str, mask: list[bool], open_idx: int, close_idx: int) -> list[tuple[int, int]]:
    """Top-level comma separated argument spans (start, end) inside ( ... )."""
    spans, depth, start = [], 0, open_idx + 1
    for i in range(open_idx + 1, close_idx):
        if not mask[i]:
            continue
        ch = src[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif ch == "," and depth == 0:
            spans.append((start, i))
            start = i + 1
    if src[start:close_idx].strip():
        spans.append((start, close_idx))
    return spans


_CALL = re.compile(
    r"(?<![\w$.])(?P<callee>test(?:\.(?:describe(?:\.(?:serial|parallel|only|skip|fixme))?|only|skip|fixme|fail|slow|step|beforeEach|afterEach|beforeAll|afterAll|use|setTimeout|configure))*"
    r"|describe(?:\.(?:only|skip|serial|parallel))?|it(?:\.(?:only|skip))?)\s*\("
)


def _string_value(text: str):
    """Value of a plain string literal arg, or None if not a simple literal."""
    t = text.strip()
    if len(t) >= 2 and t[0] in "'\"" and t[-1] == t[0]:
        body = t[1:-1]
        return re.sub(r"\\(.)", lambda m: {"n": "\n", "t": "\t"}.get(m.group(1), m.group(1)), body)
    if len(t) >= 2 and t[0] == "`" and t[-1] == "`" and "${" not in t:
        return t[1:-1]
    return None


def _kind(callee: str) -> str:
    if ".describe" in callee or callee.startswith("describe"):
        return "describe" if not callee.endswith(".configure") else "ignore"
    if callee.endswith((".beforeEach", ".afterEach", ".beforeAll", ".afterAll")):
        return "hook"
    if callee.endswith((".use", ".setTimeout", ".configure", ".slow", ".fixme", ".fail")) and "." in callee and not callee.endswith(".skip"):
        return "config"
    if callee.endswith(".step"):
        return "step"
    return "test"


def discover(src: str):
    """Return (tests, groups, mask). Each test: dict(start,end,title,dynamic,params,body_span,groups,skipped,only)."""
    mask = code_mask(src)
    # brace pairs for ancestry checks
    stack, pairs = [], []
    for i, ch in enumerate(src):
        if not mask[i]:
            continue
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            pairs.append((stack.pop(), i))
    calls = []
    for m in _CALL.finditer(src):
        if not mask[m.start()]:
            continue
        open_idx = m.end() - 1
        close_idx = match_close(src, mask, open_idx)
        if close_idx == -1:
            continue
        callee = m.group("callee")
        calls.append({
            "callee": callee, "kind": _kind(callee), "start": m.start(), "open": open_idx, "close": close_idx,
        })
    groups = [c for c in calls if c["kind"] == "describe"]
    tests = []
    for c in calls:
        if c["kind"] != "test":
            continue
        args = split_args(src, mask, c["open"], c["close"])
        title_text = src[args[0][0]:args[0][1]] if args else ""
        title = _string_value(title_text)
        # callback = last argument that contains => or function
        cb = None
        for a in reversed(args):
            seg = src[a[0]:a[1]]
            if "=>" in seg or re.search(r"\bfunction\b", seg):
                cb = a
                break
        enclosing_groups = [g for g in groups if g["open"] < c["start"] < g["close"]]
        enclosing_groups.sort(key=lambda g: g["start"])
        c_groups = enclosing_groups
        # parameterised: nested inside a non-describe block that looks like a loop
        dynamic = title is None
        for (o, cl) in pairs:
            if o < c["start"] < cl and not any(g["open"] < o < g["close"] and g["start"] <= o for g in []):
                before = src[max(0, o - 160):o]
                if re.search(r"(for\s*\([^)]*\)|\.forEach\s*\([^)]*=>|\.map\s*\([^)]*=>|\bwhile\s*\([^)]*\))\s*$", before.rstrip() + " ", re.S) \
                        and not any(g["open"] < o < g["close"] and src[g["open"]:o].count("(") and False for g in groups):
                    dynamic = True
        # forEach(...) loops use a callback paren, so also check the call line text
        line_start = src.rfind("\n", 0, c["start"]) + 1
        prefix = src[max(0, c["start"] - 200):c["start"]]
        if re.search(r"\.(forEach|map)\s*\(\s*(\([^)]*\)|\w+)\s*=>\s*\{?\s*$", prefix):
            dynamic = True
        groups_chain = []
        for g in enclosing_groups:
            gargs = split_args(src, mask, g["open"], g["close"])
            gt = _string_value(src[gargs[0][0]:gargs[0][1]]) if gargs else None
            groups_chain.append(gt if gt is not None else "<dynamic group>")
        # a trailing `;` belongs to the declaration
        end = c["close"] + 1
        k = end
        while k < len(src) and src[k] in " \t":
            k += 1
        if k < len(src) and src[k] == ";":
            end = k + 1
        callee = c["callee"]
        tests.append({
            "start": c["start"], "end": end, "title": title, "title_raw": title_text.strip(),
            "dynamic": dynamic, "callback": cb, "groups": groups_chain,
            "skipped": callee.endswith(".skip") or callee.endswith(".fixme"), "only": callee.endswith(".only"),
            "callee": callee, "group_nodes": enclosing_groups,
        })
    hooks = []
    for c in calls:
        if c["kind"] != "hook":
            continue
        args = split_args(src, mask, c["open"], c["close"])
        cb = None
        for a in reversed(args):
            seg = src[a[0]:a[1]]
            if "=>" in seg or re.search(r"\bfunction\b", seg):
                cb = a
                break
        hooks.append({
            "phase": c["callee"].rsplit(".", 1)[-1], "callback": cb, "start": c["start"], "close": c["close"],
            "groups": [g for g in groups if g["open"] < c["start"] < g["close"]],
        })
    return tests, groups, mask, hooks


def _remove_spans(src: str, spans: list[tuple[int, int]]) -> str:
    out, cursor = [], 0
    for s, e in sorted(spans):
        out.append(src[cursor:s])
        out.append("\n" * src[s:e].count("\n"))
        cursor = e
    out.append(src[cursor:])
    return "".join(out)


def callback_info(src: str, mask: list[bool], test: dict):
    """(params_text, body_text, is_block) of a test callback."""
    cb = test["callback"]
    if not cb:
        return "", "", False
    seg_start, seg_end = cb
    seg = src[seg_start:seg_end]
    arrow = seg.find("=>")
    if arrow != -1 and not re.search(r"\bfunction\b", seg[:arrow]):
        head = seg[:arrow]
        params = head.strip()
        if params.startswith("async"):
            params = params[5:].strip()
        rest = seg[arrow + 2:].strip()
        if rest.startswith("{"):
            abs_open = seg_start + seg.find("{", arrow)
            close = match_close(src, mask, abs_open)
            return params, src[abs_open + 1:close], True
        return params, rest, False
    fm = re.search(r"function\s*\w*\s*\(", seg)
    if fm:
        p_open = seg_start + fm.end() - 1
        p_close = match_close(src, mask, p_open)
        brace = src.find("{", p_close)
        close = match_close(src, mask, brace)
        return src[p_open + 1:p_close], src[brace + 1:close], True
    return "", "", False


_CONST_LINE = re.compile(
    r"^(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::\s*(?:string|number|boolean))?\s*=\s*(.+?);?\s*$"
)


def top_level_constants(source: str, mask: list[bool]) -> list[tuple[str, str]]:
    """Single-line module-level ``const NAME = <literal>`` declarations (outside any block).

    Only the declaration text is captured; the converter decides whether the value is a literal it
    can reproduce, so nothing here can silently change behaviour."""
    out: list[tuple[str, str]] = []
    depth = 0
    pos = 0
    for line in source.split("\n"):
        if depth == 0:
            m = _CONST_LINE.match(line.strip())
            if m:
                out.append((m.group(1), m.group(2).strip()))
        for k, ch in enumerate(line):
            if mask[pos + k]:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
        pos += len(line) + 1
    return out


def extract_js(source: str, filename: str, fmt: str) -> list[ExtractedTest]:
    stem = re.sub(r"\.[^.]+$", "", filename or "script")
    tests, _groups, mask, hooks = discover(source)
    module_constants = top_level_constants(source, mask)
    if not tests:
        t = ExtractedTest(test_id=find_test_id(filename) or stem, name=stem, source_filename=filename,
                          source_format=fmt, original_source=source, source_runtime=RUNTIME_NODE)
        t.status = STATUS_INVALID
        t.add(SEVERITY_ERROR, "NO_TEST_FOUND", "No test('…', async ({ page }) => { … }) declaration was found in this file.")
        return [t]
    all_spans = [(t["start"], t["end"]) for t in tests]
    results: list[ExtractedTest] = []
    for index, t in enumerate(tests):
        title = t["title"] or t["title_raw"] or f"test {index + 1}"
        found = find_test_id(title, filename if len(tests) == 1 else "", " ".join(t["groups"]))
        original = source[t["start"]:t["end"]]
        name = re.sub(TEST_ID_STRIP, "", title).strip(" -:_") or title
        et = ExtractedTest(
            test_id=found or f"{stem}_{index + 1:02d}", name=name or title, source_filename=filename,
            source_format=fmt, original_source=original, index_in_file=index,
            group=" > ".join(g for g in t["groups"] if g), id_generated=found is None,
            source_runtime=RUNTIME_NODE, skipped_in_source=t["skipped"],
        )
        other = [s for i, s in enumerate(all_spans) if i != index]
        native = _remove_spans(source, other)
        # collapse describe wrappers that now contain nothing -- keep them (valid JS), only blank lines tidied
        et.native_source = re.sub(r"\n{4,}", "\n\n\n", native)
        et.notes["body_params"], et.notes["body"], et.notes["body_is_block"] = callback_info(source, mask, t)
        # hooks that apply to THIS test: top-level hooks and hooks of every enclosing describe
        applicable = []
        group_ids = {id(g) for g in t["group_nodes"]}
        for h in hooks:
            scoped = {id(g) for g in h["groups"]}
            if scoped <= group_ids:
                params, body, is_block = callback_info(source, mask, {"callback": h["callback"]})
                applicable.append({"phase": h["phase"], "params": params, "body": body, "is_block": is_block,
                                   "depth": len(scoped), "start": h["start"]})
        applicable.sort(key=lambda h: (h["depth"], h["start"]))
        et.notes["hooks"] = applicable
        et.notes["constants"] = module_constants
        et.notes["raw_title"] = t["title_raw"]
        if t["dynamic"]:
            et.add(SEVERITY_WARNING, "PARAMETERIZED",
                   "This test is generated dynamically (loop / forEach / computed title). It cannot be split into "
                   "fixed independent test cases statically; the whole declaration is preserved and needs review.")
        if t["skipped"]:
            et.add(SEVERITY_INFO, "SKIPPED_IN_SOURCE", "Declared with test.skip / test.fixme; it will not run natively.")
        if t["only"]:
            et.add(SEVERITY_WARNING, "TEST_ONLY", "Declared with test.only; remove .only so other imported tests are not excluded when run natively.")
        if found is None:
            et.add(SEVERITY_WARNING, "ID_GENERATED",
                   "No Test Case ID (e.g. TC_XXX_0001) found in the test title; a temporary ID was generated. "
                   "Map it to a test case before saving.")
        results.append(et)
    return results


TEST_ID_STRIP = re.compile(r"(?<![A-Za-z0-9])TC[_-](?:[A-Za-z0-9]+[_-])*\d+(?![A-Za-z0-9])", re.IGNORECASE)
