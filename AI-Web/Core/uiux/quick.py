"""The one-click workflow: Select a design -> enter the target URL -> pick categories -> Run.

This module turns those few inputs into what the engine needs, without relaxing any rule:

  * the target URL is validated and, only when the user confirms they are authorized, registered as an authorized target
    exactly the way the Advanced "Authorize target" form does (internal/loopback addresses still need an administrator);
  * a single design frame is mapped to the entered page automatically and its size becomes the default viewport;
  * several frames get a compact suggested mapping, and anything ambiguous is returned for the user to decide;
  * the configuration is built and validated here, so a missing target or a typo in a tolerance is refused with a clear message
    *before* a run exists, instead of producing a "Blocked" run later.

Nothing here opens a website on its own except the optional reachability check and page matching, which run only after the
target has been authorized.
"""
from __future__ import annotations

import socket
from urllib.parse import urlsplit

from . import categories as UC
from .repo import RepoError, VIEWPORT_PRESETS, UiuxRepo
from .security import ENVIRONMENTS, TargetError, authorize, classify_ip, normalize_url, origin_of, resolve_host
from .structure import Tolerances
from .analysis import is_reference_only

MAX_PLAN_FRAMES = 40
CONFIDENT = 0.65         # a suggestion this good, and clearly ahead of the runner-up, needs no question
MARGIN = 0.15
VIEWPORT_MODES = ("auto", "desktop", "tablet", "mobile", "custom")


class QuickError(ValueError):
    """A problem the user can fix; ``code`` lets the UI react, ``status`` is the HTTP status the router uses."""

    def __init__(self, message: str, code: str = "invalid", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


# ----------------------------------------------------------------------------- target
def split_url(url: str) -> tuple[str, str, str]:
    """(normalised URL, origin, route) - the route is the path and query only, never a full URL."""
    try:
        clean = normalize_url(url)
    except TargetError as error:
        raise QuickError(str(error), "invalid_url") from None
    parts = urlsplit(clean)
    route = (parts.path or "/") + (("?" + parts.query) if parts.query else "")
    try:
        route = UiuxRepo.clean_route(route)
    except RepoError as error:
        raise QuickError(str(error), "invalid_url") from None
    return clean, origin_of(clean), route


DEFAULT_VIEWPORT = (1440, 900)       # used when the design gives no reliable frame size; it is named in every report


def auto_environment(host: str, resolver=None) -> str:
    """The normal workflow has no environment field: an internal address is QA, anything public is recorded as Production."""
    try:
        classes = {classify_ip(ip) for ip in resolve_host(host, resolver)}
    except TargetError:
        return "Production"
    return "QA" if classes & {"loopback", "private"} else "Production"


def _tcp_reachable(ip: str, port: int, timeout=3.0) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_target(repo: UiuxRepo, url: str, environment: str, user: str, is_admin: bool, confirmed: bool, resolver=None, probe=True) -> dict:
    """Validate the URL, authorize it when the user confirmed (same rules as the Advanced form), and report reachability."""
    clean, origin, route = split_url(url)
    host = urlsplit(clean).hostname.lower()
    if environment in ("", "auto", None):
        environment = auto_environment(host, resolver)
    if environment not in ENVIRONMENTS:
        raise QuickError("Select a valid environment: " + ", ".join(ENVIRONMENTS) + ".", "environment")
    records = repo.target_records()
    record = next((r for r in records if r["origin"] == origin and r["environment"] == environment and r["active"]), None)
    newly = False
    if record is None:
        try:
            ips = resolve_host(host, resolver)
        except TargetError as error:
            raise QuickError(str(error), "unresolved") from None
        classes = {classify_ip(ip) for ip in ips}
        if "blocked" in classes:
            raise QuickError(f"'{host}' is in a blocked address range (metadata, link-local or reserved) and can never be tested.", "blocked", 403)
        internal = bool(classes & {"loopback", "private"})
        if not confirmed:
            raise QuickError(f"{origin} is not an authorized target for {environment} yet. Confirm that you are authorized to test it to continue.", "needs_confirmation", 409)
        if internal and not is_admin:
            raise QuickError(f"'{host}' is an internal address (this machine or a private network). Only an administrator can authorize internal targets; "
                             "ask an administrator to authorize it once, then it can be tested by anyone with access.", "admin_required", 403)
        try:
            record = repo.add_target(host + (f":{urlsplit(clean).port}" if urlsplit(clean).port else ""), origin, environment, internal, [], user,
                                     note="Authorized from AI UI/UX Testing", resolver=resolver)
        except RepoError as error:
            raise QuickError(str(error), "target") from None
        newly = True
    try:
        target = authorize(origin + "/", environment, repo.target_records(), resolver)
    except TargetError as error:
        raise QuickError(str(error), "target", 403) from None
    reachable = None
    if probe and target.pinned_ips:
        port = urlsplit(clean).port or (443 if origin.startswith("https") else 80)
        reachable = _tcp_reachable(target.pinned_ips[0], port)
    return {"ok": True, "target_id": record["id"], "origin": origin, "route": route, "environment": environment, "internal": target.internal, "newly_authorized": newly,
            "reachable": reachable,
            "message": ("The application did not answer. Check that it is running and the URL is correct; the test will report this if it still cannot connect." if reachable is False else "")}


# ----------------------------------------------------------------------------- viewport and frames
def resolve_viewport(mode: str, frame: dict, custom: dict | None = None) -> tuple[int, int]:
    if mode not in VIEWPORT_MODES:
        raise QuickError("Choose Auto, Desktop, Tablet, Mobile or Custom for the viewport.", "viewport")
    if mode == "custom":
        try:
            w, h = int((custom or {}).get("w")), int((custom or {}).get("h"))
        except (TypeError, ValueError):
            raise QuickError("Enter a custom viewport width and height in pixels.", "viewport") from None
        if not (200 <= w <= 3840 and 200 <= h <= 5000):
            raise QuickError("The custom viewport must be between 200x200 and 3840x5000 pixels.", "viewport")
        return w, h
    if mode in VIEWPORT_PRESETS:
        return VIEWPORT_PRESETS[mode]
    if is_reference_only(frame) or not (frame.get("width") and frame.get("height")):
        return DEFAULT_VIEWPORT          # a preview thumbnail's size is not the design size; never use it as the viewport
    w = int(round(frame["width"]))
    h = int(round(frame["height"]))
    return max(200, min(w, 3840)), max(200, min(h, 1200))


def usable_frames(registry, asset_id: str, frame_ids=None) -> tuple[dict, list]:
    asset = registry.get(asset_id)
    wanted = set(frame_ids or [])
    frames = [f for f in asset["frames"] if (f["id"] in wanted if wanted else f["selected"])]
    if not frames:
        hint = {"fig": " A native .fig file cannot be read structurally; upload a PNG/JPEG export of the frame or a Figma JSON, or attach a frame image under Advanced options.",
                "figma_link": " Select at least one frame of the Figma file."}.get(asset["asset_kind"], "")
        raise QuickError("This design has no frame that can be tested." + hint, "no_frames")
    return asset, frames


def needs_analysis(asset: dict, frames: list) -> bool:
    return asset["asset_kind"] == "figma_link" and any(not f["has_image"] and not f["has_structure"] for f in frames)


def capabilities(frame: dict) -> dict:
    """What can be verified for this frame, so the UI states the limits instead of implying checks that cannot happen."""
    structural = bool(frame.get("has_structure"))
    ref = is_reference_only(frame)
    note = ""
    if ref:
        note = ("Reference preview only: the .fig contains just a small thumbnail whose size is not the design size. The page is tested at the default desktop viewport (1440x900); "
                "overall look and colours are compared for review, and fonts, CSS and measurements are Not Verifiable.")
    elif not structural:
        note = "Image design: visual comparison only. Structural checks (colours, fonts, spacing values) are reported as Not Verifiable."
    return {"visual": bool(frame.get("has_image")), "structural": structural, "reference_only": ref, "note": note}


# ----------------------------------------------------------------------------- plan
def build_plan(repo, registry, asset_id: str, frame_ids, target_id: str, route: str, viewport_mode="auto", custom=None, user="", resolver=None, auth=None, probe=True) -> dict:
    """One row per frame to test: which page, which viewport, and whether the user must choose."""
    asset, frames = usable_frames(registry, asset_id, frame_ids)
    if len(frames) > MAX_PLAN_FRAMES:
        raise QuickError(f"Select at most {MAX_PLAN_FRAMES} frames per test.", "too_many_frames")
    target_row = repo.get_target(target_id)
    existing = {}
    for m in repo.list_mappings(asset["document_version_id"]):
        if m["status"] != "Rejected" and m["asset_id"] == asset_id and m["target_id"] == target_id:
            existing.setdefault(m["frame_id"], []).append(m)
    rows = []
    for fr in frames:
        w, h = resolve_viewport(viewport_mode, fr, custom)
        rows.append({"frame_id": fr["id"], "frame": fr["name"], "page": fr["page"], "design_size": [int(round(fr["width"])), int(round(fr["height"]))], "route": route, "viewport": {"w": w, "h": h},
                     "viewport_source": ("default desktop viewport (design size not reliable)" if (is_reference_only(fr) and viewport_mode == "auto") else ("design frame size" if viewport_mode == "auto" else viewport_mode)),
                     "status": "auto", "score": None, "candidates": [], "capabilities": capabilities(fr), "note": ""})
    if len(rows) == 1:
        rows[0]["note"] = "One frame: it is compared with the page you entered."
        return {"single": True, "rows": rows, "ambiguous": 0, "asset_kind": asset["asset_kind"]}
    # several frames: keep what the user already approved, suggest the rest
    suggestions = {}
    if probe:
        try:
            from .mapping_suggest import discover_routes, suggest
            target = authorize(target_row["origin"] + "/", target_row["environment"], repo.target_records(), resolver)
            routes = [route]
            try:
                routes += [r for r in discover_routes(target, auth) if r not in routes]
            except Exception:
                pass
            result = suggest(repo, registry, asset_id, target_row, routes[:6], user, resolver=resolver, auth=auth, create=False)
            suggestions = {s["frame_id"]: s for s in result["suggestions"]}
        except Exception as error:            # matching is a convenience; the user can still choose pages by hand
            for r in rows:
                r["note"] = "Pages could not be matched automatically (" + str(error)[:120] + "). Choose the page for each frame."
    ambiguous = 0
    for r in rows:
        old = existing.get(r["frame_id"])
        if old:
            m = old[0]
            r.update(route=m["route"], status="existing", score=m["confidence"], note="Using the mapping you approved earlier.")
            continue
        s = suggestions.get(r["frame_id"])
        cands = [{"route": c["route"], "score": round(c["score"], 2)} for c in ((s or {}).get("candidates") or []) if "error" not in (c.get("signals") or {})]
        r["candidates"] = cands
        if cands and cands[0]["score"] >= CONFIDENT and (len(cands) == 1 or cands[0]["score"] - cands[1]["score"] >= MARGIN):
            r.update(route=cands[0]["route"], status="auto", score=cands[0]["score"], note="Matched automatically.")
        elif cands and cands[0]["score"] >= 0.35:
            r.update(route=cands[0]["route"], status="needs_choice", score=cands[0]["score"], note="More than one page could match. Check the suggestion.")
            ambiguous += 1
        else:
            r.update(status="needs_choice", note=r["note"] or "No page matched this frame with enough confidence. Choose its page or skip it.")
            ambiguous += 1
    return {"single": False, "rows": rows, "ambiguous": ambiguous, "asset_kind": asset["asset_kind"]}


def apply_rows(repo, registry, asset_id: str, target_id: str, rows: list, viewport_mode: str, custom, user: str) -> list:
    """Create (or reuse) the approved mappings the user confirmed in the table; returns the mapping ids to test."""
    asset = registry.get(asset_id)
    frames = {f["id"]: f for f in asset["frames"]}
    ids = []
    for row in rows or []:
        if row.get("skip"):
            continue
        fr = frames.get(row.get("frame_id"))
        if not fr:
            raise QuickError("A selected frame does not belong to this design.", "frame")
        try:
            route = UiuxRepo.clean_route(row.get("route") or "/")
        except RepoError as error:
            raise QuickError(str(error), "route") from None
        vp = row.get("viewport") or {}
        if viewport_mode == "auto" and vp.get("w") and vp.get("h"):
            size = (int(vp["w"]), int(vp["h"]))
        else:
            size = resolve_viewport(viewport_mode, fr, custom)
        existing = next((m for m in repo.list_mappings(asset["document_version_id"])
                         if m["frame_id"] == fr["id"] and m["target_id"] == target_id and m["route"] == route and m["viewport_w"] == size[0] and m["status"] != "Rejected"), None)
        if existing:
            if existing["status"] != "Approved":
                repo.set_status(existing["id"], "Approved", user)
            ids.append(existing["id"])
            continue
        try:
            m = repo.create_mapping(asset_id, fr["id"], target_id, route, size, user, method="manual", confidence=float(row.get("score") or 1.0),
                                    evidence={"origin": "one-click", "approved_by_action": True, "status_in_plan": row.get("status", "")})
        except RepoError as error:
            raise QuickError(str(error), "mapping") from None
        ids.append(m["id"])
    if not ids:
        raise QuickError("Nothing to test: every frame was skipped.", "nothing_selected")
    return ids


# ----------------------------------------------------------------------------- configuration
def build_config(body: dict, target_id: str, mapping_ids: list, environment: str = "") -> dict:
    asked = list(body.get("categories") or [])
    unknown = [str(k)[:40] for k in asked if k not in UC.KEYS]
    if unknown:
        raise QuickError("Unknown test category: " + ", ".join(unknown[:3]) + ".", "categories")
    cats = list(dict.fromkeys(asked))
    if not cats:
        raise QuickError("Select at least one test category.", "categories")
    bad = Tolerances.problems(body.get("tolerances"))
    if bad:
        raise QuickError("Check the tolerances: " + " ".join(bad[:3]), "tolerances")
    visual = {}
    thr = (body.get("visual") or {}).get("color_threshold")
    if thr is not None:
        if isinstance(thr, bool) or not isinstance(thr, (int, float)) or not (1 <= thr <= 255):
            raise QuickError("The visual similarity threshold must be a number from 1 to 255.", "tolerances")
        visual = {"color_threshold": float(thr)}
    auth = body.get("auth") or "none"
    if auth not in ("none", "stored"):
        raise QuickError("Choose None or stored credentials for authentication.", "auth")
    return {"target_id": target_id, "environment": environment if environment in ENVIRONMENTS else (body.get("environment") if body.get("environment") in ENVIRONMENTS else "QA"), "build_label": str(body.get("build_label") or "")[:80], "browser": "chromium",
            "mapping_ids": mapping_ids, "ui_categories": True, "user_categories": cats, "tolerances": body.get("tolerances") or {}, "visual": visual,
            "auth": {"type": auth}, "ai": {"enabled": bool(body.get("ai"))}, "scope": "selected", "colour_scheme": "dark" if body.get("colour_scheme") == "dark" else "light"}
