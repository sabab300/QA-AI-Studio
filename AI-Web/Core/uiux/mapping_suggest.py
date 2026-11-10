"""Suggest frame -> page mappings by probing authorized pages. Suggestions are never auto-approved."""
from __future__ import annotations

from . import matching
from .browser import BrowserSession
from .security import authorize

MAX_ROUTES = 12


def discover_routes(target, auth=None, limit=MAX_ROUTES) -> list:
    """Same-origin, non-destructive links found on the target's start page (read-only)."""
    with BrowserSession(target, viewport=(1440, 900), auth=auth) as s:
        s.open("/")
        s.stabilize()
        links = s.safe_links(limit)
    return links


def suggest(repo, registry, asset_id: str, target_row: dict, routes: list, user: str, resolver=None, auth=None, agent=None, create=True) -> dict:
    asset = registry.get(asset_id)
    target = authorize(target_row["origin"] + "/", target_row["environment"], repo.target_records(), resolver)
    routes = [repo.clean_route(r) for r in routes][:MAX_ROUTES]
    if not routes:
        return {"suggestions": [], "note": "Enter at least one page path to compare with the design frames."}
    frames = [registry.frame(f["id"]) for f in asset["frames"] if f["selected"]]
    probes = {}
    for width in sorted({int(round(f["width"])) for f in frames}):
        height = 900 if width > 600 else 800
        for route in routes:
            try:
                with BrowserSession(target, viewport=(width, height), auth=auth) as s:
                    s.open(route)
                    s.stabilize()
                    probes[(width, route)] = s.probe()
            except Exception as error:
                probes[(width, route)] = {"error": str(error)[:150]}
    out = []
    for fr in frames:
        w = int(round(fr["width"]))
        ranked = []
        for route in routes:
            probe = probes.get((w, route))
            if not probe or "error" in probe:
                ranked.append({"route": route, "score": 0.0, "signals": {"error": (probe or {}).get("error", "")}})
                continue
            score, signals = matching.screen_score(fr, probe, route)
            ranked.append({"route": route, "score": score, "signals": signals, "title": probe.get("title", "")})
        ranked.sort(key=lambda r: -r["score"])
        entry = {"frame_id": fr["id"], "frame": fr["name"], "width": w, "candidates": ranked[:5], "created": None, "note": ""}
        if agent and ranked and ranked[0]["score"] < 0.8:
            try:
                cands = [{"id": r["route"], "path": r["route"], "title": r.get("title", ""), "texts": []} for r in ranked if "error" not in r["signals"]]
                texts = [n["text"]["chars"] for n in fr["nodes"] if n.get("type") == "TEXT" and (n.get("text") or {}).get("chars")]
                for ai in agent.rank_pages({"name": fr["name"], "width": w, "texts": texts}, cands)[:1]:
                    entry["ai_hint"] = {"route": ai["id"], "reason": ai["reason"], "confidence": ai["confidence"], "advisory": True}
            except Exception as error:
                entry["ai_hint"] = {"unavailable": str(error)[:120]}
        best = ranked[0] if ranked else None
        if best and best["score"] >= 0.35 and create:
            try:
                m = repo.create_mapping(asset_id, fr["id"], target_row["id"], best["route"], (w, 900 if w > 600 else 800), user, method="auto", confidence=best["score"],
                                        evidence={"signals": best["signals"], "alternatives": [{"route": r["route"], "score": r["score"]} for r in ranked[1:4]]})
                entry["created"] = m["id"]
            except Exception as error:
                entry["note"] = str(error)
        elif not best or best["score"] < 0.35:
            entry["note"] = "No page matched this frame with enough confidence. Map it manually."
        out.append(entry)
    return {"suggestions": out, "note": "Suggested mappings are not approved. Review them and approve (or correct) before relying on Confirmed results."}
