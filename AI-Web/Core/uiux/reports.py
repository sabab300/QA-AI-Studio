"""Report model and exports (printable HTML, Excel, PDF) for one AI UI/UX Testing run.

Honesty rules baked into the model:
  * the verdict is derived from what actually executed - skipped, blocked or cancelled screens can never read as success;
  * verified findings (measured), AI-generated text, untested areas and unsupported checks are separate, labelled sections;
  * "No differences found" is always qualified with the number of checks and what was not verifiable.
"""
from __future__ import annotations

import html
import io
from datetime import datetime, timezone

from . import ENGINE_VERSION
from . import categories as UC

CLASS_ORDER = ["Confirmed Difference", "Potential Difference", "Needs Investigation", "Not Verifiable"]
SEV_ORDER = ["Critical", "High", "Medium", "Low"]
GROUPS = [("visual", "Visual comparison"), ("css_typography", "CSS and typography"), ("forms_components", "Forms and components"), ("responsive", "Responsive behaviour")]


def _verdict(run: dict, totals: dict, tested: int, planned: int) -> dict:
    status = run["status"]
    if status in ("Blocked", "Failed"):
        return {"label": "Not tested", "tone": "bad", "detail": run["status_detail"] or "The run could not execute."}
    if status in ("Queued", "Running", "Paused"):
        return {"label": "Paused" if status == "Paused" else "In progress", "tone": "info", "detail": "The run has not finished; results are incomplete."}
    if status in ("Cancelled", "Interrupted"):
        return {"label": "Incomplete", "tone": "warn", "detail": f"{run['status_detail']} Only {tested} of {planned} screens were tested."}
    prefix = "" if status == "Completed" else f"Partially tested ({tested} of {planned} screens). "
    if totals.get("confirmed"):
        return {"label": "Differences confirmed", "tone": "bad", "detail": prefix + f"{totals['confirmed']} confirmed difference(s) from the design."}
    if totals.get("potential") or totals.get("needs_investigation"):
        return {"label": "Review required", "tone": "warn", "detail": prefix + "No confirmed differences, but some findings need a human decision."}
    if status == "Partial":
        return {"label": "Incomplete", "tone": "warn", "detail": prefix + "No differences found in the screens that were tested."}
    return {"label": "No differences found in the tested scope", "tone": "good", "detail": "This does not cover areas marked Not Verifiable or not tested."}


def _target_text(origin: str, pages: list) -> str:
    """The origin, with the tested page appended when there is exactly one (http://host:8000/defect/crf); several pages are listed after it."""
    unique = [p for p in dict.fromkeys(pages) if p]
    if not unique:
        return origin
    if len(unique) == 1:
        return origin.rstrip("/") + unique[0]
    return origin + " (pages: " + ", ".join(unique) + ")"


def build_report(repo, registry, run_id: str) -> dict:
    run = repo.get_run(run_id)
    findings = repo.list_findings(run_id, limit=5000)["items"]
    verified = [f for f in findings if f["verifiable"]]
    nv = [f for f in findings if not f["verifiable"]]
    summary = run["summary"]
    units = summary.get("units", [])
    tested = [u for u in units if u.get("status") == "Tested"]
    planned = max(len(units), len(run["config"].get("mapping_ids", [])))
    try:
        asset = registry.get(run["asset_id"])
    except Exception:
        asset = {"file_name": "(removed)", "version": run["version"], "source_type": run["source_type"], "status": "Missing", "frames": []}
    target = None
    try:
        target = repo.get_target(run["config"].get("target_id", ""))
    except Exception:
        pass
    reviews = []
    for f in verified:
        for r in repo.review_history(f["document_version_id"], f["fingerprint"]):
            reviews.append({"finding": f["title"], "defect_id": f["defect_id"], "decision": r["decision"], "comment": r["comment"], "reviewer": r["reviewer"], "reviewed_at": r["reviewed_at"]})
    prev = None
    if run["parent_run_id"] or summary.get("regression"):
        prev = summary.get("regression")
    baseline_cmp = None
    if run["baseline_id"]:
        try:
            baseline_cmp = repo.compare_with_baseline(run["baseline_id"], run_id)
        except Exception:
            baseline_cmp = None
    categories = {}
    for u in tested:
        for cat, row in (u.get("checks", {}).get("by_category") or {}).items():
            agg = categories.setdefault(cat, {"passed": 0, "failed": 0, "not_verifiable": 0, "explained": 0})
            for k in agg:
                agg[k] += row.get(k, 0)
    groups = {
        "visual": [f for f in verified if f["category"] == "visual"],
        "css_typography": [f for f in verified if f["category"] in ("css", "typography")],
        "forms_components": [f for f in verified if f["category"] in ("forms", "buttons", "navigation", "tables", "icons_images", "layout")],
        "responsive": [f for f in verified if f["category"] == "responsive"],
    }
    a11y = [f for f in verified if f["category"] == "accessibility"]
    human = []
    for u in tested:
        for h in (u.get("accessibility") or {}).get("human_review", []):
            key = (h["topic"], h["target"])
            if key not in [(x["topic"], x["target"]) for x in human]:
                human.append({**h, "screen": u["screen"]})
    recommendations = []
    if any(f["classification"] == "Confirmed Difference" for f in verified):
        recommendations.append("Fix the confirmed differences, starting with Critical and High severity, then re-run this configuration to verify the fixes.")
    if any(f["classification"] == "Potential Difference" for f in verified):
        recommendations.append("Review each Potential Difference and record a decision (Confirmed, Not Applicable, Needs Investigation) so it is tracked across builds.")
    if summary.get("mappings_unapproved"):
        recommendations.append("Approve the screen mappings (" + ", ".join(summary["mappings_unapproved"]) + "); until then no finding can be classed as Confirmed.")
    if nv:
        recommendations.append("Provide the missing design information for the Not Verifiable items (see that section) so they can be checked in future runs.")
    if human:
        recommendations.append("Complete the manual accessibility review items; automated checks cannot cover them.")
    if verified and not repo.list_checks(run["document_version_id"]):
        recommendations.append("Save confirmed findings as regression checks so they are re-tested on every build.")
    if not repo.active_baseline(run["document_version_id"], run["asset_id"]) and run["status"] in ("Completed", "Partial"):
        recommendations.append("Approve and activate a baseline from a clean run so later builds can be compared against it.")
    ai = summary.get("ai") or {}
    vtotals = summary.get("totals", {})
    cfg = run["config"]
    ui = bool(cfg.get("ui_categories"))
    sel = list(cfg.get("user_categories") or [])
    cat_rows = [{"key": k, **(summary.get("categories") or {}).get(k, {"label": UC.LABELS.get(k, k), "executed": 0, "passed": 0, "failed": 0, "not_verifiable": 0, "status": "Not run"})} for k in sel] if ui else []
    ck = summary.get("checks", {}) or {}
    executed = ck.get("executed", (ck.get("passed", 0) + ck.get("failed", 0)) if tested else 0)
    skipped = ck.get("skipped_blocked", sum(1 for u in units if u.get("status") != "Tested"))
    outcome = {"Completed": "Completed", "Partial": "Partially completed", "Failed": "Failed", "Blocked": "Blocked - nothing was tested", "Cancelled": "Cancelled", "Interrupted": "Interrupted",
               "Queued": "Queued", "Running": "Running", "Paused": "Paused"}.get(run["status"], run["status"])
    if run["status"] in ("Cancelled", "Interrupted", "Partial"):
        outcome += f" ({len(tested)} of {planned} screens tested)"
    started, ended = run.get("started_at"), run.get("ended_at")
    duration = None
    try:
        if started and ended:
            duration = round((datetime.fromisoformat(str(ended).replace("Z", "+00:00")) - datetime.fromisoformat(str(started).replace("Z", "+00:00"))).total_seconds(), 1)
    except Exception:
        duration = None
    videos = repo.list_evidence(run_id, "video")
    steps = repo.list_steps(run_id, limit=5000)
    pages = [f'{(u.get("page") or {}).get("path", "")}' for u in tested]
    execution = {
        "run_id": run_id, "hierarchy": " > ".join(x for x in (run["domain"], run["module"], run["knowledge_name"]) if x), "design_source": f'{run["source_type"]} / {run["source_file"]}', "design_version": run["version"],
        "target": _target_text(run["app_url"], pages), "environment": run["environment"], "build": run["build_label"] or "not recorded",
        "started_at": started, "ended_at": ended, "duration_s": duration, "browser": "Chromium", "viewports": run["viewports"], "run_by": run["created_by"],
        "categories": [(UC.LABELS.get(k, k) if ui else k) for k in (sel if ui else cfg.get("categories", []))], "status": run["status"], "outcome": outcome,
        "checks": {"executed": executed, "passed": ck.get("passed", 0), "failed": ck.get("failed", 0), "not_verifiable": ck.get("not_verifiable", 0), "skipped_blocked": skipped},
        "findings_by_severity": {sv: vtotals.get(sv.lower(), 0) for sv in SEV_ORDER},
    }
    replay = {"available": bool(steps), "steps": len(steps), "video": bool(videos),
              "note": "Replay shows what was recorded during the run; it never re-runs the application." + ("" if videos else " No screen recording exists for this run; the replay uses timestamped screenshots and steps.")}
    return {
        "execution": execution, "category_results": cat_rows, "replay": replay,
        "title": f"AI UI/UX Testing report - {run['knowledge_name']} {run['version']}", "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "engine_version": ENGINE_VERSION,
        "run": {k: run[k] for k in ("run_id", "status", "status_detail", "kind", "parent_run_id", "created_at", "started_at", "ended_at", "created_by", "build_label", "environment", "browser", "viewports", "ai_status", "ai_note")},
        "hierarchy": {k: run[k] for k in ("domain", "module", "knowledge_name", "document_type", "version", "source_type", "source_file")},
        "verdict": _verdict(run, vtotals, len(tested), planned),
        "executive": {"totals": vtotals, "screens_tested": len(tested), "screens_planned": planned, "checks": summary.get("checks", {}), "regression": summary.get("regression", {})},
        "design": {"asset_id": asset.get("id", run["asset_id"]), "name": asset.get("file_name", ""), "kind": asset.get("asset_kind", ""), "source_type": asset.get("source_type", ""),
                   "version": asset.get("version", run["version"]), "content_hash": asset.get("content_hash", ""), "status": asset.get("status", ""), "last_analysis_at": asset.get("last_analysis_at"),
                   "structural": bool(asset.get("structural_available")), "extraction": asset.get("extraction_status", ""),
                   "frames": [{"name": f["name"], "width": f["width"], "height": f["height"], "viewport_class": f["viewport_class"], "has_image": f["has_image"], "has_structure": f["has_structure"]} for f in asset.get("frames", [])]},
        "application": {"origin": run["app_url"], "environment": run["environment"], "build": run["build_label"], "browser": run["browser"] + " (Chromium)", "target": (target or {}).get("name", ""),
                        "pages": [{"screen": u["screen"], "viewport": u.get("viewport"), "title": (u.get("page") or {}).get("title", ""), "path": (u.get("page") or {}).get("path", ""), "http_status": (u.get("page") or {}).get("http_status")} for u in tested]},
        "scope": {"categories": run["config"].get("categories", []), "tolerances": run["config"].get("tolerances", {}), "screens": [{"screen": u.get("screen"), "viewport": u.get("viewport"), "status": u.get("status"),
                  "mapping_trusted": u.get("mapping_trusted"), "mapping_confidence": u.get("mapping_confidence"), "message": u.get("message", "")} for u in units], "visual": run["config"].get("visual", {})},
        "results": {"by_classification": {c: sum(1 for f in findings if f["classification"] == c) for c in CLASS_ORDER}, "by_severity": {s: sum(1 for f in verified if f["severity"] == s) for s in SEV_ORDER},
                    "by_category": categories, "by_screen": [{"screen": u["screen"], "viewport": u.get("viewport"), "findings": u.get("findings", 0), "checks": u.get("checks", {})} for u in tested]},
        "defects": [f for f in verified if f["category"] != "accessibility"] + a11y,
        "groups": {key: groups[key] for key, _ in GROUPS},
        "visual_screens": [{"screen": u["screen"], "viewport": u.get("viewport"), "visual": u.get("visual"), "evidence": u.get("evidence", {}), "notes": u.get("notes", []), "drift": u.get("drift")} for u in tested],
        "accessibility": {"findings": a11y, "human_review": human},
        "not_verifiable": nv, "reviews": reviews,
        "regression": {"vs_run": summary.get("regression", {}), "vs_baseline": baseline_cmp, "fixed": [x for u in tested for x in u.get("fixed", [])]},
        "untested": summary.get("untested", []), "unsupported": [f for f in nv if f["category"] in ("css", "typography", "visual") and "structure" in f["description"].lower()][:5],
        "recommendations": recommendations,
        "ai": {"status": run["ai_status"], "note": run["ai_note"], "summary": ai.get("summary", ""), "recommendations": ai.get("recommendations", []), "uncertainty": ai.get("uncertainty", []), "model": ai.get("model", {})},
        "security": [{"screen": u["screen"], **(u.get("security") or {})} for u in tested],
    }


# ============================================================================= HTML

CSS = """
:root{--ink:#14202b;--mute:#5a6b7b;--line:#d7dee6;--paper:#ffffff;--soft:#f3f6f9;--good:#17794a;--warn:#9a6700;--bad:#b42318;--info:#175cd3;--accent:#0b5c7a}
*{box-sizing:border-box}body{margin:0;background:#e9eef3;color:var(--ink);font:14px/1.5 "Segoe UI",system-ui,sans-serif}
.page{max-width:1040px;margin:24px auto;background:var(--paper);padding:40px 48px;border:1px solid var(--line)}
h1{font-size:24px;margin:0 0 4px}h2{font-size:17px;margin:30px 0 10px;padding-bottom:6px;border-bottom:2px solid var(--accent);color:var(--accent)}h3{font-size:14px;margin:18px 0 6px}
.meta{color:var(--mute);font-size:12px}.verdict{margin:16px 0;padding:14px 16px;border-left:6px solid var(--accent);background:var(--soft)}
.verdict.good{border-color:var(--good)}.verdict.warn{border-color:var(--warn)}.verdict.bad{border-color:var(--bad)}.verdict b{font-size:16px}
table{width:100%;border-collapse:collapse;margin:8px 0;font-size:13px}th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--soft);font-weight:600}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;margin:10px 0}.kpi{border:1px solid var(--line);padding:10px;text-align:center}.kpi b{display:block;font-size:22px}
.pill{display:inline-block;padding:1px 8px;border-radius:10px;font-size:11px;font-weight:600;border:1px solid}.Critical{color:#fff;background:#7a1c14;border-color:#7a1c14}.High{color:var(--bad);border-color:var(--bad)}.Medium{color:var(--warn);border-color:var(--warn)}.Low{color:var(--mute);border-color:var(--mute)}
.c-Confirmed{color:var(--bad)}.c-Potential{color:var(--warn)}.c-Needs{color:var(--info)}.c-Not{color:var(--mute)}
.ai{background:#f7f4ff;border:1px dashed #7c5cc4;padding:10px 12px;margin:8px 0}.note{background:#fff8e6;border:1px solid #f0d58a;padding:8px 12px;margin:8px 0}
img.ev{max-width:100%;border:1px solid var(--line);margin:6px 0}.small{font-size:12px;color:var(--mute)}
@media print{body{background:#fff}.page{margin:0;border:0;padding:0;max-width:none}h2{break-after:avoid}tr,img{break-inside:avoid}}
"""


def _e(value) -> str:
    return html.escape("" if value is None else str(value))


def _table(headers, rows, cls="") -> str:
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c if isinstance(c, _Raw) else _e(c)}</td>" for c in r) + "</tr>" for r in rows)
    return f'<table class="{cls}"><thead><tr>{head}</tr></thead><tbody>{body or "<tr><td colspan=%d>None.</td></tr>" % len(headers)}</tbody></table>'


class _Raw(str):
    pass


def _data_uri(png: bytes) -> str:
    import base64
    return "data:image/png;base64," + base64.b64encode(png).decode()


def _finding_rows(items):
    return [[f["defect_id"].split("-D")[-1] and f["defect_id"], _Raw(f'<span class="pill {f["severity"]}">{_e(f["severity"])}</span>'),
             _Raw(f'<span class="c-{_e(f["classification"].split()[0])}">{_e(f["classification"])}</span> ({int(f["confidence"] * 100)}%)'),
             f["category"], f["screen"], f["title"], f["expected"], f["actual"], f["review_status"] + (" (reassess)" if f["needs_reassessment"] else "")] for f in items]


FIND_HEAD = ["Defect", "Severity", "Classification", "Category", "Screen", "Title", "Expected", "Actual", "QA review"]


def to_html(r: dict, images: dict | None = None) -> str:
    images = images or {}
    v, h, run = r["verdict"], r["hierarchy"], r["run"]
    out = [f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(r["title"])}</title><style>{CSS}</style></head><body><div class="page">']
    out.append(f'<h1>{_e(r["title"])}</h1><div class="meta">Run {_e(run["run_id"])} · generated {_e(r["generated_at"])} · engine {_e(r["engine_version"])}</div>')
    out.append(f'<div class="verdict {v["tone"]}"><b>{_e(v["label"])}</b><br>{_e(v["detail"])}</div>')
    ex = r["execution"]
    out.append("<h2>Execution summary</h2>" + _table(["Item", "Value"], [
        ["Run ID", ex["run_id"]], ["Domain / Module / Knowledge", ex["hierarchy"]], ["Design source and version", f'{ex["design_source"]} · {ex["design_version"]}'], ["Target", ex["target"]], ["Environment", ex["environment"]],
        ["Build", ex["build"]], ["Started / finished", f'{ex["started_at"] or "-"} / {ex["ended_at"] or "-"}' + (f' ({ex["duration_s"]} s)' if ex["duration_s"] is not None else "")], ["Browser and viewport", f'{ex["browser"]} · {", ".join(ex["viewports"]) or "-"}'],
        ["Categories", ", ".join(ex["categories"])], ["Status", ex["outcome"]],
        ["Checks", f'{ex["checks"]["executed"]} executed · {ex["checks"]["passed"]} passed · {ex["checks"]["failed"]} failed · {ex["checks"]["not_verifiable"]} not verifiable · {ex["checks"]["skipped_blocked"]} screen(s) skipped or blocked'],
        ["Findings by severity", " · ".join(f"{k} {v2}" for k, v2 in ex["findings_by_severity"].items())]]))
    if r["category_results"]:
        out.append("<h3>Selected test categories</h3>" + _table(["Category", "Checks run", "Passed", "Failed", "Not verifiable", "Result"], [[x["label"], x["executed"], x["passed"], x["failed"], x["not_verifiable"], x["status"]] for x in r["category_results"]]) +
                   '<p class="small">"No applicable check" means the design or the page offered nothing to measure for that category; it is not a pass.</p>')
    out.append(f'<p class="small">Replay: {_e(r["replay"]["note"])}</p>')
    t = r["executive"]["totals"]
    out.append('<h2>1. Executive summary</h2><div class="kpis">' + "".join(f'<div class="kpi"><b>{_e(val)}</b>{_e(lab)}</div>' for lab, val in (
        ("Confirmed", t.get("confirmed", 0)), ("Potential", t.get("potential", 0)), ("Needs investigation", t.get("needs_investigation", 0)), ("Not verifiable", t.get("not_verifiable", 0)),
        ("Critical + High", t.get("critical", 0) + t.get("high", 0)), ("Screens tested", f'{r["executive"]["screens_tested"]}/{r["executive"]["screens_planned"]}'))) + "</div>")
    c = r["executive"]["checks"]
    if c:
        out.append(f'<p>{_e(c.get("total", 0))} individual checks ran: {_e(c.get("passed", 0))} passed, {_e(c.get("failed", 0))} failed, {_e(c.get("not_verifiable", 0))} could not be verified.</p>')
    if r["ai"]["summary"]:
        out.append(f'<div class="ai"><b>AI-generated summary (advisory, not evidence)</b><br>{_e(r["ai"]["summary"])}<div class="small">Model: {_e(r["ai"]["model"].get("provider", ""))} {_e(r["ai"]["model"].get("model", ""))}</div></div>')
    elif run["ai_status"] not in ("Not requested",):
        out.append(f'<div class="note">AI assistance: {_e(run["ai_status"])}. {_e(run["ai_note"])}</div>')
    d = r["design"]
    out.append("<h2>2. Design information</h2>" + _table(["Item", "Value"], [["Hierarchy", f'{h["domain"]} › {h["module"]} › {h["knowledge_name"]} › {h["document_type"]} › {h["version"]}'], ["Source type / document", f'{h["source_type"]} / {h["source_file"]}'],
          ["Design asset", d["name"]], ["Content hash", d["content_hash"][:16] + "…" if d["content_hash"] else ""], ["Structural data", "Available" if d["structural"] else "Not available (image comparison only)"],
          ["Last analysed", d["last_analysis_at"] or "never"]]) + _table(["Frame", "Size", "Class", "Image", "Structure"], [[f["name"], f'{int(f["width"])}×{int(f["height"])}', f["viewport_class"], "yes" if f["has_image"] else "no", "yes" if f["has_structure"] else "no"] for f in d["frames"]]))
    a = r["application"]
    out.append("<h2>3. Application information</h2>" + _table(["Item", "Value"], [["Target", a["target"]], ["Origin", a["origin"]], ["Environment", a["environment"]], ["Build / version", a["build"] or "not recorded"], ["Browser", a["browser"]]]) +
               _table(["Screen", "Viewport", "Page title", "Path", "HTTP"], [[p["screen"], p["viewport"], p["title"], p["path"], p["http_status"]] for p in a["pages"]]))
    s = r["scope"]
    out.append("<h2>4. Scope</h2><p>Categories: " + _e(", ".join(s["categories"])) + "</p>" + _table(["Screen", "Viewport", "Status", "Mapping", "Note"], [[x["screen"], x["viewport"], x["status"], ("approved" if x["mapping_trusted"] else "not approved") if x["status"] == "Tested" else "", x["message"]] for x in s["screens"]]))
    if r["untested"]:
        out.append('<div class="note"><b>Not tested:</b> ' + _e("; ".join(f'{u["screen"]} - {u["reason"]}' for u in r["untested"])) + "</div>")
    out.append("<h2>5. Overall results</h2>" + _table(["Classification", "Count"], [[k, v2] for k, v2 in r["results"]["by_classification"].items()]) + _table(["Severity (verified)", "Count"], [[k, v2] for k, v2 in r["results"]["by_severity"].items()]) +
               _table(["Category", "Passed", "Failed", "Not verifiable", "Explained"], [[k, x["passed"], x["failed"], x["not_verifiable"], x["explained"]] for k, x in sorted(r["results"]["by_category"].items())]))
    out.append("<h2>6. Defects</h2>" + _table(FIND_HEAD, _finding_rows(r["defects"])))
    shown = 0
    for f in r["defects"]:
        evid = [e for e in f.get("evidence", []) if e["id"] in images]
        if evid and shown < 15:
            shown += 1
            out.append(f'<h3>{_e(f["defect_id"])} - {_e(f["title"])}</h3><div class="small">{_e(f["description"])}</div>' + (f'<div class="small">Suggested correction ({_e(f["suggestion_source"] or "rule")}): {_e(f["suggestion"])}</div>' if f["suggestion"] else "") +
                       f'<img class="ev" alt="Expected, actual and difference for {_e(f["title"])}" src="{_data_uri(images[evid[0]["id"]])}">' + (f'<div class="ai">AI note (advisory): {_e(f["ai_note"])}</div>' if f["ai_note"] else ""))
    out.append("<h2>7. Visual comparison</h2>")
    for vs in r["visual_screens"]:
        vis = vs["visual"]
        out.append(f'<h3>{_e(vs["screen"])} ({_e(vs["viewport"])})</h3>' + (f'<p>{vis["regions"]} differing region(s); {vis["diff_pixels"]} differing pixels ({vis["diff_ratio"] * 100:.3f}% of the compared area); {vis["masked_pixels_ignored"]} pixels ignored by approved masks.</p>' if vis else '<p class="small">No visual comparison was performed for this screen (see Not Verifiable).</p>'))
        for n in vs["notes"]:
            out.append(f'<div class="note">{_e(n)}</div>')
        evid = vs["evidence"].get("comparison")
        if evid and evid in images:
            out.append(f'<img class="ev" alt="Expected, actual and difference for {_e(vs["screen"])}" src="{_data_uri(images[evid])}">')
    out.append("<h2>8. CSS and typography</h2>" + _table(FIND_HEAD, _finding_rows(r["groups"]["css_typography"])))
    out.append("<h2>9. Forms and components</h2>" + _table(FIND_HEAD, _finding_rows(r["groups"]["forms_components"])))
    out.append("<h2>10. Responsive behaviour</h2>" + _table(FIND_HEAD, _finding_rows(r["groups"]["responsive"])) + '<p class="small">Each frame was compared only against the viewport it is mapped to; no other viewport was inferred.</p>')
    out.append("<h2>11. Accessibility</h2><h3>Automated findings</h3>" + _table(FIND_HEAD, _finding_rows(r["accessibility"]["findings"])) + "<h3>Needs human review (not counted as pass or fail)</h3>" +
               _table(["Topic", "Target", "Question"], [[x["topic"], x["target"], x["question"]] for x in r["accessibility"]["human_review"]]))
    out.append("<h2>12. Not Verifiable</h2><p class=\"small\">These were not checked. They are not passes.</p>" + _table(["Category", "Screen", "Item", "Reason"], [[f["category"], f["screen"], f["title"], f["description"]] for f in r["not_verifiable"]]))
    out.append("<h2>13. QA review decisions</h2>" + _table(["Defect", "Finding", "Decision", "Reviewer", "When", "Comment"], [[x["defect_id"], x["finding"], x["decision"], x["reviewer"], x["reviewed_at"], x["comment"]] for x in r["reviews"]]))
    reg = r["regression"]
    rows = [[k.title(), val] for k, val in (reg["vs_run"] or {}).items()]
    out.append("<h2>14. Regression comparison</h2>" + _table(["Against the previous run", "Count"], rows) + (_table(["Baseline", "New", "Fixed"], [[reg["vs_baseline"]["baseline"]["label"], len(reg["vs_baseline"]["new"]), len(reg["vs_baseline"]["fixed"])]]) if reg["vs_baseline"] else '<p class="small">No active baseline was used.</p>') +
               (_table(["Fixed since the previous run", "Severity", "Category"], [[x["title"], x["severity"], x["category"]] for x in reg["fixed"]]) if reg["fixed"] else ""))
    out.append("<h2>15. Recommendations</h2><ul>" + "".join(f"<li>{_e(x)}</li>" for x in r["recommendations"]) + "</ul>")
    if r["ai"]["recommendations"] or r["ai"]["uncertainty"]:
        out.append('<div class="ai"><b>AI suggestions (advisory)</b><ul>' + "".join(f"<li>{_e(x)}</li>" for x in r["ai"]["recommendations"]) + "</ul>" + ("<b>Where the AI was unsure</b><ul>" + "".join(f"<li>{_e(x)}</li>" for x in r["ai"]["uncertainty"]) + "</ul>" if r["ai"]["uncertainty"] else "") + "</div>")
    out.append('<p class="small">Verified results come from measurements of the design source and the rendered application. AI text is labelled and is never evidence. Items marked Not Verifiable or not tested are not passes.</p></div></body></html>')
    return "".join(out)


# ============================================================================= Excel

def to_xlsx(r: dict, images: dict | None = None) -> bytes:
    from openpyxl import Workbook
    from openpyxl.drawing.image import Image as XLImage
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter
    images = images or {}
    wb = Workbook()
    head_font, head_fill = Font(bold=True, color="FFFFFF"), PatternFill("solid", fgColor="0B5C7A")

    def sheet(name, headers, rows, widths=None):
        ws = wb.create_sheet(name[:31])
        ws.append(headers)
        for c in ws[1]:
            c.font, c.fill, c.alignment = head_font, head_fill, Alignment(vertical="center", wrap_text=True)
        for row in rows:
            ws.append([("" if x is None else (x if isinstance(x, (int, float)) else str(x)[:32000])) for x in row])
        for i, h in enumerate(headers, 1):
            ws.column_dimensions[get_column_letter(i)].width = (widths or {}).get(i, min(48, max(12, len(h) + 4)))
        for row in ws.iter_rows(min_row=2):
            for c in row:
                c.alignment = Alignment(vertical="top", wrap_text=True)
        ws.freeze_panes = "A2"
        if rows:
            ws.auto_filter.ref = ws.dimensions
        return ws

    ws = wb.active
    ws.title = "Summary"
    t = r["executive"]["totals"]
    rows = [("Report", r["title"]), ("Run ID", r["run"]["run_id"]), ("Verdict", r["verdict"]["label"]), ("Verdict detail", r["verdict"]["detail"]), ("Status", r["run"]["status"]),
            ("Domain / Module", f'{r["hierarchy"]["domain"]} / {r["hierarchy"]["module"]}'), ("Knowledge", r["hierarchy"]["knowledge_name"]), ("Document type", r["hierarchy"]["document_type"]),
            ("Design version", r["hierarchy"]["version"]), ("Source", f'{r["hierarchy"]["source_type"]} / {r["hierarchy"]["source_file"]}'), ("Application", r["application"]["origin"]),
            ("Environment", r["application"]["environment"]), ("Build", r["application"]["build"]), ("Browser", r["application"]["browser"]),
            ("Outcome", r["execution"]["outcome"]), ("Categories", ", ".join(r["execution"]["categories"])), ("Run by", r["execution"]["run_by"]), ("Started", r["execution"]["started_at"]), ("Finished", r["execution"]["ended_at"]),
            ("Checks executed", r["execution"]["checks"]["executed"]), ("Checks passed", r["execution"]["checks"]["passed"]), ("Checks failed", r["execution"]["checks"]["failed"]),
            ("Checks not verifiable", r["execution"]["checks"]["not_verifiable"]), ("Screens skipped or blocked", r["execution"]["checks"]["skipped_blocked"]),
            ("Screens tested", f'{r["executive"]["screens_tested"]} of {r["executive"]["screens_planned"]}'), ("Confirmed differences", t.get("confirmed", 0)), ("Potential differences", t.get("potential", 0)),
            ("Needs investigation", t.get("needs_investigation", 0)), ("Not verifiable", t.get("not_verifiable", 0)), ("Critical", t.get("critical", 0)), ("High", t.get("high", 0)),
            ("Medium", t.get("medium", 0)), ("Low", t.get("low", 0)), ("Generated", r["generated_at"]), ("Engine", r["engine_version"]),
            ("Note", "Verified results are measurements. AI text is advisory. Not Verifiable and untested items are not passes.")]
    for k, v in rows:
        ws.append([k, v])
    ws.column_dimensions["A"].width, ws.column_dimensions["B"].width = 26, 90
    for c in ws["A"]:
        c.font = Font(bold=True)
    fh = ["Defect ID", "Severity", "Classification", "Confidence", "Category", "Screen", "Viewport", "Component", "Title", "Description", "Expected", "Actual", "Difference", "Suggested correction", "Suggestion source",
          "AI note (advisory)", "Regression", "QA review", "Needs reassessment", "Run", "Design version", "Build"]
    sheet("Defects", fh, [[f["defect_id"], f["severity"], f["classification"], f["confidence"], f["category"], f["screen"], (f["browser_evidence"] or {}).get("viewport"), f["component"], f["title"], f["description"], f["expected"], f["actual"],
                           f["difference"], f["suggestion"], f["suggestion_source"], f["ai_note"], f["regression_state"], f["review_status"], "yes" if f["needs_reassessment"] else "", f["run_id"], f["design_version"], f["application_build"]] for f in r["defects"]],
          {9: 48, 10: 60, 14: 48, 16: 48})
    sheet("Not Verifiable", ["Category", "Screen", "Item", "Reason"], [[f["category"], f["screen"], f["title"], f["description"]] for f in r["not_verifiable"]], {3: 50, 4: 80})
    sheet("QA Reviews", ["Defect", "Finding", "Decision", "Reviewer", "Reviewed at", "Comment"], [[x["defect_id"], x["finding"], x["decision"], x["reviewer"], x["reviewed_at"], x["comment"]] for x in r["reviews"]], {2: 50, 6: 60})
    sheet("Categories", ["Category", "Checks run", "Passed", "Failed", "Not verifiable", "Result"], [[x["label"], x["executed"], x["passed"], x["failed"], x["not_verifiable"], x["status"]] for x in r["category_results"]], {1: 34, 6: 22})
    sheet("Coverage", ["Category", "Passed", "Failed", "Not verifiable", "Explained"], [[k, x["passed"], x["failed"], x["not_verifiable"], x["explained"]] for k, x in sorted(r["results"]["by_category"].items())])
    sheet("Screens", ["Screen", "Viewport", "Status", "Mapping approved", "Note"], [[x["screen"], x["viewport"], x["status"], "yes" if x["mapping_trusted"] else ("no" if x["status"] == "Tested" else ""), x["message"]] for x in r["scope"]["screens"]], {1: 40, 5: 70})
    sheet("Accessibility review", ["Topic", "Target", "Question"], [[x["topic"], x["target"], x["question"]] for x in r["accessibility"]["human_review"]], {3: 90})
    reg = r["regression"]
    sheet("Regression", ["Measure", "Value"], [[k, v] for k, v in (reg["vs_run"] or {}).items()] + [["Fixed: " + x["title"], x["severity"]] for x in reg["fixed"]] + ([["Baseline", reg["vs_baseline"]["baseline"]["label"]], ["New vs baseline", len(reg["vs_baseline"]["new"])], ["Fixed vs baseline", len(reg["vs_baseline"]["fixed"])]] if reg["vs_baseline"] else []), {1: 60})
    sheet("Recommendations", ["Source", "Recommendation"], [["Rule", x] for x in r["recommendations"]] + [["AI (advisory)", x] for x in r["ai"]["recommendations"]], {2: 120})
    ev_ws = wb.create_sheet("Evidence")
    ev_ws.append(["Screen", "Evidence"])
    row = 2
    for vs in r["visual_screens"]:
        evid = vs["evidence"].get("comparison")
        ev_ws.cell(row=row, column=1, value=f'{vs["screen"]} ({vs["viewport"]})')
        if evid and evid in images:
            try:
                from PIL import Image as PI
                img = PI.open(io.BytesIO(images[evid]))
                scale = min(1.0, 1100 / img.width)
                buf = io.BytesIO()
                img.resize((int(img.width * scale), int(img.height * scale))).save(buf, "PNG")
                buf.seek(0)
                xi = XLImage(buf)
                ev_ws.add_image(xi, f"B{row}")
                ev_ws.row_dimensions[row].height = max(40, xi.height * 0.75)
            except Exception:
                ev_ws.cell(row=row, column=2, value=f"image not embedded (evidence {evid})")
        else:
            ev_ws.cell(row=row, column=2, value="No visual comparison image for this screen")
        row += 1
    ev_ws.column_dimensions["A"].width = 40
    ev_ws.column_dimensions["B"].width = 150
    # Values come from the application under test and from people: a text that starts with '=' must stay text, never become a formula.
    for sheet_ in wb.worksheets:
        for row_ in sheet_.iter_rows():
            for cell_ in row_:
                if isinstance(cell_.value, str) and cell_.value[:1] in ("=", "+", "-", "@") and cell_.data_type != "s":
                    cell_.data_type = "s"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# ============================================================================= PDF

def to_pdf(r: dict, images: dict | None = None) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import Image as RLImage, KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    images = images or {}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm, title=r["title"], author="QA AI Studio")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontSize=8.5, leading=11)
    small = ParagraphStyle("s", parent=body, fontSize=7.5, leading=9.5, textColor=colors.HexColor("#5a6b7b"))
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontSize=18, textColor=colors.HexColor("#14202b"))
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontSize=12, textColor=colors.HexColor("#0b5c7a"), spaceBefore=10)
    P = lambda t, s=body: Paragraph(html.escape(str(t if t is not None else "")).replace("\n", "<br/>"), s)
    story = [Paragraph(html.escape(r["title"]), h1), P(f'Run {r["run"]["run_id"]} · generated {r["generated_at"]} · engine {r["engine_version"]}', small), Spacer(1, 4)]
    tone = {"good": "#17794a", "warn": "#9a6700", "bad": "#b42318", "info": "#175cd3"}[r["verdict"]["tone"]]
    story.append(Paragraph(f'<font color="{tone}"><b>{html.escape(r["verdict"]["label"])}</b></font> - {html.escape(r["verdict"]["detail"])}', body))

    def table(headers, rows, widths=None, repeat=1):
        data = [[P(h, ParagraphStyle("th", parent=body, fontName="Helvetica-Bold")) for h in headers]] + [[P(c) for c in row] for row in rows] if rows else [[P(h) for h in headers], [P("None.")] + [P("") for _ in headers[1:]]]
        if widths is None:
            widths = [(265 * mm) / len(headers)] * len(headers)      # never let the layout engine guess: a long cell could collapse its column
        t = Table(data, colWidths=widths, repeatRows=repeat, hAlign="LEFT")
        t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#d7dee6")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f3f6f9")), ("VALIGN", (0, 0), (-1, -1), "TOP"), ("FONTSIZE", (0, 0), (-1, -1), 8)]))
        return t

    ex = r["execution"]
    story += [Paragraph("Execution summary", h2), table(["Item", "Value"], [["Run ID", ex["run_id"]], ["Domain / Module / Knowledge", ex["hierarchy"]], ["Design source and version", f'{ex["design_source"]} · {ex["design_version"]}'],
              ["Target", ex["target"]], ["Environment / build", f'{ex["environment"]} / {ex["build"]}'], ["Started / finished", f'{ex["started_at"] or "-"} / {ex["ended_at"] or "-"}'],
              ["Browser and viewport", f'{ex["browser"]} · {", ".join(ex["viewports"]) or "-"}'], ["Categories", ", ".join(ex["categories"])], ["Status", ex["outcome"]],
              ["Checks", f'{ex["checks"]["executed"]} executed · {ex["checks"]["passed"]} passed · {ex["checks"]["failed"]} failed · {ex["checks"]["not_verifiable"]} not verifiable · {ex["checks"]["skipped_blocked"]} screen(s) skipped or blocked']], [60 * mm, 200 * mm])]
    if r["category_results"]:
        story.append(table(["Category", "Checks run", "Passed", "Failed", "Not verifiable", "Result"], [[x["label"], x["executed"], x["passed"], x["failed"], x["not_verifiable"], x["status"]] for x in r["category_results"]], [70 * mm, 30 * mm, 30 * mm, 30 * mm, 35 * mm, 70 * mm]))
    t = r["executive"]["totals"]
    story += [Paragraph("1. Executive summary", h2), table(["Confirmed", "Potential", "Needs investigation", "Not verifiable", "Critical", "High", "Medium", "Low", "Screens tested"],
              [[t.get("confirmed", 0), t.get("potential", 0), t.get("needs_investigation", 0), t.get("not_verifiable", 0), t.get("critical", 0), t.get("high", 0), t.get("medium", 0), t.get("low", 0), f'{r["executive"]["screens_tested"]}/{r["executive"]["screens_planned"]}']])]
    c = r["executive"]["checks"]
    if c:
        story.append(P(f'{c.get("total", 0)} checks ran: {c.get("passed", 0)} passed, {c.get("failed", 0)} failed, {c.get("not_verifiable", 0)} could not be verified.'))
    if r["ai"]["summary"]:
        story.append(Paragraph("<b>AI-generated summary (advisory, not evidence):</b> " + html.escape(r["ai"]["summary"]), body))
    d, a, h = r["design"], r["application"], r["hierarchy"]
    story += [Paragraph("2-3. Design and application", h2), table(["Item", "Value"], [["Hierarchy", f'{h["domain"]} > {h["module"]} > {h["knowledge_name"]} > {h["document_type"]} > {h["version"]}'], ["Source", f'{h["source_type"]} / {h["source_file"]}'],
              ["Structural data", "Available" if d["structural"] else "Not available (image comparison only)"], ["Application", f'{a["origin"]} ({a["environment"]})'], ["Build", a["build"] or "not recorded"], ["Browser", a["browser"]]], [60 * mm, 200 * mm])]
    story += [Paragraph("4. Scope", h2), P("Categories: " + ", ".join(r["scope"]["categories"])), table(["Screen", "Viewport", "Status", "Mapping", "Note"], [[x["screen"], x["viewport"], x["status"], ("approved" if x["mapping_trusted"] else "NOT approved") if x["status"] == "Tested" else "", x["message"]] for x in r["scope"]["screens"]], [60 * mm, 35 * mm, 25 * mm, 30 * mm, 112 * mm])]
    story += [Paragraph("5. Overall results", h2), table(["Category", "Passed", "Failed", "Not verifiable"], [[k, x["passed"], x["failed"], x["not_verifiable"]] for k, x in sorted(r["results"]["by_category"].items())], [90 * mm, 35 * mm, 35 * mm, 40 * mm])]
    story += [Paragraph("6. Defects", h2), table(["Defect", "Sev.", "Classification", "Category", "Screen", "Title", "Expected", "Actual", "QA review"],
              [[f["defect_id"][-16:], f["severity"], f'{f["classification"]} ({int(f["confidence"] * 100)}%)', f["category"], f["screen"], f["title"], f["expected"], f["actual"], f["review_status"]] for f in r["defects"]], [27 * mm, 17 * mm, 32 * mm, 25 * mm, 28 * mm, 48 * mm, 30 * mm, 30 * mm, 26 * mm])]
    shown = 0
    for f in r["defects"]:
        evid = [e for e in f.get("evidence", []) if e["id"] in images]
        if evid and shown < 12:
            shown += 1
            try:
                from PIL import Image as PI
                img = PI.open(io.BytesIO(images[evid[0]["id"]]))
                w = min(250 * mm, img.width * 0.75)
                ratio = img.height / img.width
                block = [P(f'{f["defect_id"]} - {f["title"]}', body), RLImage(io.BytesIO(images[evid[0]["id"]]), width=w, height=min(w * ratio, 70 * mm))]
                if f["suggestion"]:
                    block.append(P(f'Suggested correction ({f["suggestion_source"] or "rule"}): {f["suggestion"]}', small))
                story.append(KeepTogether(block))
            except Exception:
                continue
    story.append(Paragraph("7. Visual comparison", h2))
    for vs in r["visual_screens"]:
        vis = vs["visual"]
        story.append(P(f'{vs["screen"]} ({vs["viewport"]}): ' + (f'{vis["regions"]} differing region(s), {vis["diff_ratio"] * 100:.3f}% of compared pixels differ.' if vis else "no visual comparison was performed (see Not Verifiable).")))
        evid = vs["evidence"].get("comparison")
        if evid and evid in images:
            try:
                from PIL import Image as PI
                img = PI.open(io.BytesIO(images[evid]))
                w = min(255 * mm, img.width * 0.5)
                story.append(RLImage(io.BytesIO(images[evid]), width=w, height=min(w * img.height / img.width, 110 * mm)))
            except Exception:
                pass
    for key, title in (("css_typography", "8. CSS and typography"), ("forms_components", "9. Forms and components"), ("responsive", "10. Responsive behaviour")):
        story += [Paragraph(title, h2), table(["Defect", "Sev.", "Classification", "Screen", "Title", "Expected", "Actual"], [[f["defect_id"][-16:], f["severity"], f["classification"], f["screen"], f["title"], f["expected"], f["actual"]] for f in r["groups"][key]], [24 * mm, 17 * mm, 34 * mm, 30 * mm, 70 * mm, 42 * mm, 42 * mm])]
    story += [Paragraph("11. Accessibility", h2), table(["Defect", "Sev.", "Classification", "Title", "Expected", "Actual"], [[f["defect_id"][-16:], f["severity"], f["classification"], f["title"], f["expected"], f["actual"]] for f in r["accessibility"]["findings"]], [24 * mm, 17 * mm, 34 * mm, 90 * mm, 50 * mm, 50 * mm]),
              P("Needs human review (not counted as pass or fail):", small), table(["Topic", "Question"], [[x["topic"], x["question"]] for x in r["accessibility"]["human_review"]], [60 * mm, 205 * mm])]
    story += [Paragraph("12. Not Verifiable (not passes)", h2), table(["Category", "Screen", "Item", "Reason"], [[f["category"], f["screen"], f["title"], f["description"]] for f in r["not_verifiable"]], [28 * mm, 45 * mm, 70 * mm, 122 * mm])]
    story += [Paragraph("13. QA review decisions", h2), table(["Defect", "Decision", "Reviewer", "When", "Comment"], [[x["defect_id"][-16:], x["decision"], x["reviewer"], x["reviewed_at"], x["comment"]] for x in r["reviews"]], [40 * mm, 30 * mm, 26 * mm, 48 * mm, 121 * mm])]
    reg = r["regression"]
    story += [Paragraph("14. Regression comparison", h2), table(["Measure", "Value"], [[k.title(), v] for k, v in (reg["vs_run"] or {}).items()] + ([["Baseline: " + reg["vs_baseline"]["baseline"]["label"], f'{len(reg["vs_baseline"]["new"])} new, {len(reg["vs_baseline"]["fixed"])} fixed']] if reg["vs_baseline"] else [["Baseline", "none active"]]))]
    story += [Paragraph("15. Recommendations", h2)] + [P("• " + x) for x in r["recommendations"]]
    if r["ai"]["recommendations"]:
        story += [P("AI suggestions (advisory):", small)] + [P("• " + x, small) for x in r["ai"]["recommendations"]]
    story.append(Spacer(1, 6))
    story.append(P("Verified results are measurements of the design source and the rendered application. AI text is labelled and is never evidence. Not Verifiable and untested items are not passes.", small))
    doc.build(story)
    return buf.getvalue()


def load_report_images(repo, report: dict, per_finding=15) -> dict:
    """Evidence bytes the exports embed: the side-by-side per screen and the first image of the top findings."""
    ids = []
    for vs in report["visual_screens"]:
        if vs["evidence"].get("comparison"):
            ids.append(vs["evidence"]["comparison"])
    n = 0
    for f in report["defects"]:
        ev = f.get("evidence") or []
        if ev and n < per_finding:
            ids.append(ev[0]["id"])
            n += 1
    out = {}
    for evid in ids:
        try:
            out[evid], _ = repo.evidence_bytes(evid)
        except Exception:
            continue
    return out
