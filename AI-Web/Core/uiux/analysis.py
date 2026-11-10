"""What a registered design can honestly be used for.

The answer is shown on the Design & Upload tab and recorded with every run, so a report never implies more evidence than the file contains:

  * a Figma structure (JSON export or API) gives measured sizes, colours and typography  -> structural checks;
  * an image export (PNG/JPEG/SVG raster) gives pixels at a size the author exported      -> visual comparison;
  * a native ``.fig`` only exposes the small preview thumbnail Figma embeds (typically a few hundred pixels wide). That
    thumbnail is a *reference picture*, not the frame at its design size, so its dimensions are never used as a viewport and
    nothing is measured from it other than what a picture shows (overall look and dominant colours).
"""
from __future__ import annotations

FIG_THUMBNAIL = "fig-thumbnail"


def is_reference_only(frame: dict) -> bool:
    """True when the frame's only content is an embedded preview thumbnail whose size is not the design size."""
    return (frame.get("image_source") or "") == FIG_THUMBNAIL and not frame.get("has_structure", bool(frame.get("nodes")))


def frame_dimensions(frame: dict) -> dict:
    w, h = int(round(frame.get("width") or 0)), int(round(frame.get("height") or 0))
    src = frame.get("image_source") or ""
    if src == FIG_THUMBNAIL:
        return {"width": w, "height": h, "source": "preview thumbnail embedded in the .fig file", "reliable": False,
                "note": "This is the size of a small preview picture, not the size of the design frame."}
    if frame.get("has_structure") or frame.get("nodes"):
        return {"width": w, "height": h, "source": "Figma frame bounds", "reliable": True, "note": ""}
    if src in ("svg-raster",):
        return {"width": w, "height": h, "source": "rasterised SVG", "reliable": True, "note": "SVG size as drawn."}
    return {"width": w, "height": h, "source": "image export", "reliable": bool(w and h),
            "note": "Assumed to be the frame exported at 1x. A 2x export would appear twice as wide as the frame."}


def analyse(asset: dict) -> dict:
    """Summary used by the UI: status (Completed / Limited / Failed), the frame, its dimensions, which checks are possible and the limits."""
    frames = [f for f in asset.get("frames", []) if f.get("selected")] or list(asset.get("frames", []))
    kind = asset.get("asset_kind", "")
    out = {"status": "Failed", "frame": "", "frames": len(frames), "dimensions": None, "available": "None", "checks": {"visual": False, "structural": False},
           "limitations": [], "summary": ""}
    if not frames:
        msg = {"fig": "No preview image could be read from this native .fig file, so there is nothing to compare. Upload a PNG/JPEG export of the frame or a Figma JSON export.",
               "figma_link": "Select at least one frame of the Figma file."}.get(kind, "This design contains no frame that can be tested.")
        out["summary"] = msg
        out["limitations"].append(msg)
        return out
    first = frames[0]
    visual = any(f.get("has_image") for f in frames)
    structural = any(f.get("has_structure") for f in frames)
    reference = all(is_reference_only(f) for f in frames if f.get("has_image")) and visual and not structural
    out["frame"] = first["name"] if len(frames) == 1 else f"{len(frames)} frames"
    out["dimensions"] = frame_dimensions(first)
    out["checks"] = {"visual": visual, "structural": structural}
    if reference:
        out["status"] = "Limited"
        out["available"] = "Reference image only"
        out["limitations"] += [
            f"Only the {out['dimensions']['width']}×{out['dimensions']['height']} preview thumbnail embedded in the .fig could be read. Its size is not the design size.",
            "Fonts, CSS, sizes and spacing cannot be read from this file and are reported as Not Verifiable.",
            "The page is judged against the thumbnail only for overall look and dominant colours, and those results need review.",
        ]
        out["summary"] = ("Analysis limited — reference preview only. Overall look and colours can be compared; fonts, CSS and measurements cannot be verified from this file. "
                          "For full checks upload a PNG export of the frame, a Figma JSON export, or connect the Figma file.")
    elif visual and structural:
        out["status"] = "Completed"
        out["available"] = "Visual and structural"
        out["summary"] = "Analysis completed — visual and structural comparison available."
    elif structural:
        out["status"] = "Limited"
        out["available"] = "Structural only"
        out["limitations"].append("No design image is attached, so pixel comparison is not available.")
        out["summary"] = "Analysis completed — structural checks available; visual comparison needs a design image."
    else:
        out["status"] = "Completed"
        out["available"] = "Visual only"
        out["limitations"].append("An image carries no style data: fonts, CSS values and exact spacing cannot be verified.")
        out["summary"] = "Analysis completed — visual comparison available. Structural CSS and font properties cannot be verified from this file."
    if out["dimensions"] and out["dimensions"]["note"] and not reference:
        out["limitations"].append(out["dimensions"]["note"])
    return out
