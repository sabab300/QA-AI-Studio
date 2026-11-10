"""The header of the Reports tab: what was tested, with what, and how the results add up. Read from saved run data only."""
from __future__ import annotations

from datetime import datetime

from . import categories as UC
from . import results as R
from .analysis import analyse
from .clickup import page_url


def duration_s(run: dict):
    try:
        if run.get("started_at") and run.get("ended_at"):
            return round((datetime.fromisoformat(run["ended_at"]) - datetime.fromisoformat(run["started_at"])).total_seconds())
    except ValueError:
        pass
    return None


def run_header(repo, registry, results, run_id: str) -> dict:
    run = repo.get_run(run_id)
    counts = results.counts(run_id)
    cfg, summ = run["config"] or {}, run["summary"] or {}
    units = summ.get("units") or []
    tested = [u for u in units if u.get("status") == "Tested"]
    selected = list(cfg.get("user_categories") or [])
    checked = [k for k, v in counts["by_category"].items() if v[R.PASS] + v[R.FAIL] + v[R.REVIEW]]
    analysis = None
    try:
        if run["asset_id"]:
            analysis = analyse(registry.get(run["asset_id"]))
    except Exception:
        analysis = None
    notes = []
    for u in tested:
        notes += [n for n in (u.get("notes") or [])][:3]
        if u.get("reference_only"):
            notes.append("The design is a .fig preview thumbnail: the page was tested at the default desktop viewport "
                         f"({u['viewport_px'][0]}x{u['viewport_px'][1]}) and only a coarse look-and-colour comparison, marked Needs Review, was possible against it.")
    return {"run_id": run_id, "status": run["status"], "status_detail": run["status_detail"], "design": run["source_file"] or run["knowledge_name"], "version": run["version"],
            "website": page_url(run), "test_date": run.get("started_at") or run["created_at"], "ended_at": run.get("ended_at"), "duration_s": duration_s(run),
            "categories_selected": [{"key": k, "label": UC.LABELS.get(k, k)} for k in selected], "categories_checked": [{"key": k, "label": UC.LABELS.get(k, k)} for k in checked],
            "counts": counts, "tickets": len(results.links_for_run(run_id)), "design_analysis": analysis, "notes": list(dict.fromkeys(notes))[:8],
            "viewports": [{"screen": u.get("screen"), "viewport": u.get("viewport_px"), "default_used": bool(u.get("reference_only"))} for u in tested],
            "units_not_tested": [{"screen": u.get("screen"), "reason": u.get("message", "")} for u in units if u.get("status") != "Tested"],
            "ai_note": run.get("ai_note", ""), "engine_version": run.get("engine_version", ""), "has_results": counts["total_rows"] > 0}
