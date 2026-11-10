"""Screenshot comparison with explicit tolerances (numpy + Pillow only).

Not every pixel difference is a defect. This engine:
  * ignores sub-pixel / anti-aliasing shifts (a pixel is "different" only if no pixel within +-``shift`` px in the other
    image has a similar colour, in BOTH directions);
  * ignores approved mask rectangles (dynamic content such as timestamps);
  * groups differing pixels into regions so a human sees "where", not millions of pixels;
  * never resizes images silently: a size mismatch is reported and only the overlapping area is compared.
"""
from __future__ import annotations

import io
from dataclasses import dataclass, field

import numpy as np
from PIL import Image, ImageDraw, ImageFont

MAX_PIXELS = 60_000_000


@dataclass
class VisualConfig:
    color_threshold: float = 28.0      # RGB Euclidean distance (0..441) above which a pixel differs
    shift: int = 1                     # anti-aliasing / sub-pixel tolerance in pixels
    block: int = 8                     # grouping cell size
    block_min_pixels: int = 3          # differing pixels a cell needs to count
    merge_cells: int = 1               # cells apart that still merge into one region
    min_region_pixels: int = 12        # regions smaller than this are dropped
    max_height: int = 12000
    pass_ratio: float = 0.0005         # diff pixel ratio at/below which the screen counts as matching


def _load(data: bytes) -> np.ndarray:
    with Image.open(io.BytesIO(data)) as im:
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
            bg.alpha_composite(im)
            im = bg
        return np.asarray(im.convert("RGB"), dtype=np.int16)


def _dist(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    d = a.astype(np.float32) - b.astype(np.float32)
    return np.sqrt((d * d).sum(axis=2))


def _shifted_min(a: np.ndarray, b: np.ndarray, shift: int) -> np.ndarray:
    """Minimum colour distance between each pixel of ``a`` and the (2*shift+1)^2 neighbourhood of ``b``."""
    h, w = a.shape[:2]
    best = np.full((h, w), 1e9, dtype=np.float32)
    padded = np.pad(b, ((shift, shift), (shift, shift), (0, 0)), mode="edge")
    for dy in range(2 * shift + 1):
        for dx in range(2 * shift + 1):
            cand = padded[dy:dy + h, dx:dx + w]
            np.minimum(best, _dist(a, cand).astype(np.float32), out=best)
    return best


def _regions(mask: np.ndarray, cfg: VisualConfig) -> list:
    h, w = mask.shape
    bs = cfg.block
    gh, gw = (h + bs - 1) // bs, (w + bs - 1) // bs
    pad = np.zeros((gh * bs, gw * bs), dtype=np.uint8)
    pad[:h, :w] = mask
    cells = pad.reshape(gh, bs, gw, bs).sum(axis=(1, 3)) >= cfg.block_min_pixels
    seen = np.zeros_like(cells, dtype=bool)
    out = []
    m = cfg.merge_cells
    for gy, gx in zip(*np.nonzero(cells)):
        if seen[gy, gx]:
            continue
        stack, comp = [(gy, gx)], []
        seen[gy, gx] = True
        while stack:
            cy, cx = stack.pop()
            comp.append((cy, cx))
            for ny in range(max(0, cy - m - 0), min(gh, cy + m + 1)):
                for nx in range(max(0, cx - m), min(gw, cx + m + 1)):
                    if cells[ny, nx] and not seen[ny, nx]:
                        seen[ny, nx] = True
                        stack.append((ny, nx))
        ys = [c[0] for c in comp]
        xs = [c[1] for c in comp]
        x0, y0 = min(xs) * bs, min(ys) * bs
        x1, y1 = min(w, (max(xs) + 1) * bs), min(h, (max(ys) + 1) * bs)
        px = int(mask[y0:y1, x0:x1].sum())
        if px >= cfg.min_region_pixels:
            out.append({"x": int(x0), "y": int(y0), "w": int(x1 - x0), "h": int(y1 - y0), "diff_pixels": px})
    out.sort(key=lambda r: -r["diff_pixels"])
    return out


def compare(expected_png: bytes, actual_png: bytes, masks: list | None = None, cfg: VisualConfig | None = None) -> dict:
    cfg = cfg or VisualConfig()
    ea, aa = _load(expected_png), _load(actual_png)
    eh, ew = ea.shape[:2]
    ah, aw = aa.shape[:2]
    notes = []
    truncated = False
    if max(eh, ah) > cfg.max_height:
        ea, aa = ea[:cfg.max_height], aa[:cfg.max_height]
        eh, ah = ea.shape[0], aa.shape[0]
        truncated = True
        notes.append(f"Images were cropped to the first {cfg.max_height}px for comparison.")
    h, w = min(eh, ah), min(ew, aw)
    if h * w > MAX_PIXELS:
        raise ValueError("The images are too large to compare.")
    size = {"expected": [ew, eh], "actual": [aw, ah], "compared": [w, h],
            "width_mismatch": ew != aw, "height_mismatch": eh != ah, "truncated": truncated}
    e, a = ea[:h, :w], aa[:h, :w]
    raw = _dist(e, a) > cfg.color_threshold
    if cfg.shift > 0 and raw.any():
        keep = (_shifted_min(e, a, cfg.shift) > cfg.color_threshold) & (_shifted_min(a, e, cfg.shift) > cfg.color_threshold)
        diff = raw & keep
    else:
        diff = raw
    masked_pixels = 0
    for r in masks or []:
        x0, y0 = max(0, int(r["x"])), max(0, int(r["y"]))
        x1, y1 = min(w, int(r["x"] + r["w"])), min(h, int(r["y"] + r["h"]))
        if x1 > x0 and y1 > y0:
            masked_pixels += int(diff[y0:y1, x0:x1].sum())
            diff[y0:y1, x0:x1] = False
    regions = _regions(diff.astype(np.uint8), cfg)
    for r in regions:
        sl = (slice(r["y"], r["y"] + r["h"]), slice(r["x"], r["x"] + r["w"]))
        sub = diff[sl]
        if sub.any():
            r["expected_mean"] = [int(v) for v in e[sl][sub].mean(axis=0)]
            r["actual_mean"] = [int(v) for v in a[sl][sub].mean(axis=0)]
        r["ratio"] = round(r["diff_pixels"] / max(1, r["w"] * r["h"]), 4)
    total = int(diff.sum())
    ratio = total / max(1, h * w)
    return {"size": size, "diff_pixels": total, "diff_ratio": round(ratio, 6), "masked_pixels_ignored": masked_pixels,
            "regions": regions[:200], "region_count": len(regions), "match": ratio <= cfg.pass_ratio and not regions,
            "notes": notes, "config": {"color_threshold": cfg.color_threshold, "shift": cfg.shift, "block": cfg.block},
            "_diff": diff, "_e": e, "_a": a}


def _font(size=14):
    for name in ("DejaVuSans.ttf", "Arial.ttf", "LiberationSans-Regular.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def diff_overlay(result: dict, regions=None, label=True) -> bytes:
    """Actual screenshot faded, differing pixels in magenta, regions outlined."""
    a, diff = result["_a"], result["_diff"]
    faded = (a * 0.35 + 255 * 0.65).astype(np.uint8)
    faded[diff] = (255, 0, 160)
    im = Image.fromarray(faded, "RGB")
    draw = ImageDraw.Draw(im)
    for n, r in enumerate(regions if regions is not None else result["regions"], 1):
        draw.rectangle([r["x"] - 2, r["y"] - 2, r["x"] + r["w"] + 1, r["y"] + r["h"] + 1], outline=(220, 38, 38), width=2)
        if label:
            draw.text((r["x"] + 2, max(0, r["y"] - 14)), str(n), fill=(220, 38, 38), font=_font(12))
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def side_by_side(result: dict, max_panel_width=720) -> bytes:
    """Expected | Actual | Difference with captions."""
    e, a = result["_e"], result["_a"]
    overlay = Image.open(io.BytesIO(diff_overlay(result, label=True))).convert("RGB")
    panels = [("Expected design", Image.fromarray(e.astype(np.uint8), "RGB")), ("Actual application", Image.fromarray(a.astype(np.uint8), "RGB")), ("Difference", overlay)]
    scale = min(1.0, max_panel_width / panels[0][1].width)
    gap, cap = 12, 28
    scaled = [(t, im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)) for t, im in panels]
    W = sum(im.width for _, im in scaled) + gap * (len(scaled) + 1)
    H = max(im.height for _, im in scaled) + cap + gap
    canvas = Image.new("RGB", (W, H), (241, 245, 249))
    draw = ImageDraw.Draw(canvas)
    x = gap
    for title, im in scaled:
        draw.text((x, 6), title, fill=(15, 23, 42), font=_font(14))
        canvas.paste(im, (x, cap))
        x += im.width + gap
    buf = io.BytesIO()
    canvas.save(buf, "PNG", optimize=True)
    return buf.getvalue()


def crop(png: bytes, x, y, w, h, pad=0) -> bytes:
    with Image.open(io.BytesIO(png)) as im:
        box = (max(0, int(x - pad)), max(0, int(y - pad)), min(im.width, int(x + w + pad)), min(im.height, int(y + h + pad)))
        out = io.BytesIO()
        im.crop(box).save(out, "PNG")
        return out.getvalue()


def region_difference(expected_png: bytes, actual_png: bytes, box: dict, cfg: VisualConfig | None = None) -> float:
    """Fraction of differing pixels inside one rectangle (used for icon/image checks)."""
    cfg = cfg or VisualConfig()
    pad = 2
    e = _load(crop(expected_png, box["x"], box["y"], box["w"], box["h"], pad))
    a = _load(crop(actual_png, box["x"], box["y"], box["w"], box["h"], pad))
    h, w = min(e.shape[0], a.shape[0]), min(e.shape[1], a.shape[1])
    if h < 1 or w < 1:
        return 0.0
    e, a = e[:h, :w], a[:h, :w]
    d = _dist(e, a) > cfg.color_threshold
    return float(d.sum()) / (h * w)


def public(result: dict) -> dict:
    return {k: v for k, v in result.items() if not k.startswith("_")}


def crop_pair_ratio(expected_png: bytes, e_box: dict, actual_png: bytes, a_box: dict, cfg: VisualConfig | None = None, max_size_gap=3) -> float | None:
    """Share of the drawn ("ink") pixels of a design region and an application region that differ.

    Normalising by ink instead of by area keeps small glyphs/icons meaningful: a 16px icon is mostly background, so a
    pixel-area ratio would hide a completely different shape. Returns None when the two regions differ in size.
    """
    cfg = cfg or VisualConfig()
    if abs(e_box["w"] - a_box["w"]) > max_size_gap or abs(e_box["h"] - a_box["h"]) > max_size_gap:
        return None
    e = _load(crop(expected_png, e_box["x"], e_box["y"], e_box["w"], e_box["h"], 2))
    a = _load(crop(actual_png, max(0, a_box["x"]), max(0, a_box["y"]), a_box["w"], a_box["h"], 2))
    h, w = min(e.shape[0], a.shape[0]), min(e.shape[1], a.shape[1])
    if h < 1 or w < 1:
        return None
    e, a = e[:h, :w], a[:h, :w]
    bg_e, bg_a = e[0, 0].astype(np.float32), a[0, 0].astype(np.float32)
    ink_e = np.sqrt(((e.astype(np.float32) - bg_e) ** 2).sum(axis=2)) > cfg.color_threshold
    ink_a = np.sqrt(((a.astype(np.float32) - bg_a) ** 2).sum(axis=2)) > cfg.color_threshold
    ink = ink_e | ink_a
    if not ink.any():
        return 0.0 if _dist(e, a).max() <= cfg.color_threshold else 1.0
    # glyph shapes are compared at the same position; anti-aliasing noise is absorbed by a higher colour threshold instead of a shift
    d = _dist(e, a) > max(cfg.color_threshold, 60.0)
    return float((d & ink).sum()) / float(ink.sum())


def triptych(expected_png: bytes | None, actual_png: bytes, diff_overlay_png: bytes | None, region: dict, e_box: dict | None = None, a_box: dict | None = None, pad=18, max_w=420) -> bytes:
    """Expected | Actual | Difference crops around one finding, with the compared boxes outlined."""
    x0, y0 = max(0, int(region["x"] - pad)), max(0, int(region["y"] - pad))
    w, h = int(region["w"] + 2 * pad), int(region["h"] + 2 * pad)
    panels = []
    sources = [("Expected design", expected_png, e_box), ("Actual application", actual_png, a_box), ("Difference", diff_overlay_png, None)]
    for title, data, box in sources:
        if data is None:
            panels.append((title, None))
            continue
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGB")
            tile = im.crop((x0, y0, min(im.width, x0 + w), min(im.height, y0 + h)))
        if box:
            d = ImageDraw.Draw(tile)
            d.rectangle([box["x"] - x0 - 1, box["y"] - y0 - 1, box["x"] - x0 + box["w"], box["y"] - y0 + box["h"]], outline=(220, 38, 38), width=2)
        panels.append((title, tile))
    real = [t for _, t in panels if t is not None]
    scale = min(3.0, max_w / max(1, max(t.width for t in real)))
    gap, cap = 10, 26
    scaled = []
    for title, t in panels:
        if t is None:
            scaled.append((title, None))
        else:
            scaled.append((title, t.resize((max(1, int(t.width * scale)), max(1, int(t.height * scale))), Image.NEAREST if scale >= 1.5 else Image.LANCZOS)))
    width = sum((t.width if t else max_w // 2) for _, t in scaled) + gap * (len(scaled) + 1)
    height = max(t.height for t in [x for _, x in scaled if x is not None]) + cap + gap
    canvas = Image.new("RGB", (width, height), (241, 245, 249))
    draw = ImageDraw.Draw(canvas)
    x = gap
    for title, t in scaled:
        draw.text((x, 6), title, fill=(15, 23, 42), font=_font(13))
        if t is None:
            draw.rectangle([x, cap, x + max_w // 2, cap + 60], outline=(148, 163, 184))
            draw.text((x + 8, cap + 22), "not available", fill=(100, 116, 139), font=_font(12))
            x += max_w // 2 + gap
        else:
            canvas.paste(t, (x, cap))
            x += t.width + gap
    out = io.BytesIO()
    canvas.save(out, "PNG", optimize=True)
    return out.getvalue()
