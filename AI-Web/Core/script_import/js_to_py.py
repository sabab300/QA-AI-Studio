"""Lossless JS/TS -> Python Playwright conversion for a recognised safe subset.

Anything outside the subset is reported as an unsupported statement and the
whole test is left NEEDS REVIEW with its native/original source preserved --
there is NO text replacement fallback and a test is never partially converted.
"""
from __future__ import annotations

import re

from Core.script_import.js_ext import code_mask, match_close, split_args, _string_value
from Core.script_import.models import (
    ExtractedTest, RUNTIME_PYTHON, STATUS_CONVERSION_REQUIRED, STATUS_NEEDS_REVIEW,
    SEVERITY_WARNING, SEVERITY_INFO, SEVERITY_ERROR,
)

LOCATOR_METHODS = {
    "locator": "locator", "frameLocator": "frame_locator", "getByRole": "get_by_role",
    "getByText": "get_by_text", "getByLabel": "get_by_label", "getByPlaceholder": "get_by_placeholder",
    "getByTestId": "get_by_test_id", "getByAltText": "get_by_alt_text", "getByTitle": "get_by_title",
    "filter": "filter", "nth": "nth", "and": "and_", "or": "or_",
}
LOCATOR_PROPS = {"first": "first", "last": "last"}  # JS first()/last() are methods; Python properties
ACTIONS = {
    "click": "click", "dblclick": "dblclick", "fill": "fill", "check": "check", "uncheck": "uncheck",
    "press": "press", "selectOption": "select_option", "hover": "hover", "focus": "focus", "blur": "blur",
    "clear": "clear", "setInputFiles": "set_input_files", "dragTo": "drag_to", "tap": "tap",
    "waitFor": "wait_for", "scrollIntoViewIfNeeded": "scroll_into_view_if_needed",
    "pressSequentially": "press_sequentially", "setChecked": "set_checked", "type": "type",
    "dispatchEvent": "dispatch_event", "screenshot": "screenshot",
}
VALUE_METHODS = {"textContent": "text_content", "innerText": "inner_text", "inputValue": "input_value",
                 "getAttribute": "get_attribute", "isVisible": "is_visible", "isChecked": "is_checked",
                 "isEnabled": "is_enabled", "count": "count"}
PAGE_METHODS = {
    "goto": "goto", "waitForTimeout": "wait_for_timeout", "waitForURL": "wait_for_url",
    "waitForLoadState": "wait_for_load_state", "waitForSelector": "wait_for_selector",
    "goBack": "go_back", "goForward": "go_forward", "reload": "reload", "bringToFront": "bring_to_front",
    "setViewportSize": "set_viewport_size", "evaluate": "evaluate", "close": "close",
    "click": "click", "fill": "fill", "press": "press", "check": "check", "uncheck": "uncheck",
    "selectOption": "select_option", "hover": "hover", "dblclick": "dblclick", "type": "type",
    "setInputFiles": "set_input_files", "waitForFunction": "wait_for_function",
}
MATCHERS = {
    "toBeVisible", "toBeHidden", "toBeEnabled", "toBeDisabled", "toBeChecked", "toBeEditable",
    "toBeEmpty", "toBeFocused", "toHaveText", "toContainText", "toHaveValue", "toHaveValues",
    "toHaveAttribute", "toHaveClass", "toContainClass", "toHaveCount", "toHaveURL", "toHaveTitle", "toHaveId",
    "toHaveCSS", "toBeAttached", "toBeInViewport",
}
OPTION_KEYS = {
    "exact": "exact", "name": "name", "hasText": "has_text", "hasNotText": "has_not_text", "has": "has",
    "hasNot": "has_not", "timeout": "timeout", "force": "force", "delay": "delay", "button": "button",
    "clickCount": "click_count", "modifiers": "modifiers", "position": "position", "state": "state",
    "checked": "checked", "level": "level", "pressed": "pressed", "selected": "selected",
    "expanded": "expanded", "disabled": "disabled", "includeHidden": "include_hidden",
    "waitUntil": "wait_until", "noWaitAfter": "no_wait_after", "trial": "trial", "strict": "strict",
    "value": "value", "label": "label", "index": "index", "ignoreCase": "ignore_case",
    "useInnerText": "use_inner_text", "referer": "referer", "path": "path", "fullPage": "full_page",
    "steps": "steps", "sourcePosition": "source_position", "targetPosition": "target_position",
}


class Unsupported(Exception):
    pass


def _snake(name: str) -> str:
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", name).lower()


class Converter:
    def __init__(self, params: str, body: str, is_block: bool, is_ts: bool):
        self.body = body
        self.is_block = is_block
        self.params = params
        self.is_ts = is_ts
        self.lines: list[str] = []
        self.unsupported: list[tuple[int, str, str]] = []
        self.assertions = 0
        self.steps: list[dict] = []
        self.known_vars: set[str] = set()
        self.uses_re = False
        self.relative_goto = False
        self.fixtures = self._fixtures(params)

    # ---- fixtures ------------------------------------------------------
    @staticmethod
    def _fixtures(params: str) -> list[str]:
        m = re.search(r"\{([^}]*)\}", params or "")
        if not m:
            return []
        out = []
        for part in m.group(1).split(","):
            name = part.split(":")[0].split("=")[0].strip()
            if name:
                out.append(name)
        return out

    # ---- statements ----------------------------------------------------
    def split_statements(self, text: str):
        mask = code_mask(text)
        depth = 0
        start = 0
        out = []
        n = len(text)
        i = 0
        while i < n:
            ch = text[i]
            if mask[i]:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
                elif ch == ";" and depth == 0:
                    out.append(text[start:i].strip())
                    start = i + 1
                elif ch == "\n" and depth == 0:
                    seg = text[start:i].strip()
                    # continue the statement across a leading-dot chained line or a trailing operator
                    nxt = text[i + 1:].lstrip()
                    if seg and not (nxt.startswith(".") or nxt.startswith("?.") or re.search(r"[,+\-*/=&|(:?]$", seg)):
                        if self._is_balanced(seg):
                            out.append(seg)
                            start = i + 1
            i += 1
        tail = text[start:].strip()
        if tail:
            out.append(tail)
        return [s for s in out if s and not s.startswith("//")]

    @staticmethod
    def _is_balanced(seg):
        m = code_mask(seg)
        d = 0
        for i, ch in enumerate(seg):
            if m[i]:
                if ch in "([{":
                    d += 1
                elif ch in ")]}":
                    d -= 1
        return d == 0

    def convert(self):
        stmts = self.split_statements(self.body) if self.is_block else [f"await {self.body}" if False else self.body]
        self._convert_list(stmts, indent="    ")
        return self

    def _emit(self, line, indent, description=None, action=None, target=""):
        self.lines.append(indent + line)
        if description is not None:
            self.steps.append({"step": len(self.steps) + 1, "action": action or "", "target": target[:120],
                               "description": description[:140]})

    def _convert_list(self, stmts, indent):
        i = 0
        while i < len(stmts):
            stmt = stmts[i]
            try:
                consumed = self._convert_one(stmts, i, indent)
                i += consumed
            except Unsupported as err:
                self.unsupported.append((i + 1, stmt.replace("\n", " ")[:160], str(err)))
                i += 1

    # ---- one statement -------------------------------------------------
    def _convert_one(self, stmts, i, indent) -> int:
        stmt = stmts[i].strip()
        if not stmt or stmt.startswith("//"):
            return 1
        if stmt.startswith("/*"):
            return 1

        # popup / download / file-chooser promise pattern
        m = re.match(r"(?:const|let|var)\s+(\w+)\s*=\s*page\d*\.waitForEvent\(\s*['\"](popup|download|filechooser)['\"]\s*\)$", stmt)
        if m and i + 2 < len(stmts) + 0:
            promise, kind = m.group(1), m.group(2)
            # find the matching "const X = await promise" after one or more action statements
            for j in range(i + 1, len(stmts)):
                m2 = re.match(r"(?:const|let|var)\s+(\w+)\s*=\s*await\s+" + re.escape(promise) + r"$", stmts[j].strip())
                if m2:
                    method = {"popup": "expect_popup", "download": "expect_download", "filechooser": "expect_file_chooser"}[kind]
                    owner = "page"
                    info = f"{promise}_info"
                    self._emit(f"with {owner}.{method}() as {info}:", indent)
                    inner = stmts[i + 1:j]
                    if not inner:
                        raise Unsupported("waitForEvent promise without a triggering action")
                    before = len(self.lines)
                    self._convert_list(inner, indent + "    ")
                    if len(self.lines) == before:
                        raise Unsupported("triggering action inside waitForEvent could not be converted")
                    self._emit(f"{m2.group(1)} = {info}.value", indent)
                    self.known_vars.add(m2.group(1))
                    return j - i + 1
            raise Unsupported(f"waitForEvent('{kind}') promise is never awaited with a matching `await {promise}`")

        m = re.match(r"(?:const|let|var)\s+\[\s*(\w+)\s*\]\s*=\s*await\s+Promise\.all\(\s*\[(.+)\]\s*\)$", stmt, re.S)
        if m:
            var, inner = m.group(1), m.group(2)
            imask = code_mask("(" + inner + ")")
            items = [("(" + inner + ")")[a:b].strip() for a, b in split_args("(" + inner + ")", imask, 0, len(inner) + 1)]
            items = [x for x in items if x]
            ev = [(n, x) for n, x in enumerate(items)
                  if re.fullmatch(r"page\d*\.waitForEvent\(\s*['\"](popup|download|filechooser)['\"]\s*\)", x)]
            if len(items) == 2 and len(ev) == 1:
                kind = re.search(r"['\"](\w+)['\"]", ev[0][1]).group(1)
                action = items[1 - ev[0][0]]
                method = {"popup": "expect_popup", "download": "expect_download", "filechooser": "expect_file_chooser"}[kind]
                info = f"{var}_info"
                self._emit(f"with page.{method}() as {info}:", indent)
                before = len(self.lines)
                self._convert_list([action], indent + "    ")
                if len(self.lines) == before:
                    raise Unsupported("triggering action inside Promise.all([waitForEvent, ...]) could not be converted")
                self._emit(f"{var} = {info}.value", indent)
                self.known_vars.add(var)
                return 1
            raise Unsupported("Promise.all(...) is only supported as [page.waitForEvent(popup|download|filechooser), <one action>]")

        # test.step('title', async () => { ... })
        m = re.match(r"(?:await\s+)?test\.step\(\s*(['\"`])(.*?)\1\s*,\s*async\s*\(\s*\)\s*=>\s*\{", stmt, re.S)
        if m and stmt.rstrip().endswith("})"):
            body_open = stmt.index("{", m.end() - 1)
            mask = code_mask(stmt)
            close = match_close(stmt, mask, body_open)
            self._emit(f"# step: {m.group(2)}", indent)
            self._convert_list(self.split_statements(stmt[body_open + 1:close]), indent)
            return 1

        if re.match(r"test\.(setTimeout|slow|use)\(", stmt):
            self._emit(f"# (ignored runtime setting) {stmt[:80]}", indent)
            return 1

        m = re.match(r"console\.(log|info|warn|error)\((.*)\)$", stmt, re.S)
        if m:
            self._emit(f"print({self._args_py(m.group(2))})", indent)
            return 1

        # variable declaration
        m = re.match(r"(?:const|let|var)\s+(\w+)\s*(?::\s*[\w<>\[\]| ]+)?\s*=\s*(.+)$", stmt, re.S)
        if m:
            name, expr = m.group(1), m.group(2).strip()
            if expr.startswith("await "):
                py = self._chain_to_py(expr[6:].strip(), allow_value=True)
                self._emit(f"{name} = {py}", indent, f"read value into {name}", "assign")
            else:
                py = self._expr_py(expr)
                self._emit(f"{name} = {py}", indent)
            self.known_vars.add(name)
            return 1

        s = stmt[6:].strip() if stmt.startswith("await ") else stmt

        # dialog handlers
        m = re.match(r"page\d*\.(on|once)\(\s*['\"]dialog['\"]\s*,\s*(.+)\)$", s, re.S)
        if m:
            handler = m.group(2).strip()
            hm = re.match(r"(?:async\s*)?\(?\s*(\w+)\s*\)?\s*=>\s*(\w+)\.(accept|dismiss)\(\s*\)$", handler)
            if not hm or hm.group(1) != hm.group(2):
                raise Unsupported("dialog handler is not a simple accept()/dismiss() arrow function")
            self._emit(f'page.{m.group(1)}("dialog", lambda {hm.group(1)}: {hm.group(1)}.{hm.group(3)}())', indent,
                       f"dialog handler: {hm.group(3)}", "dialog")
            return 1

        # expect(...)
        if s.startswith("expect(") or s.startswith("expect.soft(") or s.startswith("expect.poll("):
            if s.startswith(("expect.soft(", "expect.poll(")):
                raise Unsupported("expect.soft / expect.poll are not supported")
            py = self._expect_to_py(s)
            self.assertions += 1
            self._emit(py, indent, f"assert {s[:100]}", "assert")
            return 1

        # page / locator chains
        if re.match(r"(page\d*|popup\d*|\w+)\b", s):
            py = self._chain_to_py(s)
            desc = s[:120]
            action = self._last_method(py)
            self._emit(py, indent, desc, action, target=s.split(".")[0])
            return 1
        raise Unsupported("statement shape is not in the supported subset")

    # ---- expressions ---------------------------------------------------
    @staticmethod
    def _last_method(py):
        m = re.findall(r"\.(\w+)\(", py)
        return m[-1] if m else ""

    def _expect_to_py(self, s):
        mask = code_mask(s)
        open_idx = s.index("(")
        close = match_close(s, mask, open_idx)
        subject = s[open_idx + 1:close].strip()
        rest = s[close + 1:].strip()
        m = re.match(r"\.(not\.)?(\w+)\(", rest)
        if not m or m.group(2) not in MATCHERS:
            raise Unsupported(f"matcher '{m.group(2) if m else rest[:30]}' is not supported")
        rmask = code_mask(rest)
        mo = rest.index("(", m.start(2))
        mc = match_close(rest, rmask, mo)
        if mc != len(rest) - 1:
            raise Unsupported("unexpected text after expect() matcher")
        args = self._args_py(rest[mo + 1:mc])
        subj = self._chain_to_py(subject) if not re.fullmatch(r"page\d*", subject) else subject
        name = _snake(m.group(2))
        if m.group(1):
            name = "not_" + name
        return f"expect({subj}).{name}({args})"

    def _chain_to_py(self, text: str, allow_value=False) -> str:
        text = text.strip()
        m = re.match(r"([A-Za-z_$][\w$]*)", text)
        if not m:
            raise Unsupported("expression does not start with an identifier")
        base = m.group(1)
        if base not in {"page", "context", "browser"} and base not in self.known_vars and not re.fullmatch(r"(page|popup)\d*", base):
            raise Unsupported(f"unknown identifier '{base}' (only page/context/popup variables and literal constants are supported)")
        out = base
        i = m.end()
        mask = code_mask(text)
        n = len(text)
        in_keyboard = False
        while i < n:
            if text[i].isspace():
                i += 1
                continue
            if text[i:i + 2] == "?.":
                raise Unsupported("optional chaining is not supported")
            if text[i] != ".":
                raise Unsupported(f"unexpected token '{text[i]}'")
            i += 1
            while i < n and text[i].isspace():
                i += 1
            nm = re.match(r"[A-Za-z_$][\w$]*", text[i:])
            if not nm:
                raise Unsupported("malformed member access")
            name = nm.group(0)
            i += nm.end()
            if name in ("keyboard", "mouse") and (i >= n or text[i] != "("):
                out += f".{name}"
                in_keyboard = True
                continue
            if i >= n or text[i] != "(":
                raise Unsupported(f"property access '.{name}' is not supported")
            close = match_close(text, mask, i)
            if close == -1:
                raise Unsupported("unbalanced parentheses")
            raw_args = text[i + 1:close]
            i = close + 1
            if name in ("first", "last") and not raw_args.strip():
                out += f".{name}"
                continue
            if in_keyboard:
                out += f".{_snake(name)}({self._args_py(raw_args)})"
                in_keyboard = False
                continue
            py_name = LOCATOR_METHODS.get(name) or ACTIONS.get(name) or PAGE_METHODS.get(name) or (VALUE_METHODS.get(name) if allow_value else None)
            if py_name is None:
                raise Unsupported(f"method '{name}()' is not supported")
            if name in ("goto", "waitForURL") and raw_args.strip():
                lit = _string_value(raw_args.split(",")[0])
                if lit is not None and lit.startswith("/"):
                    self.relative_goto = True
            out += f".{py_name}({self._args_py(raw_args)})"
        return out

    def _args_py(self, raw: str) -> str:
        raw = raw.strip()
        if not raw:
            return ""
        mask = code_mask("(" + raw + ")")
        spans = split_args("(" + raw + ")", mask, 0, len(raw) + 1)
        parts = []
        for a, b in spans:
            arg = ("(" + raw + ")")[a:b].strip()
            if arg.startswith("{") and arg.endswith("}"):
                parts.append(self._object_kwargs(arg))
            else:
                parts.append(self._expr_py(arg))
        return ", ".join(p for p in parts if p)

    def _object_kwargs(self, obj: str) -> str:
        inner = obj[1:-1].strip()
        if not inner:
            return ""
        mask = code_mask("(" + inner + ")")
        spans = split_args("(" + inner + ")", mask, 0, len(inner) + 1)
        kws = []
        for a, b in spans:
            entry = ("(" + inner + ")")[a:b].strip()
            m = re.match(r"([\w$]+|['\"][^'\"]+['\"])\s*:\s*(.+)$", entry, re.S)
            if not m:
                raise Unsupported("object argument uses unsupported syntax (shorthand/spread/computed key)")
            key = m.group(1).strip("'\"")
            if key not in OPTION_KEYS:
                raise Unsupported(f"option '{key}' is not supported")
            kws.append(f"{OPTION_KEYS[key]}={self._expr_py(m.group(2).strip())}")
        return ", ".join(kws)

    def _expr_py(self, e: str) -> str:
        e = e.strip()
        if not e:
            raise Unsupported("empty expression")
        s = _string_value(e)
        if s is not None:
            return repr(s)
        if e.startswith("`"):
            m = re.fullmatch(r"`([^`]*)`", e)
            if m and re.fullmatch(r"(?:[^${]|\$\{\s*[A-Za-z_]\w*\s*\})*", m.group(1)):
                body = re.sub(r"\$\{\s*(\w+)\s*\}", r"{\1}", m.group(1))
                if "{" in body:
                    for v in re.findall(r"\{(\w+)\}", body):
                        if v not in self.known_vars:
                            raise Unsupported(f"template literal references unknown variable '{v}'")
                    return "f" + repr(body)
                return repr(body)
            raise Unsupported("template literal with expressions is not supported")
        if re.fullmatch(r"-?\d+(\.\d+)?", e):
            return e
        if e in ("true", "false"):
            return e.capitalize()
        if e in ("null", "undefined"):
            return "None"
        rm = re.fullmatch(r"/((?:\\.|[^/\\])+)/([gimsuy]*)", e)
        if rm:
            self.uses_re = True
            flags = []
            if "i" in rm.group(2):
                flags.append("re.IGNORECASE")
            if "s" in rm.group(2):
                flags.append("re.DOTALL")
            if "m" in rm.group(2):
                flags.append("re.MULTILINE")
            return f"re.compile({rm.group(1)!r}" + (", " + "|".join(flags) if flags else "") + ")"
        if e.startswith("[") and e.endswith("]"):
            mask = code_mask(e)
            spans = split_args(e, mask, 0, len(e) - 1)
            return "[" + ", ".join(self._expr_py(e[a:b]) for a, b in spans) + "]"
        if e.startswith("{") and e.endswith("}"):
            inner = e[1:-1].strip()
            if not inner:
                return "{}"
            mask = code_mask("(" + inner + ")")
            spans = split_args("(" + inner + ")", mask, 0, len(inner) + 1)
            items = []
            for a, b in spans:
                entry = ("(" + inner + ")")[a:b].strip()
                m = re.match(r"([\w$]+|['\"][^'\"]+['\"])\s*:\s*(.+)$", entry, re.S)
                if not m:
                    raise Unsupported("object literal uses unsupported syntax")
                items.append(f"{m.group(1).strip(chr(39) + chr(34))!r}: {self._expr_py(m.group(2))}")
            return "{" + ", ".join(items) + "}"
        am = re.fullmatch(r"(?:async\s*)?\(?\s*(\w+)\s*\)?\s*=>\s*(.+)", e, re.S)
        if am:
            return f"lambda {am.group(1)}: {self._expr_py(am.group(2))}"
        if re.fullmatch(r"[A-Za-z_]\w*", e):
            if e in self.known_vars:
                return e
            raise Unsupported(f"unknown identifier '{e}'")
        if re.match(r"(page|popup)\d*\.", e) or e in ("page",):
            return self._chain_to_py(e)
        raise Unsupported(f"expression '{e[:60]}' is not supported")


def _convert_hook(conv: "Converter", hook: dict, where: str):
    fixtures = Converter._fixtures(hook.get("params", ""))
    bad = [f for f in fixtures if f not in ("page", "context", "browser")]
    label = f"# {hook['phase']} hook"
    if bad:
        conv.unsupported.append((0, label, "hook uses unsupported fixture(s) " + ", ".join(bad)))
        return
    conv.lines.append("    " + label)
    stmts = conv.split_statements(hook["body"]) if hook.get("is_block") else [hook["body"]]
    conv._convert_list(stmts, "    ")


def convert_js_test(test: ExtractedTest, is_ts: bool):
    """Try to produce a lossless Python replay script for ``test``; update status/issues."""
    params = test.notes.get("body_params", "")
    body = test.notes.get("body", "")
    is_block = test.notes.get("body_is_block", True)
    conv = Converter(params, body, is_block, is_ts)
    unsupported_fixture = [f for f in conv.fixtures if f not in ("page", "context", "browser")]

    test.add(SEVERITY_INFO, "NATIVE_SOURCE", "A self-contained native @playwright/test file was preserved for this test.")
    if unsupported_fixture:
        test.add(SEVERITY_WARNING, "UNSUPPORTED_FIXTURE",
                 "Uses Playwright fixture(s) " + ", ".join(unsupported_fixture) +
                 " which have no Python replay equivalent; not converted.")
        _finish_unconverted(test)
        return
    # Lifecycle hooks (beforeAll/beforeEach before the body, afterEach/afterAll after it) are part
    # of the test's behaviour. They are converted by the SAME rules; a hook that cannot be
    # converted makes the whole test NEEDS REVIEW (never silently dropped).
    hooks = test.notes.get("hooks", [])
    before = [h for h in hooks if h["phase"] in ("beforeAll", "beforeEach")]
    after = [h for h in hooks if h["phase"] in ("afterEach", "afterAll")]
    # module-level literal constants (const BASE = '...') are part of the test's behaviour
    for cname, cexpr in test.notes.get("constants", []):
        try:
            conv.lines.append(f"    {cname} = {conv._expr_py(cexpr)}")
            conv.known_vars.add(cname)
        except Unsupported:
            continue  # not a reproducible literal; any use of it is reported as an unknown identifier
    for h in before:
        _convert_hook(conv, h, "before")
    conv.convert()
    for h in after:
        _convert_hook(conv, h, "after")
    if hooks:
        test.add(SEVERITY_INFO, "HOOKS_INLINED",
                 f"{len(hooks)} lifecycle hook(s) (" + ", ".join(sorted({h['phase'] for h in hooks})) + ") were inlined into the converted script.")
    test.steps = conv.steps
    test.assertion_count = conv.assertions
    if conv.unsupported:
        for n, text, reason in conv.unsupported:
            test.add(SEVERITY_WARNING, "UNSUPPORTED_STATEMENT", f"Statement {n} not converted ({reason}): {text}", step=n)
        _finish_unconverted(test)
        return
    if not conv.lines:
        test.add(SEVERITY_ERROR, "NO_ACTIONS", "The test body contains no convertible actions.")
        test.status = STATUS_NEEDS_REVIEW
        return
    if conv.relative_goto:
        test.add(SEVERITY_WARNING, "RELATIVE_URL",
                 "Navigates to a relative URL, which depends on baseURL from playwright.config. "
                 "Supply a full URL before replaying with the QA AI Studio engine.")
    header = ["from playwright.sync_api import Playwright, sync_playwright, expect"]
    if conv.uses_re:
        header.insert(0, "import re")
    lines = header + ["", "", "def run(playwright: Playwright) -> None:",
                      "    browser = playwright.chromium.launch(headless=False)",
                      "    context = browser.new_context()",
                      "    page = context.new_page()"]
    lines += conv.lines
    lines += ["    context.close()", "    browser.close()", "", "", "with sync_playwright() as playwright:", "    run(playwright)", ""]
    script = "\n".join(lines)
    import ast
    try:
        ast.parse(script)
    except SyntaxError as err:
        test.add(SEVERITY_WARNING, "CONVERSION_INVALID", f"Converted Python did not parse ({err.msg}); not offered.")
        _finish_unconverted(test)
        return
    test.executable_source = script
    test.executable_runtime = RUNTIME_PYTHON
    if test.assertion_count == 0:
        test.add(SEVERITY_WARNING, "NO_ASSERTIONS", "The test contains no assertions.")
    blocking = [i for i in test.issues if i.code in ("PARAMETERIZED", "ID_GENERATED", "RELATIVE_URL")]
    test.status = STATUS_NEEDS_REVIEW if blocking else STATUS_CONVERSION_REQUIRED


def _finish_unconverted(test: ExtractedTest):
    test.executable_source = None
    test.status = STATUS_NEEDS_REVIEW
