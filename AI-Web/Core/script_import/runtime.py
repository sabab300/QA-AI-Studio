"""Runtime probing + isolated native (Node) execution of imported tests."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from Core.script_import.models import mask_secrets


class RuntimeProbe:
    """Detects which runtimes are present. Static checks only -- never a live result."""

    def __init__(self, node_modules_dir: str | None = None):
        self.node = shutil.which("node")
        self.node_modules_dir = node_modules_dir or os.environ.get("QA_NATIVE_NODE_MODULES")
        self._cache = {}

    def native_ready(self) -> tuple[bool, str]:
        if not self.node:
            return False, "Node.js was not found on PATH."
        nm = self._find_playwright_test()
        if not nm:
            return False, "The @playwright/test package was not found (set QA_NATIVE_NODE_MODULES or install it)."
        return True, f"node + @playwright/test at {nm}"

    def _find_playwright_test(self):
        candidates = []
        if self.node_modules_dir:
            candidates.append(Path(self.node_modules_dir))
        candidates += [Path.cwd() / "node_modules"]
        for c in candidates:
            if (c / "@playwright" / "test").exists():
                return str(c)
        return None

    def check_js_syntax(self, source: str, typescript: bool = False) -> tuple[bool, str, bool]:
        """(ok, message, verified). verified=False when no checker could run."""
        if not self.node:
            return True, "Node.js not available; syntax not verified.", False
        suffix = ".mts" if typescript else ".mjs"
        with tempfile.TemporaryDirectory(prefix="qa-syntax-") as tmp:
            path = Path(tmp) / f"check{suffix}"
            path.write_text(source, encoding="utf-8")
            cmd = [self.node, "--check", str(path)]
            if typescript:
                cmd = [self.node, "--experimental-strip-types", "--check", str(path)]
            try:
                done = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            except Exception as err:  # pragma: no cover - defensive
                return True, f"Syntax check could not run ({type(err).__name__}).", False
            if done.returncode == 0:
                return True, "", True
            msg = (done.stderr or done.stdout or "").strip()
            if typescript and re.search(r"(unknown option|bad option|--experimental-strip-types|ERR_UNKNOWN_FILE_EXTENSION|Unexpected token)", msg) and "SyntaxError" not in msg.split("\n")[0:6].__str__():
                return True, "TypeScript syntax could not be verified with this Node version.", False
            first = next((ln for ln in msg.splitlines() if "Error" in ln), msg.splitlines()[0] if msg else "syntax error")
            return False, first[:300], True


class NativeRunResult(dict):
    pass


_CONFIG = """
const { defineConfig } = require('@playwright/test');
module.exports = defineConfig({
  testDir: './tests', timeout: %(timeout)d, retries: 0, workers: 1, fullyParallel: false,
  reporter: [['json', { outputFile: 'report.json' }]],
  use: { headless: true, screenshot: 'only-on-failure', trace: 'off' },
});
"""


def run_native_test(native_source: str, *, typescript: bool, test_title_hint: str = "", timeout_ms: int = 60000,
                    node_modules_dir: str | None = None, base_dir: str | None = None) -> NativeRunResult:
    """Run ONE imported test natively in an isolated temp project. Returns a unified result.

    Isolation: separate working directory, scrubbed environment (no application secrets),
    hard timeout, no write access outside the temp project by the runner itself.
    """
    probe = RuntimeProbe(node_modules_dir)
    ready, why = probe.native_ready()
    started = time.time()
    if not ready:
        return NativeRunResult(engine="node-playwright-test", status="NOT_EXECUTED", passed=False,
                               reason=why, duration_seconds=0.0, steps=[], failure=None)
    work = Path(tempfile.mkdtemp(prefix="qa-native-", dir=base_dir))
    try:
        (work / "tests").mkdir()
        ext = ".spec.ts" if typescript else ".spec.js"
        (work / "tests" / f"imported{ext}").write_text(native_source, encoding="utf-8")
        (work / "playwright.config.js").write_text(_CONFIG % {"timeout": timeout_ms}, encoding="utf-8")
        nm = probe._find_playwright_test()
        link = work / "node_modules"
        try:
            os.symlink(nm, link, target_is_directory=True)
        except (OSError, NotImplementedError):
            shutil.copytree(nm, link)
        env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "SYSTEMROOT", "TEMP", "TMP", "PLAYWRIGHT_BROWSERS_PATH", "LANG", "DISPLAY")}
        env["CI"] = "1"
        cli = Path(nm) / "@playwright" / "test" / "cli.js"
        cmd = [probe.node, str(cli), "test", "--config", "playwright.config.js"]
        if typescript:
            cmd = [probe.node, "--experimental-strip-types"] + cmd[1:] if False else cmd
        try:
            done = subprocess.run(cmd, cwd=work, env=env, capture_output=True, text=True,
                                  timeout=max(30, timeout_ms / 1000 * 2 + 30))
        except subprocess.TimeoutExpired:
            return NativeRunResult(engine="node-playwright-test", status="FAIL", passed=False,
                                   reason="Native run exceeded its time limit and was stopped.",
                                   duration_seconds=round(time.time() - started, 2), steps=[], failure="timeout")
        report = {}
        rp = work / "report.json"
        if rp.exists():
            try:
                report = json.loads(rp.read_text(encoding="utf-8"))
            except ValueError:
                report = {}
        return _summarise(report, done, started)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _walk_specs(suite, acc):
    for spec in suite.get("specs", []) or []:
        acc.append(spec)
    for child in suite.get("suites", []) or []:
        _walk_specs(child, acc)


def _summarise(report: dict, done, started: float) -> NativeRunResult:
    specs = []
    for suite in report.get("suites", []) or []:
        _walk_specs(suite, specs)
    tests = []
    for spec in specs:
        for t in spec.get("tests", []) or []:
            results = t.get("results", []) or []
            last = results[-1] if results else {}
            status = last.get("status") or t.get("status") or "unknown"
            err = ""
            if last.get("error"):
                err = (last["error"].get("message") or "")[:600]
            tests.append({"title": spec.get("title"), "status": status, "expected": t.get("expectedStatus"),
                          "duration_ms": last.get("duration"), "error": mask_secrets(re.sub(r"\x1b\[[0-9;]*m", "", err)),
                          "annotations": t.get("annotations", [])})
    if not tests:
        return NativeRunResult(engine="node-playwright-test", status="NOT_EXECUTED", passed=False,
                               reason="The native runner produced no result (compilation or startup error).",
                               stderr=mask_secrets((done.stderr or done.stdout or "")[-1500:]),
                               duration_seconds=round(time.time() - started, 2), steps=[], failure="no_result")
    skipped = [t for t in tests if t["status"] == "skipped"]
    failed = [t for t in tests if t["status"] not in ("passed", "skipped")]
    if failed:
        overall = "FAIL"
    elif skipped and len(skipped) == len(tests):
        overall = "SKIPPED"
    else:
        overall = "PASS"
    return NativeRunResult(engine="node-playwright-test", status=overall, passed=overall == "PASS",
                           tests=tests, duration_seconds=round(time.time() - started, 2),
                           failure=failed[0]["error"] if failed else None, steps=[], reason=None)
