"""Python Playwright extraction, validation and shape normalisation.

Supported source shapes:
  * codegen / QA Studio shape:  def run(playwright): ... + with sync_playwright()
  * pytest-playwright shape:    def test_x(page[, context, browser]): ...
  * several test functions in one file, optionally inside ``class Test*``
  * a flat module-level ``with sync_playwright() as p:`` script

Each test is emitted as its OWN canonical ``def run(playwright)`` script. A
shape change is performed only when it is lossless; otherwise the test is
NEEDS REVIEW and its original text is preserved untouched.
"""
from __future__ import annotations

import ast
import re
import textwrap

from Core.script_import.models import (
    ExtractedTest, FORMAT_PYTHON, FORMAT_QA_JSON, RUNTIME_PYTHON,
    STATUS_VALID, STATUS_CONVERSION_REQUIRED, STATUS_NEEDS_REVIEW, STATUS_INVALID,
    SEVERITY_ERROR, SEVERITY_WARNING, SEVERITY_INFO, find_test_id,
)

ALLOWED_IMPORT_ROOTS = {
    "playwright", "re", "os", "time", "json", "datetime", "pathlib", "typing",
    "random", "string", "math", "collections", "itertools", "functools",
    "pytest", "unittest", "__future__", "decimal", "uuid", "base64", "urllib.parse",
}
RISKY_IMPORT_ROOTS = {"subprocess", "socket", "shutil", "ctypes", "multiprocessing", "requests", "urllib", "http", "ftplib", "smtplib", "paramiko", "pickle"}
RISKY_CALLS = {"eval", "exec", "compile", "__import__", "system", "popen", "Popen", "rmtree", "remove", "unlink"}
PYTEST_FIXTURES_SUPPORTED = {"page", "context", "browser", "self"}

SUPPORTED_PAGE_ACTIONS = {
    "goto", "click", "dblclick", "fill", "press", "check", "uncheck", "select_option",
    "set_input_files", "hover", "type", "clear", "focus", "tap", "drag_to", "blur",
    "wait_for", "wait_for_timeout", "wait_for_url", "wait_for_load_state",
    "wait_for_selector", "wait_for_function", "evaluate", "go_back", "go_forward",
    "reload", "set_viewport_size", "once", "on", "expect_popup", "expect_navigation",
    "expect_download", "expect_file_chooser", "accept", "dismiss", "scroll_into_view_if_needed",
    "screenshot", "bring_to_front", "close", "press_sequentially", "dispatch_event",
    "set_checked", "text_content", "inner_text", "input_value", "is_visible",
    "is_checked", "is_enabled", "get_attribute", "down", "up", "wheel", "move",
}
LOCATOR_BUILDERS = {
    "locator", "frame_locator", "get_by_role", "get_by_text", "get_by_label",
    "get_by_placeholder", "get_by_test_id", "get_by_alt_text", "get_by_title",
    "nth", "first", "last", "filter", "and_", "or_",
}
ASSERT_MARKERS = ("expect(", "assert ", "assert(")


def _segment(src, node):
    return ast.get_source_segment(src, node) or ""


def _calls_in(node):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            yield sub


def _describe_call(call) -> tuple[str, str]:
    """(action, target-text) for a call chain such as page.get_by_role(...).click()."""
    func = call.func
    if isinstance(func, ast.Attribute):
        action = func.attr
        try:
            target = ast.unparse(func.value)
        except Exception:  # pragma: no cover - defensive
            target = ""
        return action, target
    if isinstance(func, ast.Name):
        return func.id, ""
    return "call", ""


def analyse_body(body_stmts, src, test: ExtractedTest, step_offset=0):
    """Populate steps/assertions/issues from the statements of a test body."""
    steps = []
    assertion_count = 0
    unsupported = []
    for stmt in body_stmts:
        text = _segment(src, stmt).strip()
        first = text.splitlines()[0] if text else ""
        step_no = step_offset + len(steps) + 1
        if isinstance(stmt, ast.Assert) or text.startswith("expect("):
            assertion_count += 1
            steps.append({"step": step_no, "action": "assert", "target": "", "description": first[:140]})
            continue
        calls = list(_calls_in(stmt))
        if not calls:
            if isinstance(stmt, (ast.Assign, ast.AnnAssign, ast.Pass, ast.Import, ast.ImportFrom)):
                continue
            steps.append({"step": step_no, "action": type(stmt).__name__.lower(), "target": "", "description": first[:140]})
            continue
        for call in calls:
            action, _ = _describe_call(call)
            if action in ("expect",):
                assertion_count += 1
        outer = stmt.value if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call) else None
        if outer is not None:
            action, target = _describe_call(outer)
            steps.append({"step": step_no, "action": action, "target": target[:120], "description": first[:140]})
            if action not in SUPPORTED_PAGE_ACTIONS and action not in LOCATOR_BUILDERS and not action.startswith("to_") \
                    and action not in ("expect", "print", "sleep", "append"):
                unsupported.append((step_no, action))
        else:
            steps.append({"step": step_no, "action": type(stmt).__name__.lower(), "target": "", "description": first[:140]})
    test.steps = steps
    test.assertion_count = assertion_count
    return unsupported


def check_dependencies(tree, test: ExtractedTest, src: str):
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module)
    test.requires = sorted(roots)
    for module in sorted(roots):
        root = module.split(".")[0]
        if module in ALLOWED_IMPORT_ROOTS or root in ALLOWED_IMPORT_ROOTS:
            continue
        if root in RISKY_IMPORT_ROOTS:
            test.add(SEVERITY_WARNING, "UNTRUSTED_IMPORT",
                     f"Imports '{module}', which can reach outside the browser (network, processes, filesystem). "
                     "Review the script before executing it.")
        else:
            test.add(SEVERITY_WARNING, "UNKNOWN_DEPENDENCY",
                     f"Imports '{module}', which is not part of the supported Playwright runtime.")
    for call in _calls_in(tree):
        name = call.func.id if isinstance(call.func, ast.Name) else call.func.attr if isinstance(call.func, ast.Attribute) else ""
        if name in RISKY_CALLS:
            test.add(SEVERITY_WARNING, "RISKY_CALL", f"Calls '{name}()', which is not a browser action. Review before executing.")


def check_secrets_and_variables(test: ExtractedTest, text: str):
    if re.search(r"(password|passwd|pwd|secret|token|otp)[^\n]{0,60}\.(fill|type)\(\s*['\"][^'\"]+['\"]", text, re.IGNORECASE):
        test.add(SEVERITY_WARNING, "LITERAL_SECRET",
                 "A credential-like field is filled with a literal value. It is masked in previews/logs; "
                 "move it to a runtime variable before sharing this script.")
    if "os.environ" in text or "getenv(" in text:
        test.add(SEVERITY_INFO, "ENV_VARIABLE", "Script reads environment variables; they must exist at execution time.")


def _has_main_block(tree) -> bool:
    for node in tree.body:
        if isinstance(node, ast.With):
            for item in node.items:
                if "sync_playwright" in ast.unparse(item.context_expr):
                    return True
    return False


def _top_level_helpers(tree, src, skip):
    chunks = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        if node in skip or isinstance(node, ast.With):
            continue
        if isinstance(node, ast.If) and "__name__" in ast.unparse(node.test):
            continue
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            # other (non-test) helpers are carried across
            if node.name.startswith("test") or node.name == "run" or node.name.startswith("run_"):
                continue
        chunks.append(_segment(src, node))
    return chunks


def _imports(tree, src):
    return [_segment(src, n) for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))]


def _canonical_imports(imports):
    lines = list(imports)
    joined = "\n".join(lines)
    if "sync_playwright" not in joined:
        lines.insert(0, "from playwright.sync_api import Playwright, sync_playwright, expect")
    else:
        if not re.search(r"\bPlaywright\b", joined):
            lines.append("from playwright.sync_api import Playwright")
    return lines


def _run_wrapper(body_src: str, fixtures: set) -> str:
    """Wrap a pytest-style body in run(playwright), preserving statement text."""
    body = textwrap.dedent(body_src).rstrip() or "pass"
    indent = "    "
    lines = ["def run(playwright: Playwright) -> None:",
             f"{indent}browser = playwright.chromium.launch(headless=False)",
             f"{indent}context = browser.new_context()",
             f"{indent}page = context.new_page()"]
    lines += [indent + ln if ln.strip() else ln for ln in body.splitlines()]
    lines += [f"{indent}context.close()", f"{indent}browser.close()"]
    return "\n".join(lines)


MAIN_BLOCK = "\n\nwith sync_playwright() as playwright:\n    run(playwright)\n"


def _function_body_source(src, func):
    lines = src.splitlines()
    start = func.body[0].lineno - 1
    # include a leading docstring-free body from the first statement; keep decorators out
    end = func.end_lineno
    body = "\n".join(lines[start:end])
    # drop a docstring statement
    first = func.body[0]
    if isinstance(first, ast.Expr) and isinstance(getattr(first, "value", None), ast.Constant) and isinstance(first.value.value, str):
        body = "\n".join(lines[func.body[1].lineno - 1:end]) if len(func.body) > 1 else "pass"
    return body


def _candidate_functions(tree):
    found = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("test") or node.name == "run" or node.name.startswith("run_"):
                found.append((node, ""))
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for child in node.body:
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) and child.name.startswith("test"):
                    found.append((child, node.name))
    return found


def _title_from(name: str) -> str:
    cleaned = re.sub(r"^(test|run)[_-]?", "", name, flags=re.IGNORECASE)
    return re.sub(r"[_-]+", " ", cleaned).strip() or name


def extract_python(source: str, filename: str, source_format=FORMAT_PYTHON) -> list[ExtractedTest]:
    stem = re.sub(r"\.[^.]+$", "", filename or "script")
    try:
        tree = ast.parse(source)
    except SyntaxError as err:
        bad = ExtractedTest(test_id=find_test_id(filename) or stem, name=stem, source_filename=filename,
                            source_format=source_format, original_source=source, status=STATUS_INVALID)
        bad.add(SEVERITY_ERROR, "SYNTAX_ERROR", f"Python syntax error at line {err.lineno}: {err.msg}")
        return [bad]

    imports = _imports(tree, source)
    candidates = _candidate_functions(tree)
    results: list[ExtractedTest] = []
    has_main = _has_main_block(tree)

    if not candidates:
        # flat module-level "with sync_playwright() as playwright:" script
        flat = next((n for n in tree.body if isinstance(n, ast.With)
                     and any("sync_playwright" in ast.unparse(i.context_expr) for i in n.items)), None)
        t = ExtractedTest(test_id=find_test_id(filename, source[:400]) or stem, name=stem, source_filename=filename,
                          source_format=source_format, original_source=source, index_in_file=0)
        t.id_generated = find_test_id(filename, source[:400]) is None
        if flat is None:
            t.status = STATUS_INVALID
            t.add(SEVERITY_ERROR, "NO_TEST_FOUND",
                  "No test function (test_*, run) or sync_playwright() block was found in this file.")
            return [t]
        var = None
        for item in flat.items:
            if item.optional_vars is not None and isinstance(item.optional_vars, ast.Name):
                var = item.optional_vars.id
        if var is None:
            t.status = STATUS_NEEDS_REVIEW
            t.add(SEVERITY_WARNING, "UNRECOGNISED_SHAPE", "sync_playwright() block has no named playwright variable.")
            return [t]
        inner = "\n".join(_segment(source, s) for s in flat.body)
        body_lines = [ln for ln in textwrap.dedent(inner).splitlines()]
        helper_src = "\n".join(_top_level_helpers(tree, source, set()))
        canon = "\n".join(_canonical_imports(imports)) + "\n\n" + (helper_src + "\n\n" if helper_src else "")
        canon += f"def run({var}: Playwright) -> None:\n" + "\n".join("    " + ln if ln.strip() else ln for ln in body_lines)
        canon += MAIN_BLOCK.replace("run(playwright)", f"run(playwright)")
        t.executable_source = canon
        t.executable_runtime = RUNTIME_PYTHON
        try:
            ast.parse(canon)
        except SyntaxError as err:  # pragma: no cover - defensive
            t.executable_source = None
            t.add(SEVERITY_ERROR, "CONVERSION_FAILED", f"Normalised script did not parse: {err.msg}")
            t.status = STATUS_NEEDS_REVIEW
            return [t]
        unsupported = analyse_body(flat.body, source, t)
        _finalise_python(t, tree, source, unsupported, shape_changed=True)
        return [t]

    for index, (func, cls) in enumerate(candidates):
        original = _segment(source, func)
        # include decorators in the preserved original
        if func.decorator_list:
            first_line = min(d.lineno for d in func.decorator_list)
            original = "\n".join(source.splitlines()[first_line - 1:func.end_lineno])
        doc = ast.get_docstring(func) or ""
        found_id = find_test_id(func.name, doc, filename if len(candidates) == 1 else "")
        # a `# Test Case ID: X` comment on the lines just above the function
        if not found_id:
            above = "\n".join(source.splitlines()[max(0, func.lineno - 4):func.lineno - 1])
            found_id = find_test_id(above)
        test = ExtractedTest(
            test_id=found_id or f"{stem}_{index + 1:02d}", name=_title_from(func.name),
            source_filename=filename, source_format=source_format, original_source=original,
            index_in_file=index, group=cls, id_generated=found_id is None,
        )
        if isinstance(func, ast.AsyncFunctionDef):
            test.status = STATUS_NEEDS_REVIEW
            test.add(SEVERITY_WARNING, "ASYNC_API",
                     "Uses the async Playwright API, which the QA AI Studio replay engine does not run. "
                     "Original preserved; convert to the sync API to make it executable.")
            analyse_body(func.body, source, test)
            check_dependencies(tree, test, source)
            check_secrets_and_variables(test, original)
            results.append(test)
            continue
        params = [a.arg for a in func.args.args]
        helper_src = "\n".join(_top_level_helpers(tree, source, set()))
        header = "\n".join(_canonical_imports(imports))
        if params and params[0] == "playwright" and not cls:
            rename = func.name != "run"
            fn_src = original
            if rename:
                fn_src = re.sub(r"def\s+" + re.escape(func.name) + r"\b", "def run", fn_src, count=1)
            canon = header + "\n\n" + (helper_src + "\n\n" if helper_src else "") + fn_src + MAIN_BLOCK
            test.executable_source, test.executable_runtime = canon, RUNTIME_PYTHON
            unsupported = analyse_body(func.body, source, test)
            _finalise_python(test, tree, source, unsupported, shape_changed=rename or not has_main or len(candidates) > 1)
        else:
            extra = [p for p in params if p not in PYTEST_FIXTURES_SUPPORTED]
            if extra:
                test.status = STATUS_NEEDS_REVIEW
                test.add(SEVERITY_WARNING, "UNSUPPORTED_FIXTURE",
                         "Test requires pytest fixture(s) " + ", ".join(extra) +
                         " which cannot be provided by the replay engine. Original preserved.")
                analyse_body(func.body, source, test)
                check_dependencies(tree, test, source)
                check_secrets_and_variables(test, original)
                results.append(test)
                continue
            body_src = _function_body_source(source, func)
            canon = header + "\n\n" + (helper_src + "\n\n" if helper_src else "") + _run_wrapper(body_src, set(params)) + MAIN_BLOCK
            try:
                ast.parse(canon)
                test.executable_source, test.executable_runtime = canon, RUNTIME_PYTHON
            except SyntaxError as err:
                test.add(SEVERITY_ERROR, "CONVERSION_FAILED", f"Normalised script did not parse: {err.msg}")
            unsupported = analyse_body(func.body, source, test)
            _finalise_python(test, tree, source, unsupported, shape_changed=True)
        results.append(test)
    return results


def _finalise_python(test: ExtractedTest, tree, src, unsupported, shape_changed: bool):
    check_dependencies(tree, test, src)
    check_secrets_and_variables(test, test.original_source)
    test.requires = sorted(set(test.requires) | {"python>=3.9", "playwright"})
    if unsupported:
        names = sorted({a for _, a in unsupported})
        test.add(SEVERITY_WARNING, "UNSUPPORTED_ACTION",
                 "Actions not recognised by the replay engine: " + ", ".join(names))
    if test.assertion_count == 0:
        test.add(SEVERITY_WARNING, "NO_ASSERTIONS",
                 "The script contains no assertions; completing without an error would not prove the scenario passed.")
    if test.executable_source is None:
        test.status = STATUS_NEEDS_REVIEW if not test.errors else STATUS_INVALID
        return
    risky = [i for i in test.issues if i.code in ("UNTRUSTED_IMPORT", "RISKY_CALL", "UNKNOWN_DEPENDENCY", "UNSUPPORTED_ACTION")]
    if risky or test.id_generated:
        if test.id_generated:
            test.add(SEVERITY_WARNING, "ID_GENERATED",
                     "No Test Case ID was found in the script; a temporary ID was generated. Map it to a test case before saving.")
        test.status = STATUS_NEEDS_REVIEW
    elif shape_changed:
        test.status = STATUS_CONVERSION_REQUIRED
    else:
        test.status = STATUS_VALID


def extract_qa_json(rows: list, filename: str) -> list[ExtractedTest]:
    """QA AI Studio JSON rows: [{'Test Case ID': ..., 'Script': <python>}]."""
    results = []
    for index, row in enumerate(rows):
        norm = {re.sub(r"[^a-z0-9]", "", str(k).lower()): v for k, v in row.items()}
        tcid = str(next((norm[k] for k in ("testcaseid", "tcid", "testcase", "tcnumber", "tc") if k in norm), "") or "").strip()
        script = str(next((norm[k] for k in ("script", "playwrightscript") if k in norm), "") or "")
        t_name = str(norm.get("name") or norm.get("title") or tcid or f"row {index + 1}")
        if not script.strip():
            t = ExtractedTest(test_id=tcid or f"row_{index + 1}", name=t_name, source_filename=filename,
                              source_format=FORMAT_QA_JSON, original_source="", index_in_file=index,
                              id_generated=not tcid)
            t.status = STATUS_NEEDS_REVIEW
            t.add(SEVERITY_WARNING, "NO_SCRIPT", "Row has a Test Case ID but no Script text; it would be a mapping only.")
            results.append(t)
            continue
        extracted = extract_python(script, filename, FORMAT_QA_JSON)
        # a QA JSON row is ONE test: carry the row's ID/name onto it
        if len(extracted) != 1:
            joined = ExtractedTest(test_id=tcid or f"row_{index + 1}", name=t_name, source_filename=filename,
                                   source_format=FORMAT_QA_JSON, original_source=script, index_in_file=index,
                                   id_generated=not tcid)
            joined.status = STATUS_NEEDS_REVIEW
            joined.add(SEVERITY_ERROR if not extracted else SEVERITY_WARNING, "MULTIPLE_TESTS_IN_ROW",
                       f"This row's script contains {len(extracted)} test functions. Split them into separate rows "
                       "(one Test Case ID per script); tests are never combined.")
            results.append(joined)
            continue
        t = extracted[0]
        t.test_id, t.id_generated, t.index_in_file = tcid or t.test_id, not tcid, index
        if norm.get("name") or norm.get("title"):
            t.name = t_name
        if t.status == STATUS_NEEDS_REVIEW and t.notes.get("only_id_generated"):
            pass
        if tcid and any(i.code == "ID_GENERATED" for i in t.issues):
            t.issues = [i for i in t.issues if i.code != "ID_GENERATED"]
            if t.executable_source and t.status == STATUS_NEEDS_REVIEW and not any(
                    i.code in ("UNTRUSTED_IMPORT", "RISKY_CALL", "UNKNOWN_DEPENDENCY", "UNSUPPORTED_ACTION") for i in t.issues):
                t.status = STATUS_VALID if "def run" in script and "sync_playwright()" in script else STATUS_CONVERSION_REQUIRED
        results.append(t)
    return results
