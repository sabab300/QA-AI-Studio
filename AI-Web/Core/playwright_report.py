# Create: AI-Web/Core/playwright_report.py

"""
QA AI Studio
Unified Playwright execution reporting

Task 1 / Phase 8.

Builds ONE report shape for an individual run and for a batch of runs, from
what the application already persists in `automation_runs`
(status/outcome/return_code, runtime_activity step events, stdout/stderr).
It never re-executes anything and never invents results:

* A run is only reported as PASSED when the persisted evidence supports it.
  A "Passed" status whose step events show recorded steps without a pass
  event, or a non-zero return code, is reported as NOT VERIFIED -- never as
  a pass ("no exception" is not a pass).
* Every string that leaves this module is sanitised: credentials, tokens,
  cookies, Authorization values and the values typed into password/secret
  style fields are masked.
* Failures are classified from the persisted error text.

Pure functions over plain dicts: no database access, no browser.
"""

import csv
import html
import io
import json
import re
from collections import Counter
from datetime import datetime

REPORT_VERSION = 1
MASK = "******"
MAX_LOG_CHARS = 6000
MAX_FIELD_CHARS = 1500

_SENSITIVE_WORDS = (
    "password|passwd|pwd|passcode|secret|token|api[-_ ]?key|apikey|authoriz|"
    "cookie|session[-_ ]?id|bearer|credential|otp|cvv|cvc|pin\\b"
)
_SENSITIVE_KEY = re.compile(_SENSITIVE_WORDS, re.IGNORECASE)

_KEY_VALUE = re.compile(
    r"(?P<key>\b[\w.-]*(?:password|passwd|pwd|passcode|secret|token|api[-_]?key|apikey|"
    r"authorization|cookie|session[-_]?id|credential)[\w.-]*\b)(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<value>[^\s\"',;&]+)(?P=quote)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"\b(Bearer|Basic)\s+[A-Za-z0-9\-._~+/]+=*", re.IGNORECASE)
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*")
_TYPED_SECRET = re.compile(
    r"(?P<head>(?:" + _SENSITIVE_WORDS + r")[^\n]*?\.(?:fill|type|press_sequentially)\(\s*)"
    r"(?P<q>[\"'])(?:(?!(?P=q)).)*(?P=q)",
    re.IGNORECASE,
)
_URL_CREDS = re.compile(r"(https?://)([^/\s:@]+):([^/\s@]+)@")
_QUERY_SECRET = re.compile(
    r"([?&](?:[\w.-]*(?:token|password|passwd|secret|api[-_]?key|apikey|session|auth)[\w.-]*)=)[^&\s\"']+",
    re.IGNORECASE,
)


def sanitize_text(text, limit=MAX_FIELD_CHARS):
    """Mask credentials in free text; truncate to `limit` characters."""

    if text is None:
        return ""
    text = str(text)
    text = _TYPED_SECRET.sub(lambda m: f"{m.group('head')}{m.group('q')}{MASK}{m.group('q')}", text)
    text = _URL_CREDS.sub(lambda m: f"{m.group(1)}{MASK}:{MASK}@", text)
    text = _QUERY_SECRET.sub(lambda m: f"{m.group(1)}{MASK}", text)
    text = _BEARER.sub(lambda m: f"{m.group(1)} {MASK}", text)
    text = _JWT.sub(MASK, text)
    text = _KEY_VALUE.sub(
        lambda m: f"{m.group('key')}{m.group('sep')}{m.group('quote')}{MASK}{m.group('quote')}", text
    )
    if limit and len(text) > limit:
        text = text[:limit] + f"... [truncated {len(text) - limit} chars]"
    return text


def _looks_sensitive(mapping):
    """True when an event/detail dict describes a credential-style field."""

    if not isinstance(mapping, dict):
        return False
    if mapping.get("sensitive") in (True, "True", "true", 1, "1"):
        return True
    probe = " ".join(
        str(mapping.get(k) or "") for k in ("locator", "description", "field_name", "label", "primary_locator")
    )
    return bool(_SENSITIVE_KEY.search(probe))


def sanitize_value(value, _sensitive=False):
    """Recursively sanitise a JSON-like structure."""

    if isinstance(value, dict):
        sensitive = _sensitive or _looks_sensitive(value)
        cleaned = {}
        for key, item in value.items():
            if _SENSITIVE_KEY.search(str(key)) and not isinstance(item, (dict, list)):
                cleaned[key] = MASK if item not in (None, "") else item
            elif sensitive and key in ("value", "expected_value", "typed_value"):
                cleaned[key] = MASK if item not in (None, "") else item
            else:
                cleaned[key] = sanitize_value(item, sensitive)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [sanitize_value(item, _sensitive) for item in value]
    if isinstance(value, str):
        return sanitize_text(value)
    return value


# ---------------------------------------------------------------- classification

_FAILURE_RULES = (
    ("CANCELLED", "Cancelled", re.compile(r"cancelled|canceled|stopped by user", re.I),
     "The run was stopped by the operator."),
    ("LOCATOR_AMBIGUOUS", "Ambiguous locator", re.compile(r"strict mode violation|count=[2-9]\d*|ambiguous|resolved to \d+ elements", re.I),
     "The locator matched more than one element. Use a more specific locator or exact matching."),
    ("LOCATOR_NOT_FOUND", "Element not found", re.compile(r"no saved locator candidate|count=0|waiting for locator|element not found|not found|no node found", re.I),
     "The element could not be found. The page may have changed; review the locator alternatives or re-record the step."),
    ("NOT_ACTIONABLE", "Element not actionable", re.compile(r"not actionable|not visible|not enabled|not editable|intercepts pointer|element is outside|disabled", re.I),
     "The element was found but could not be acted on (hidden, disabled or covered)."),
    ("ASSERTION_FAILED", "Assertion failed", re.compile(r"assertionerror|expect\(|to_have_|to_be_|expected .* to|locator expected|assert ", re.I),
     "An expected condition was not met. This is a functional failure candidate, not a script problem."),
    ("TIMEOUT", "Timeout", re.compile(r"timeout|timed out", re.I),
     "An action or wait exceeded its time limit. The application may be slow or the target never appeared."),
    ("NAVIGATION", "Navigation failure", re.compile(r"err_[a-z_]+|net::|navigation|page\.goto|name not resolved|connection refused|ssl", re.I),
     "The browser could not load the page. Check the URL, network and environment availability."),
    ("BROWSER_ENVIRONMENT", "Browser or environment", re.compile(r"browser has been closed|target closed|executable doesn't exist|playwright is not installed|xserver|headed browser|no module named", re.I),
     "The browser or runtime could not be started or was closed unexpectedly."),
    ("PLACEHOLDER", "Non-replayable step", re.compile(r"non_replayable|runtime placeholder", re.I),
     "The stored script contains a runtime-only placeholder and must be re-recorded or repaired."),
    ("SCRIPT_ERROR", "Script error", re.compile(r"syntaxerror|nameerror|typeerror|attributeerror|indentationerror|valueerror", re.I),
     "The script itself is invalid; fix or re-import it."),
)


def classify_failure(*texts):
    """Return {category,label,hint} for the first matching failure rule."""

    blob = " ".join(str(t) for t in texts if t)
    if not blob.strip():
        return {"category": "UNKNOWN", "label": "Unknown", "hint": "No diagnostic text was recorded for this failure."}
    for category, label, pattern, hint in _FAILURE_RULES:
        if pattern.search(blob):
            return {"category": category, "label": label, "hint": hint}
    return {"category": "UNKNOWN", "label": "Unclassified", "hint": "The failure did not match a known category; see the log excerpt."}


# ---------------------------------------------------------------- run report

def _event_parts(item):
    """Normalise one runtime_activity entry to (type, payload)."""

    if not isinstance(item, dict):
        return "", {}
    payload = item.get("event") if isinstance(item.get("event"), dict) else {}
    kind = item.get("type") or payload.get("event") or ""
    return str(kind), payload


def _step_record(number):
    return {"step": number, "status": "not_executed", "action": "", "description": "", "locator": "",
            "attempts": 0, "repaired": False, "repair_source": "", "error": "", "failure": None}


def _assemble_steps(activity):
    steps = {}
    total = 0
    repairs = []
    waits = 0
    for item in activity or []:
        kind, payload = _event_parts(item)
        try:
            number = int(payload.get("step") or 0)
        except (TypeError, ValueError):
            number = 0
        total = max(total, int(payload.get("total_steps") or 0) if str(payload.get("total_steps") or "").isdigit() else 0)
        if not number:
            continue
        record = steps.setdefault(number, _step_record(number))
        details = payload.get("details") if isinstance(payload.get("details"), dict) else {}
        record["action"] = record["action"] or str(details.get("action") or payload.get("code") or "")
        record["description"] = record["description"] or str(details.get("description") or "")
        record["locator"] = record["locator"] or str(details.get("locator") or payload.get("locator") or "")
        if payload.get("attempt"):
            try:
                record["attempts"] = max(record["attempts"], int(payload["attempt"]))
            except (TypeError, ValueError):
                pass
        if kind == "step_passed":
            record["status"] = "repaired" if record["repaired"] else "passed"
        elif kind in ("step_failed", "step_gave_up", "step_failure_detected"):
            if record["status"] != "passed":
                record["status"] = "failed"
            record["error"] = str(payload.get("error") or payload.get("reason") or record["error"] or "")
        elif kind in ("step_repaired", "fallback_succeeded"):
            record["repaired"] = True
            record["repair_source"] = str(payload.get("source") or payload.get("repair_source") or payload.get("selected_source") or "")
            if record["status"] == "passed":
                record["status"] = "repaired"
            repairs.append({"step": number, "source": record["repair_source"] or "unknown",
                            "persisted": payload.get("persisted")})
        elif kind == "application_readiness_wait":
            waits += 1
    return steps, total, repairs, waits


def build_run_report(run):
    """Build the per-test report from one automation_runs row (dict)."""

    run = run or {}
    activity = run.get("runtime_activity")
    if activity is None:
        try:
            activity = json.loads(run.get("runtime_activity_json") or "[]")
        except (TypeError, ValueError):
            activity = []

    steps, total, repairs, waits = _assemble_steps(activity)
    if steps and not total:
        total = max(steps)
    for number in range(1, total + 1):
        steps.setdefault(number, _step_record(number))
    ordered = [steps[k] for k in sorted(steps)]

    status = str(run.get("status") or "")
    outcome = str(run.get("outcome") or "")
    return_code = run.get("return_code")
    stderr = run.get("stderr") or ""
    error_message = run.get("error_message") or ""

    passed = sum(1 for s in ordered if s["status"] in ("passed", "repaired"))
    failed = sum(1 for s in ordered if s["status"] == "failed")
    not_executed = sum(1 for s in ordered if s["status"] == "not_executed")
    repaired = sum(1 for s in ordered if s["status"] == "repaired")

    warnings = []
    evidence = "steps" if ordered else "none"
    verdict_map = {"Passed": "PASSED", "Failed": "FAILED", "Error": "ERROR",
                   "Cancelled": "CANCELLED", "Stopped": "STOPPED"}
    verdict = verdict_map.get(status)
    if verdict is None:
        verdict = "NOT COMPLETED" if status in ("Queued", "Running", "") else status.upper()

    if str(outcome).lower() == "not verified":
        verdict = "NOT VERIFIED"
        warnings.append("The script exited normally but produced no step-level execution evidence; "
                        "a process exit code alone is not accepted as a pass.")

    is_playwright = str(run.get("automation_type") or "").lower() == "playwright"
    if verdict == "PASSED":
        if is_playwright and not ordered:
            verdict = "NOT VERIFIED"
            warnings.append("Recorded as Passed, but no step-level evidence exists for this run; "
                            "a process exit code alone is not accepted as a pass.")
        if ordered and (not_executed or failed):
            verdict = "NOT VERIFIED"
            warnings.append(
                f"Run was recorded as Passed but {not_executed + failed} of {len(ordered)} recorded steps "
                "have no pass event; it is not reported as a pass."
            )
        if return_code not in (None, 0):
            verdict = "NOT VERIFIED"
            warnings.append(f"Run was recorded as Passed but the script exited with code {return_code}.")
        if not ordered and not is_playwright:
            warnings.append("Step-level evidence is unavailable for this run; "
                            "the verdict relies on the script exit status only.")

    failure = None
    if verdict in ("FAILED", "ERROR", "NOT VERIFIED"):
        failed_step = next((s for s in ordered if s["status"] == "failed"), None)
        text = " ".join([failed_step["error"] if failed_step else "", error_message, str(run.get("final_message") or ""),
                         stderr[-1500:]])
        info = classify_failure(text)
        failure = {"step": failed_step["step"] if failed_step else None,
                   "step_action": sanitize_text(failed_step["action"], 400) if failed_step else "",
                   "message": sanitize_text(error_message or (failed_step or {}).get("error") or run.get("final_message") or "", 800),
                   **info}
        if failed_step:
            failed_step["failure"] = info["category"]
    elif verdict == "CANCELLED" or verdict == "STOPPED":
        failure = {"step": None, "step_action": "", "message": sanitize_text(run.get("final_message") or "", 400),
                   **classify_failure("cancelled")}

    log_tail = "\n".join(part for part in (run.get("stdout") or "", stderr) if part)
    log_tail = "\n".join(line for line in log_tail.splitlines()
                         if not line.startswith(("QA_EVENT::", "QA_CHILD_DIAGNOSTIC::")))
    clean_steps = []
    for s in ordered:
        clean_steps.append({
            "step": s["step"], "status": s["status"],
            "action": sanitize_text(s["action"], 400), "description": sanitize_text(s["description"], 300),
            "locator": sanitize_text(s["locator"], 300), "attempts": s["attempts"],
            "repaired": s["repaired"], "repair_source": s["repair_source"],
            "error": sanitize_text(s["error"], 500), "failure_category": s["failure"] or "",
        })

    return {
        "report_version": REPORT_VERSION,
        "kind": "test_run",
        "run_uuid": run.get("run_uuid"),
        "test_case_id": run.get("test_case_id"),
        "tc_number": run.get("tc_number"),
        "test_case_version_id": run.get("test_case_version_id"),
        "automation_type": run.get("automation_type"),
        "domain": run.get("domain"), "module": run.get("module"), "knowledge_name": run.get("knowledge_name"),
        "status": status, "outcome": outcome, "verdict": verdict,
        "started_at": run.get("started_at"), "finished_at": run.get("finished_at"),
        "duration_seconds": run.get("duration_seconds"),
        "executed_by": run.get("executed_by_username"),
        "re_run_of": run.get("re_run_of"),
        "summary": {"total_steps": len(ordered), "passed_steps": passed, "failed_steps": failed,
                    "not_executed_steps": not_executed, "repaired_steps": repaired,
                    "readiness_waits": waits},
        "evidence": {"step_events": evidence, "screenshot": bool(run.get("screenshot_path"))},
        "integrity": {"verified": verdict not in ("NOT VERIFIED", "NOT COMPLETED") and not warnings,
                      "warnings": warnings},
        "failure": failure,
        "repairs": repairs,
        "steps": clean_steps,
        "final_message": sanitize_text(run.get("final_message") or "", 600),
        "log_excerpt": sanitize_text(log_tail[-MAX_LOG_CHARS:], MAX_LOG_CHARS + 100),
    }


# ---------------------------------------------------------------- batch report

def build_batch_report(runs, title="Playwright execution report", generated_by=None, now=None):
    """Build the batch report: per-test records + an accurate summary."""

    reports = [build_run_report(run) for run in (runs or [])]
    verdicts = Counter(r["verdict"] for r in reports)
    categories = Counter((r["failure"] or {}).get("category") for r in reports if r["failure"])
    durations = [float(r["duration_seconds"]) for r in reports if isinstance(r.get("duration_seconds"), (int, float))]
    total = len(reports)
    decided = verdicts["PASSED"] + verdicts["FAILED"] + verdicts["ERROR"] + verdicts["NOT VERIFIED"]
    summary = {
        "total_runs": total,
        "passed": verdicts["PASSED"], "failed": verdicts["FAILED"], "error": verdicts["ERROR"],
        "not_verified": verdicts["NOT VERIFIED"], "cancelled": verdicts["CANCELLED"] + verdicts["STOPPED"],
        "not_completed": verdicts["NOT COMPLETED"],
        "pass_rate_percent": round(100.0 * verdicts["PASSED"] / decided, 1) if decided else None,
        "total_duration_seconds": round(sum(durations), 2),
        "repaired_steps": sum(r["summary"]["repaired_steps"] for r in reports),
        "distinct_test_cases": len({r.get("tc_number") or r.get("test_case_id") for r in reports}),
        "failure_categories": dict(categories),
    }
    return {
        "report_version": REPORT_VERSION, "kind": "batch",
        "title": sanitize_text(title, 200),
        "generated_at": (now or datetime.now()).isoformat(timespec="seconds"),
        "generated_by": generated_by,
        "summary": summary,
        "tests": reports,
    }


# ---------------------------------------------------------------- renderers

CSV_COLUMNS = ("tc_number", "run_uuid", "verdict", "status", "started_at", "finished_at", "duration_seconds",
               "total_steps", "passed_steps", "failed_steps", "not_executed_steps", "repaired_steps",
               "failed_step", "failure_category", "failure_message", "warnings", "executed_by")


def _csv_safe(value):
    """Neutralise spreadsheet formula injection."""

    text = "" if value is None else str(value)
    return "'" + text if text[:1] in ("=", "+", "-", "@") else text


def render_json(report):
    return json.dumps(report, indent=2, ensure_ascii=False)


def render_csv(report):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_COLUMNS)
    for test in report.get("tests", []):
        failure = test.get("failure") or {}
        summary = test.get("summary") or {}
        writer.writerow([_csv_safe(v) for v in (
            test.get("tc_number"), test.get("run_uuid"), test.get("verdict"), test.get("status"),
            test.get("started_at"), test.get("finished_at"), test.get("duration_seconds"),
            summary.get("total_steps"), summary.get("passed_steps"), summary.get("failed_steps"),
            summary.get("not_executed_steps"), summary.get("repaired_steps"),
            failure.get("step"), failure.get("category"), failure.get("message"),
            " | ".join((test.get("integrity") or {}).get("warnings") or []), test.get("executed_by"),
        )])
    return buffer.getvalue()


def _e(value):
    return html.escape("" if value is None else str(value))


_VERDICT_CLASS = {"PASSED": "ok", "FAILED": "bad", "ERROR": "bad", "NOT VERIFIED": "warn",
                  "CANCELLED": "mute", "STOPPED": "mute", "NOT COMPLETED": "mute"}


def render_html(report):
    s = report.get("summary", {})
    rows = []
    details = []
    for index, test in enumerate(report.get("tests", [])):
        failure = test.get("failure") or {}
        summ = test.get("summary") or {}
        cls = _VERDICT_CLASS.get(test.get("verdict"), "mute")
        rows.append(
            f"<tr><td>{_e(test.get('tc_number') or test.get('test_case_id'))}</td>"
            f"<td><span class='pill {cls}'>{_e(test.get('verdict'))}</span></td>"
            f"<td>{_e(summ.get('passed_steps'))}/{_e(summ.get('total_steps'))}</td>"
            f"<td>{_e(summ.get('repaired_steps'))}</td>"
            f"<td>{_e(test.get('duration_seconds'))}</td>"
            f"<td>{_e(failure.get('label'))}</td>"
            f"<td><a href='#t{index}'>details</a></td></tr>"
        )
        step_rows = "".join(
            f"<tr><td>{_e(st['step'])}</td><td>{_e(st['status'])}</td><td class='mono'>{_e(st['action'])}</td>"
            f"<td>{_e(st['error'])}</td></tr>" for st in test.get("steps", [])
        ) or "<tr><td colspan='4'>No step-level events were recorded for this run.</td></tr>"
        warn = "".join(f"<li>{_e(w)}</li>" for w in (test.get("integrity") or {}).get("warnings", []))
        details.append(
            f"<section id='t{index}'><h3>{_e(test.get('tc_number') or test.get('test_case_id'))} "
            f"<span class='pill {cls}'>{_e(test.get('verdict'))}</span></h3>"
            f"<p class='meta'>Run {_e(test.get('run_uuid'))} &middot; {_e(test.get('started_at'))} &middot; "
            f"{_e(test.get('executed_by'))}</p>"
            + (f"<p><b>{_e(failure.get('label'))}:</b> {_e(failure.get('message'))}<br><i>{_e(failure.get('hint'))}</i></p>" if failure else "")
            + (f"<ul class='warn'>{warn}</ul>" if warn else "")
            + f"<table><thead><tr><th>Step</th><th>Status</th><th>Action</th><th>Error</th></tr></thead><tbody>{step_rows}</tbody></table>"
            + (f"<details><summary>Log excerpt</summary><pre>{_e(test.get('log_excerpt'))}</pre></details>" if test.get("log_excerpt") else "")
            + "</section>"
        )
    cats = ", ".join(f"{_e(k)}: {_e(v)}" for k, v in (s.get("failure_categories") or {}).items()) or "none"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(report.get('title'))}</title>
<style>:root{{--bg:#fff;--fg:#14202e;--mut:#5b6b7c;--line:#d8e0e8;--ok:#0b7a4b;--bad:#b3261e;--warn:#9a6700}}
@media (prefers-color-scheme:dark){{:root{{--bg:#0f1720;--fg:#e6edf3;--mut:#9fb0c0;--line:#2a3846;--ok:#4cc38a;--bad:#ff7b72;--warn:#e3b341}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,Segoe UI,sans-serif;margin:0;padding:24px;max-width:1100px;margin-inline:auto}}
h1{{margin:0 0 4px}}.meta{{color:var(--mut)}}table{{border-collapse:collapse;width:100%;margin:10px 0;display:block;overflow-x:auto}}
th,td{{border-bottom:1px solid var(--line);padding:6px 10px;text-align:left;vertical-align:top}}.mono,pre{{font-family:Consolas,monospace;font-size:12px}}
pre{{white-space:pre-wrap;background:rgba(127,127,127,.12);padding:10px;border-radius:6px}}
.pill{{padding:1px 8px;border-radius:10px;border:1px solid currentColor;font-size:12px;font-weight:600}}
.ok{{color:var(--ok)}}.bad{{color:var(--bad)}}.warn{{color:var(--warn)}}.mute{{color:var(--mut)}}
.cards{{display:flex;flex-wrap:wrap;gap:12px;margin:14px 0}}.card{{border:1px solid var(--line);border-radius:8px;padding:8px 14px;min-width:110px}}
.card b{{display:block;font-size:20px}}section{{margin-top:28px}}</style></head><body>
<h1>{_e(report.get('title'))}</h1>
<p class="meta">Generated {_e(report.get('generated_at'))}{(' by ' + _e(report.get('generated_by'))) if report.get('generated_by') else ''}. Credentials are masked in this report.</p>
<div class="cards"><div class="card"><b>{_e(s.get('total_runs'))}</b>runs</div>
<div class="card"><b class="ok">{_e(s.get('passed'))}</b>passed</div>
<div class="card"><b class="bad">{_e((s.get('failed') or 0) + (s.get('error') or 0))}</b>failed / error</div>
<div class="card"><b class="warn">{_e(s.get('not_verified'))}</b>not verified</div>
<div class="card"><b>{_e(s.get('pass_rate_percent'))}</b>pass rate %</div>
<div class="card"><b>{_e(s.get('repaired_steps'))}</b>repaired steps</div></div>
<p class="meta">Failure categories: {cats}. Total duration {_e(s.get('total_duration_seconds'))}s.</p>
<table><thead><tr><th>Test case</th><th>Verdict</th><th>Steps passed</th><th>Repaired</th><th>Duration (s)</th><th>Failure</th><th></th></tr></thead><tbody>{''.join(rows)}</tbody></table>
{''.join(details)}</body></html>"""


RENDERERS = {
    "json": (render_json, "application/json", "json"),
    "csv": (render_csv, "text/csv; charset=utf-8", "csv"),
    "html": (render_html, "text/html; charset=utf-8", "html"),
}


def render(report, fmt):
    """Return (body_text, media_type, extension) or raise ValueError."""

    key = str(fmt or "json").lower()
    if key not in RENDERERS:
        raise ValueError(f"Unsupported report format '{fmt}'. Use json, csv or html.")
    func, media, ext = RENDERERS[key]
    return func(report), media, ext
