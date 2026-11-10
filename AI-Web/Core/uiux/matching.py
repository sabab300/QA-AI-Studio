"""Screen and component matching: Figma frame -> application page -> elements, with confidence scores.

Matching never silently confirms a guess:
  * every pair carries a score and the signals behind it;
  * a pair below ``confirm`` is "Needs confirmation" and downstream findings inherit a reduced confidence;
  * a design node whose best two candidates are nearly tied is "Ambiguous";
  * an approved manual map (design node id -> DOM selector) always overrides the automatic result.
"""
from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from .figma_parser import classify_viewport

CONTROL_ROLES = {"button", "input", "select", "textarea", "checkbox", "radio", "toggle", "link"}
# design role -> DOM roles that are a full / partial fit
_ROLE_FIT = {
    "button": ({"button"}, {"link"}),
    "input": ({"textbox", "searchbox", "spinbutton"}, {"combobox"}),
    "textarea": ({"textbox"}, set()),
    "select": ({"combobox", "listbox"}, {"textbox"}),
    "checkbox": ({"checkbox"}, set()),
    "radio": ({"radio"}, set()),
    "toggle": ({"checkbox", "switch"}, {"button"}),
    "table": ({"table"}, {"row", "rowgroup", "cell", "columnheader"}),
    "heading": ({"heading"}, set()),
    "link": ({"link"}, {"button"}),
    "nav": ({"navigation", "link"}, {"banner", "list"}),
    "icon": ({"img"}, set()),
    "image": ({"img"}, set()),
    "tab": ({"tab"}, {"button", "link"}),
    "label": (set(), set()),
}


@dataclass
class MatchConfig:
    confirm: float = 0.80
    minimum: float = 0.52
    ambiguity_margin: float = 0.06
    radius_pad: float = 140.0


def norm_text(value) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    text = re.sub(r"[\s*:]+$", "", text)
    return text


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z0-9]+", norm_text(text)))


def text_similarity(a: str, b: str) -> float:
    na, nb = norm_text(a), norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    ta, tb = _tokens(na), _tokens(nb)
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    if na in nb or nb in na:
        jac = max(jac, 0.6)
    return round(jac, 3)


def design_text(node: dict, nodes: list) -> str:
    """The text a design node displays: its own characters, or its direct text children (for buttons, fields...)."""
    if node.get("type") == "TEXT":
        return (node.get("text") or {}).get("chars", "") or ""
    if node.get("role") == "table":
        return ""
    prefix = node["path"] + "/"
    kids = [n for n in nodes if n.get("type") == "TEXT" and n["path"].startswith(prefix) and n["depth"] == node["depth"] + 1]
    kids.sort(key=lambda n: (n["y"], n["x"]))
    return " ".join((k.get("text") or {}).get("chars", "") for k in kids).strip()


def dom_texts(e: dict) -> list:
    out = []
    for value in (e.get("text"), e.get("fullText"), e.get("name"), (e.get("attrs") or {}).get("placeholder"), e.get("selectedText")):
        if value and value not in out:
            out.append(value)
    return out


def _iou(a, b) -> float:
    x0, y0 = max(a["x"], b["x"]), max(a["y"], b["y"])
    x1, y1 = min(a["x"] + a["w"], b["x"] + b["w"]), min(a["y"] + a["h"], b["y"] + b["h"])
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = a["w"] * a["h"] + b["w"] * b["h"] - inter
    return inter / union if union > 0 else 0.0


def _geo(d, e, diag) -> float:
    iou = _iou(d, e)
    dx = (d["x"] + d["w"] / 2) - (e["x"] + e["w"] / 2)
    dy = (d["y"] + d["h"] / 2) - (e["y"] + e["h"] / 2)
    prox = max(0.0, 1 - ((dx * dx + dy * dy) ** 0.5) / max(1.0, diag))
    size = (min(d["w"], e["w"]) / max(d["w"], e["w"], 1e-6)) * (min(d["h"], e["h"]) / max(d["h"], e["h"], 1e-6))
    base = 0.5 * iou + 0.3 * prox + 0.2 * size
    # a text layer sits inside its padded cell/label element: containment is a strong geometric signal
    ix0, iy0 = max(d["x"], e["x"]), max(d["y"], e["y"])
    ix1, iy1 = min(d["x"] + d["w"], e["x"] + e["w"]), min(d["y"] + d["h"], e["y"] + e["h"])
    inter = max(0.0, ix1 - ix0) * max(0.0, iy1 - iy0)
    small = min(d["w"] * d["h"], e["w"] * e["h"])
    if small > 0 and inter / small >= 0.95:
        base = max(base, 0.5 + 0.5 * small / max(d["w"] * d["h"], e["w"] * e["h"]))
    return base


def role_fit(design_role: str, e: dict) -> float:
    if not design_role or design_role in ("text", "container", "badge", "alert", "breadcrumb", "pagination"):
        return 0.6
    if design_role == "label":
        return 1.0 if e["tag"] in ("label", "legend") else (0.5 if e.get("text") else 0.2)
    full, partial = _ROLE_FIT.get(design_role, (set(), set()))
    role = e.get("role") or ""
    if role in full:
        return 1.0
    if role in partial:
        return 0.6
    if not role and design_role in ("heading", "nav") and e["tag"] in ("span", "div", "a"):
        return 0.45
    return 0.0 if role else 0.25


def score_pair(d: dict, e: dict, dtext: str, diag: float) -> tuple[float, dict]:
    geo = _geo(d, e, diag)
    role = role_fit(d.get("role", ""), e)
    if d.get("type") == "TEXT" and role < 0.5 and (e.get("role") or "") not in CONTROL_ROLES:
        role = 0.5   # a text layer's role comes from its layer name only; it is a hint, never a veto
    best_text = max((text_similarity(dtext, t) for t in dom_texts(e)), default=0.0) if dtext else 0.0
    if dtext:
        total = 0.38 * geo + 0.44 * best_text + 0.18 * role
        if best_text == 0.0:
            total *= 0.55
    else:
        total = 0.72 * geo + 0.28 * role
    if d.get("role") and role == 0.0:
        total *= 0.5
    return round(total, 4), {"geometry": round(geo, 3), "text": round(best_text, 3) if dtext else None, "role": round(role, 2)}


def _eligible_dom(e: dict) -> bool:
    if e["w"] <= 0 or e["h"] <= 0:
        return False
    return bool(e.get("role") or e.get("text") or e["tag"] in ("label", "legend", "header", "footer", "section", "main", "aside", "td", "th", "tr"))


def match_elements(design_nodes: list, dom_elements: list, frame_size: tuple, approved_map: dict | None = None,
                   cfg: MatchConfig | None = None) -> dict:
    """Return {pairs, unmatched_design, unmatched_dom, aliases, stats}.

    ``pairs`` entries: {design_id, dom_index, score, status, signals, method}.
    """
    cfg = cfg or MatchConfig()
    approved_map = approved_map or {}
    diag = (frame_size[0] ** 2 + frame_size[1] ** 2) ** 0.5
    nodes = [n for n in design_nodes if n.get("depth", 0) > 0]
    by_id = {n["id"]: n for n in nodes}
    dom = [e for e in dom_elements if _eligible_dom(e)]
    by_index = {e["i"]: e for e in dom_elements}
    by_selector = defaultdict(list)
    for e in dom_elements:
        by_selector[e["selector"]].append(e)

    # spatial buckets + text index keep this far below O(n*m)
    cell = 120
    grid = defaultdict(list)
    text_index = defaultdict(list)
    for e in dom:
        grid[(int(e["x"] // cell), int(e["y"] // cell))].append(e)
        for t in dom_texts(e):
            text_index[norm_text(t)].append(e)

    def candidates(d, dtext):
        radius = max(d["w"], d["h"]) / 2 + cfg.radius_pad
        cx, cy = d["x"] + d["w"] / 2, d["y"] + d["h"] / 2
        seen, out = set(), []
        x0, x1 = int((cx - radius - d["w"]) // cell), int((cx + radius + d["w"]) // cell)
        y0, y1 = int((cy - radius - d["h"]) // cell), int((cy + radius + d["h"]) // cell)
        for gx in range(x0, x1 + 1):
            for gy in range(y0, y1 + 1):
                for e in grid.get((gx, gy), ()):
                    if e["i"] not in seen:
                        seen.add(e["i"]); out.append(e)
        if dtext:
            for e in text_index.get(norm_text(dtext), ()):
                if e["i"] not in seen:
                    seen.add(e["i"]); out.append(e)
        return out

    scored = []
    dtexts = {}
    for d in nodes:
        dtexts[d["id"]] = design_text(d, design_nodes)
        for e in candidates(d, dtexts[d["id"]]):
            s, sig = score_pair(d, e, dtexts[d["id"]], diag)
            if s >= cfg.minimum - 0.1:
                scored.append((s, d["id"], e["i"], sig))
    scored.sort(key=lambda t: -t[0])

    pairs, used_d, used_e = [], set(), set()
    # manual approvals first
    for did, selector in approved_map.items():
        d = by_id.get(did)
        cands = by_selector.get(selector) or []
        if d and cands:
            e = cands[0]
            pairs.append({"design_id": did, "dom_index": e["i"], "score": 1.0, "status": "Approved", "signals": {"manual": True}, "method": "approved"})
            used_d.add(did); used_e.add(e["i"])
    runner_up = {}
    for s, did, ei, sig in scored:
        runner_up.setdefault(did, []).append((s, ei))
    for s, did, ei, sig in scored:
        if did in used_d or ei in used_e or s < cfg.minimum:
            continue
        rivals = [r for r in runner_up.get(did, []) if r[1] != ei and r[1] not in used_e and r[0] >= cfg.minimum]
        ambiguous = bool(rivals) and (s - rivals[0][0]) < cfg.ambiguity_margin and sig.get("text") != 1.0
        status = "Ambiguous" if ambiguous else ("Auto-matched" if s >= cfg.confirm else "Needs confirmation")
        pairs.append({"design_id": did, "dom_index": ei, "score": s, "status": status, "signals": sig, "method": "auto"})
        used_d.add(did); used_e.add(ei)

    # TEXT layers inside a matched control share that control's DOM element (button label, field placeholder)
    aliases = []
    matched_by_d = {p["design_id"]: p for p in pairs}
    for d in nodes:
        if d["id"] in used_d or d.get("type") != "TEXT":
            continue
        text = dtexts[d["id"]]
        for p in pairs:
            parent = by_id.get(p["design_id"])
            if not parent or parent.get("type") == "TEXT":
                continue
            inside = d["x"] >= parent["x"] - 1 and d["y"] >= parent["y"] - 1 and d["x"] + d["w"] <= parent["x"] + parent["w"] + 1 and d["y"] + d["h"] <= parent["y"] + parent["h"] + 1
            if not inside or not d["path"].startswith(parent["path"] + "/"):
                continue
            e = by_index[p["dom_index"]]
            kind = "selected-text" if text and norm_text(e.get("selectedText")) == norm_text(text) else "placeholder" if norm_text((e.get("attrs") or {}).get("placeholder")) == norm_text(text) and text else ("content" if any(text_similarity(text, t) >= 0.99 for t in (e.get("text"), e.get("name"), e.get("fullText")) if t) else "")
            if kind:
                aliases.append({"design_id": d["id"], "dom_index": e["i"], "parent_design_id": parent["id"], "kind": kind,
                                "score": round(min(1.0, p["score"]), 3), "status": p["status"], "method": "container-text"})
                used_d.add(d["id"])
                break

    unmatched_design = [d["id"] for d in nodes if d["id"] not in used_d]
    unmatched_dom = [e["i"] for e in dom if e["i"] not in used_e and (e.get("role") in CONTROL_ROLES or e.get("text"))]
    return {"pairs": pairs, "aliases": aliases, "unmatched_design": unmatched_design, "unmatched_dom": unmatched_dom,
            "stats": {"design_nodes": len(nodes), "dom_candidates": len(dom), "paired": len(pairs), "aliased": len(aliases),
                      "auto": sum(1 for p in pairs if p["status"] == "Auto-matched"),
                      "needs_confirmation": sum(1 for p in pairs if p["status"] in ("Needs confirmation", "Ambiguous"))}}


# ----------------------------------------------------------------------------- screens

def screen_score(frame: dict, probe: dict, route: str = "") -> tuple[float, dict]:
    """How well does an application page fit a design frame? (viewport class, title/route words, shared text)."""
    texts = [n["text"]["chars"] for n in frame["nodes"] if n.get("type") == "TEXT" and (n.get("text") or {}).get("chars")]
    page_texts = []
    for e in probe["elements"]:
        for t in (e.get("text"),):
            if t:
                page_texts.append(t)
    ft, pt = {norm_text(t) for t in texts if t}, {norm_text(t) for t in page_texts}
    overlap = len(ft & pt) / max(1, len(ft)) if ft else 0.0
    words_frame = _tokens(frame["name"]) - {"desktop", "mobile", "tablet", "screen", "page", "frame", "web"}
    words_page = _tokens(f"{probe.get('title', '')} {route}")
    name_sim = len(words_frame & words_page) / max(1, len(words_frame)) if words_frame else 0.0
    vp = classify_viewport(probe["viewport"]["w"])
    viewport_fit = 1.0 if vp == frame.get("viewport_class") else 0.0
    width_fit = max(0.0, 1 - abs(probe["viewport"]["w"] - frame["width"]) / max(frame["width"], 1))
    score = 0.55 * overlap + 0.15 * name_sim + 0.15 * viewport_fit + 0.15 * width_fit
    return round(score, 3), {"text_overlap": round(overlap, 3), "name_similarity": round(name_sim, 3), "viewport_class": viewport_fit == 1.0,
                             "width_fit": round(width_fit, 3), "shared_texts": len(ft & pt), "design_texts": len(ft)}
