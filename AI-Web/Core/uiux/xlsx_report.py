"""Excel report of one AI UI/UX Testing run (five sheets, screenshots genuinely embedded).

The workbook is built only from what was saved when the run executed (results, evidence images, ClickUp links). Nothing is
regenerated or invented here; a result without a stored screenshot says "Evidence unavailable" and why.

Sheets: Executive Summary | Test Results | Screenshot Evidence | ClickUp Tickets | Execution Details
"""
from __future__ import annotations

import io
from urllib.parse import urlsplit

from openpyxl import Workbook
from openpyxl.drawing.image import Image as XLImage
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from PIL import Image

from . import categories as UC
from . import results as R
from .structure import Tolerances

INK, MUTED, LINE = "0F172A", "475569", "CBD5E1"
FILL = {"PASS": "DCFCE7", "FAIL": "FEE2E2", "Needs Review": "FEF3C7", "Not Verifiable": "E2E8F0", "Not Applicable": "F1F5F9", "Not Executed": "F1F5F9"}
FONT_COLOR = {"PASS": "166534", "FAIL": "991B1B", "Needs Review": "92400E", "Not Verifiable": "334155", "Not Applicable": "475569", "Not Executed": "475569"}
THIN = Side(style="thin", color=LINE)
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
MAX_EMBED_ROWS = 300           # rows with their own embedded crops; the rest point at the full-page images
IMG_W, IMG_H = 300, 210        # pixels of one embedded crop
PAGE_W, PAGE_H = 420, 1000     # a full-page screenshot is scaled to fit this box
PX_PER_POINT = 4 / 3


def safe(value):
    """Cell text that can never be evaluated as a formula."""
    if value is None:
        return ""
    return value if not isinstance(value, str) else value[:32000]


def put(ws, row, col, value, bold=False, fill=None, color=None, wrap=True, size=None, align=None, border=True):
    cell = ws.cell(row=row, column=col)
    value = safe(value)
    cell.value = value
    if isinstance(value, str):
        cell.data_type = "s"                      # a text cell, never a formula, whatever the first character is
        if value[:1] in ("=", "+", "-", "@", "\t", "\r"):
            cell.quotePrefix = True
    cell.alignment = Alignment(wrap_text=wrap, vertical="top", horizontal=align)
    cell.font = Font(bold=bold, color=color or INK, size=size or 10)
    if fill:
        cell.fill = PatternFill("solid", fgColor=fill)
    if border:
        cell.border = BORDER
    return cell


def link(cell, url, text=None):
    if url and urlsplit(url).scheme == "https":
        cell.hyperlink = url
        cell.font = Font(color="1D4ED8", underline="single", size=10)
        if text is not None:
            cell.value = text
            cell.data_type = "s"


def _thumb(data: bytes, max_w: int, max_h: int):
    """(bytes, w, h) of the image scaled down proportionally to fit the box, never enlarged."""
    with Image.open(io.BytesIO(data)) as im:
        im = im.convert("RGB")
        scale = min(1.0, max_w / im.width, max_h / im.height)
        if scale < 1.0:
            im = im.resize((max(1, int(im.width * scale)), max(1, int(im.height * scale))), Image.LANCZOS)
        out = io.BytesIO()
        im.save(out, "PNG", optimize=True)
        return out.getvalue(), im.width, im.height


def _embed(ws, data: bytes, anchor: str, max_w: int, max_h: int):
    raw, w, h = _thumb(data, max_w, max_h)
    img = XLImage(io.BytesIO(raw))
    img.width, img.height = w, h
    ws.add_image(img, anchor)
    return w, h


def spec_lines(sp: dict, limit: int = 14) -> tuple:
    """(expected lines, actual lines) of one result's specification, aligned line by line, differences first."""
    order = {"differs": 0, "match": 1, "info": 2, "Not Verifiable": 3}
    fields = [f for f in (sp or {}).get("fields", []) if f["field"] not in ("Screen / page", "Location (hierarchy)", "Position in page")]
    fields = sorted(fields, key=lambda f: order.get(f["status"], 4))[:limit]
    exp = [f"{f['field']}: {f['expected']}" for f in fields]
    act = [f"{f['field']}: {f['actual']}" + ("   ◄ DIFFERS" if f["status"] == "differs" else (" (not verifiable)" if f["status"] == "Not Verifiable" else "")) for f in fields]
    return exp, act


def location_text(sp: dict) -> str:
    if not sp:
        return ""
    parts = []
    if sp.get("screen"):
        parts.append(f"Screen: {sp['screen']}")
    if sp.get("page_url"):
        parts.append(f"Page: {sp['page_url']}")
    if sp.get("element"):
        parts.append(f"Component: {sp['element']}")
    if sp.get("website_location"):
        parts.append(f"On the page: {sp['website_location']}")
    elif sp.get("design_location"):
        parts.append(f"In the design: {sp['design_location']}")
    if sp.get("position"):
        parts.append(f"Position: {sp['position']}")
    if sp.get("also_affected_total"):
        parts.append(f"+{sp['also_affected_total']} more similar element(s)")
    return "\n".join(parts)


def difference_text(sp: dict) -> str:
    return "\n".join(f"{d['field']}: expected {d['expected']}, actual {d['actual']}" for d in (sp or {}).get("differences", [])[:10])


def build(repo, results, registry, run_id: str) -> tuple:
    """Return (xlsx bytes, info) where info reports embedded image counts and sheet names for verification."""
    run = repo.get_run(run_id)
    rows = results.all(run_id)
    counts = results.counts(run_id)
    links = results.links_for_run(run_id)
    summary = run["summary"] or {}
    cfg = run["config"] or {}
    wb = Workbook()
    info = {"embedded_images": 0, "rows_with_images": 0, "rows_without_images": 0, "sheets": []}

    # --------------------------------------------------------------- 1 Executive Summary
    ws = wb.active
    ws.title = "Executive Summary"
    ws.sheet_view.showGridLines = False
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 62
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14
    c = ws.cell(row=1, column=1, value="QA AI Studio — AI UI/UX Test Report")
    c.font = Font(bold=True, size=16, color=INK)
    ws.cell(row=2, column=1, value="PASS and FAIL results with evidence for one test run").font = Font(color=MUTED, size=10)
    hier = " › ".join(x for x in (run["domain"], run["module"], run["knowledge_name"], run["document_type"], run["version"]) if x)
    started, ended = run.get("started_at") or "", run.get("ended_at") or ""
    duration = ""
    if started and ended:
        from datetime import datetime
        try:
            duration = f"{(datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds():.0f} s"
        except ValueError:
            duration = ""
    meta = [("Design name", run["source_file"] or run["knowledge_name"]), ("Design version", run["version"]), ("Knowledge Hub hierarchy", hier), ("Target URL", _target(run)),
            ("Run ID", run_id), ("Execution date/time", started or run["created_at"]), ("Run status", run["status"] + (f" — {run['status_detail']}" if run["status_detail"] else "")),
            ("Selected categories", ", ".join(UC.LABELS.get(k, k) for k in cfg.get("user_categories", [])) or "(legacy configuration)"),
            ("Categories actually checked", ", ".join(UC.LABELS.get(k, k) for k, v in counts["by_category"].items() if v[R.PASS] + v[R.FAIL] + v[R.REVIEW]) or "none"),
            ("Execution duration", duration or "n/a"), ("Total checks executed (PASS + FAIL + Needs Review)", counts["checks_executed"]),
            ("Individual element comparisons behind those checks", counts["element_comparisons"])]
    r = 4
    for label, value in meta:
        put(ws, r, 1, label, bold=True, fill="F1F5F9")
        cell = put(ws, r, 2, value)
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
        if label == "Target URL":
            link(cell, value)
        r += 1
    r += 1
    put(ws, r, 1, "Result", bold=True, fill=INK, color="FFFFFF")
    put(ws, r, 2, "Meaning", bold=True, fill=INK, color="FFFFFF")
    put(ws, r, 3, "Rows", bold=True, fill=INK, color="FFFFFF")
    ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
    meaning = {R.PASS: "Executed and verified against the design", R.FAIL: "Confirmed difference from the design", R.REVIEW: "Needs a person to decide", R.NOT_VERIFIABLE: "The design does not provide the data to check this",
               R.NOT_APPLICABLE: "Nothing applicable to check", R.NOT_EXECUTED: "The category did not run (stopped or blocked run)"}
    r0 = r + 1
    for status in R.STATUSES:
        r += 1
        put(ws, r, 1, status, bold=True, fill=FILL[status], color=FONT_COLOR[status])
        put(ws, r, 2, meaning[status])
        put(ws, r, 3, counts["by_status"][status], align="center")
        ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
    r += 1
    put(ws, r, 1, "Total rows", bold=True)
    put(ws, r, 2, "Reconciles with the Test Results sheet: " + ("yes" if counts["total_rows"] == len(rows) == sum(counts["by_status"].values()) else "NO — investigate"))
    put(ws, r, 3, counts["total_rows"], bold=True, align="center")
    ws.merge_cells(start_row=r, start_column=3, end_row=r, end_column=4)
    r += 2
    put(ws, r, 1, "Category", bold=True, fill=INK, color="FFFFFF")
    put(ws, r, 2, "PASS / FAIL / Review / Not verifiable / N/A / Not executed", bold=True, fill=INK, color="FFFFFF")
    ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
    for key in cfg.get("user_categories") or list(counts["by_category"].keys()):
        r += 1
        row = counts["by_category"].get(key)
        put(ws, r, 1, UC.LABELS.get(key, key))
        put(ws, r, 2, " / ".join(str(row[s]) for s in R.STATUSES) if row else "not executed")
        ws.merge_cells(start_row=r, start_column=2, end_row=r, end_column=4)
    info["sheets"].append(ws.title)

    # --------------------------------------------------------------- 2 Test Results
    wr = wb.create_sheet("Test Results")
    headers = ["Test ID", "Category", "Test / Check", "Expected Design", "Actual Website", "Result", "Reason", "Severity", "Evidence Reference", "ClickUp Ticket ID", "ClickUp Ticket URL", "Ticket Status", "Execution Date/Time",
               "Component", "Exact Location", "Element Locator", "Difference", "Suggested Fix"]
    widths = [17, 20, 38, 34, 34, 15, 52, 11, 22, 18, 34, 14, 22, 26, 46, 38, 44, 54]
    for i, (h, w) in enumerate(zip(headers, widths), start=1):
        put(wr, 1, i, h, bold=True, fill=INK, color="FFFFFF")
        wr.column_dimensions[get_column_letter(i)].width = w
    wr.freeze_panes = "A2"
    wr.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{max(2, len(rows) + 1)}"
    order = sorted(rows, key=lambda x: (R.SORT_ORDER[x["result"]], x["seq"]))
    result_row = {}
    for n, x in enumerate(order, start=2):
        t = x["ticket"]
        vals = [x["test_id"], x["category_label"], x["name"], x["expected"], x["actual"], x["result"], x["reason"], x["severity"] or "—", "", t["task_id"] if t and t["outcome"] == "Created" else "",
                t["task_url"] if t and t["outcome"] == "Created" else "", x["ticket_status"], x["executed_at"],
                (sp := (x.get("evidence") or {}).get("spec") or {}).get("element", ""), location_text(sp), sp.get("locator", ""), difference_text(sp), sp.get("suggested_fix", "")]
        for i, v in enumerate(vals, start=1):
            put(wr, n, i, v)
        put(wr, n, 6, x["result"], bold=True, fill=FILL[x["result"]], color=FONT_COLOR[x["result"]], align="center")
        link(wr.cell(row=n, column=11), vals[10])
        result_row[x["id"]] = n
    wr.conditional_formatting.add(f"F2:F{max(2, len(rows) + 1)}", FormulaRule(formula=['$F2="PASS"'], fill=PatternFill(start_color=FILL["PASS"], end_color=FILL["PASS"], fill_type="solid"), font=Font(bold=True, color=FONT_COLOR["PASS"])))
    wr.conditional_formatting.add(f"F2:F{max(2, len(rows) + 1)}", FormulaRule(formula=['$F2="FAIL"'], fill=PatternFill(start_color=FILL["FAIL"], end_color=FILL["FAIL"], fill_type="solid"), font=Font(bold=True, color=FONT_COLOR["FAIL"])))
    wr.conditional_formatting.add(f"F2:F{max(2, len(rows) + 1)}", FormulaRule(formula=['$F2="Needs Review"'], fill=PatternFill(start_color=FILL["Needs Review"], end_color=FILL["Needs Review"], fill_type="solid"), font=Font(bold=True, color=FONT_COLOR["Needs Review"])))
    wr.conditional_formatting.add(f"F2:F{max(2, len(rows) + 1)}", FormulaRule(formula=['$F2="Not Verifiable"'], fill=PatternFill(start_color=FILL["Not Verifiable"], end_color=FILL["Not Verifiable"], fill_type="solid"), font=Font(bold=True, color=FONT_COLOR["Not Verifiable"])))
    info["sheets"].append(wr.title)

    # --------------------------------------------------------------- 3 Screenshot Evidence
    we = wb.create_sheet("Screenshot Evidence")
    heads = ["Test ID", "Test / Check", "Result", "Exact location / component", "Expected specification (design)", "Actual specification (website)", "Difference and suggested fix",
             "Design screenshot", "Website screenshot", "Highlighted difference"]
    wid = [17, 30, 14, 40, 38, 40, 48, 46, 46, 46]
    for i, (h, w) in enumerate(zip(heads, wid), start=1):
        put(we, 1, i, h, bold=True, fill=INK, color="FFFFFF")
        we.column_dimensions[get_column_letter(i)].width = w
    we.freeze_panes = "A2"
    SLOTS = [("design", 8), ("actual", 9), ("diff", 10)]
    LAST = len(heads)
    er = 2
    embed_budget = MAX_EMBED_ROWS
    page_sets = {}
    for x in order:
        ev = x.get("evidence") or {}
        sp = ev.get("spec") or {}
        has_crop = ev.get("mode") in ("crop", "reference") and any(ev.get(s_) for s_, _ in SLOTS)
        exp_l, act_l = spec_lines(sp)
        fix = sp.get("suggested_fix") or ""
        diff_txt = difference_text(sp)
        tail = "\n\n".join(t for t in (("Difference:\n" + diff_txt) if diff_txt else "", ("Suggested fix:\n" + fix) if fix else "", x["reason"] if not diff_txt else "") if t)
        for col, v in zip(range(1, 8), [x["test_id"], x["name"], x["result"], location_text(sp) or x["reason"], "\n".join(exp_l) or x["expected"], "\n".join(act_l) or x["actual"], tail or x["reason"]]):
            put(we, er, col, v, size=9 if col >= 4 else None)
        put(we, er, 3, x["result"], bold=True, fill=FILL[x["result"]], color=FONT_COLOR[x["result"]], align="center")
        text_h = max(len((location_text(sp) or "").split("\n")) * 2.2, len(exp_l) * 1.6, len(tail) / 34 + tail.count("\n"), 4) * 12.5
        tallest = 0
        if has_crop and embed_budget > 0:
            embed_budget -= 1
            for slot, col in SLOTS:
                evid = ev.get(slot)
                cell = put(we, er, col, "")
                if not evid:
                    cell.value = "Not available for this check" + (" (the design has no image to crop)" if slot == "design" else (" (no reliable difference overlay)" if slot == "diff" else ""))
                    cell.font = Font(color=MUTED, italic=True, size=9)
                    continue
                try:
                    data, _ = repo.evidence_bytes(evid)
                    w, h = _embed(we, data, f"{get_column_letter(col)}{er}", IMG_W, IMG_H)
                    info["embedded_images"] += 1
                    tallest = max(tallest, h)
                except Exception:
                    cell.value = "Evidence unavailable: the stored image could not be read"
                    cell.font = Font(color="991B1B", italic=True, size=9)
            info["rows_with_images"] += 1
            we.row_dimensions[er].height = min(409, max(60, (tallest + 10) / PX_PER_POINT, text_h))
        else:
            if ev.get("mode") in ("crop", "reference") and embed_budget <= 0:
                note = f"Not embedded (limit of {MAX_EMBED_ROWS} evidence rows per workbook). The images are stored in QA AI Studio under {x['test_id']}."
            elif ev.get("mode") == "page":
                key = (ev.get("page_design"), ev.get("page_actual"), ev.get("page_diff"))
                page_sets.setdefault(key, []).append(x["test_id"])
                note = "Full-page screenshots for this screen are in the ‘Full-page evidence’ section below the table. " + (ev.get("limitation") or "")
            else:
                note = "Evidence unavailable: " + (ev.get("limitation") or "no screenshot was stored for this result.")
            c8 = put(we, er, 8, note)
            c8.font = Font(color=MUTED, italic=True, size=9)
            we.merge_cells(start_row=er, start_column=8, end_row=er, end_column=LAST)
            info["rows_without_images"] += 1
            we.row_dimensions[er].height = min(409, max(48, text_h))
        c1 = we.cell(row=er, column=1)
        c1.hyperlink = f"#'Test Results'!A{result_row[x['id']]}"
        c1.font = Font(color="1D4ED8", underline="single", size=10)
        ec = wr.cell(row=result_row[x["id"]], column=9)
        ec.data_type = "s"
        ec.hyperlink = f"#'Screenshot Evidence'!A{er}"
        ec.value = "Screenshot Evidence › " + x["test_id"]
        ec.font = Font(color="1D4ED8", underline="single", size=10)
        er += 1
    er += 1
    if page_sets:
        put(we, er, 1, "Full-page evidence", bold=True, fill=INK, color="FFFFFF", border=False)
        we.merge_cells(start_row=er, start_column=1, end_row=er, end_column=LAST)
        er += 1
        for (d, a, df), tests in page_sets.items():
            put(we, er, 1, "Used by: " + ", ".join(tests[:12]) + (f" (+{len(tests) - 12} more)" if len(tests) > 12 else ""), border=False)
            we.merge_cells(start_row=er, start_column=1, end_row=er, end_column=LAST)
            er += 1
            for label, col in (("Expected design (full page)", 8), ("Actual website (full page)", 9), ("Difference (full page)", 10)):
                put(we, er, col, label, bold=True, fill="F1F5F9")
            er += 1
            tallest = 0
            for evid, col in ((d, 8), (a, 9), (df, 10)):
                if not evid:
                    put(we, er, col, "Not available", border=False).font = Font(color=MUTED, italic=True, size=9)
                    continue
                try:
                    data, _ = repo.evidence_bytes(evid)
                    w, h = _embed(we, data, f"{get_column_letter(col)}{er}", PAGE_W, PAGE_H)
                    info["embedded_images"] += 1
                    tallest = max(tallest, h)
                except Exception:
                    put(we, er, col, "Evidence unavailable: the stored image could not be read", border=False)
            er += max(1, int(tallest / 20) + 2)       # the images float over rows of the default 20px height: reserve them
    info["sheets"].append(we.title)

    # --------------------------------------------------------------- 4 ClickUp Tickets
    wt = wb.create_sheet("ClickUp Tickets")
    th = ["Test ID", "Result", "Ticket title", "ClickUp task ID", "ClickUp task URL", "Task status", "Created by", "Created date", "Ticket creation outcome"]
    tw = [17, 14, 54, 18, 40, 16, 18, 22, 30]
    for i, (h, w) in enumerate(zip(th, tw), start=1):
        put(wt, 1, i, h, bold=True, fill=INK, color="FFFFFF")
        wt.column_dimensions[get_column_letter(i)].width = w
    wt.freeze_panes = "A2"
    by_id = {x["id"]: x for x in rows}
    n = 2
    for lk in links:
        x = by_id.get(lk["result_id"], {})
        vals = [x.get("test_id", ""), x.get("result", ""), lk["task_title"], lk["task_id"], lk["task_url"], lk["task_status"], lk["created_by"], lk["created_at"],
                lk["outcome"] + (f" — {lk['error']}" if lk["error"] else "") + ("; attachments: " + ", ".join(f"{a['name']} {a['status']}" for a in lk["attachments"]) if lk["attachments"] else "")]
        for i, v in enumerate(vals, start=1):
            put(wt, n, i, v)
        link(wt.cell(row=n, column=5), lk["task_url"])
        n += 1
    if not links:
        put(wt, 2, 1, "No ClickUp tickets were created from this run.", border=False)
        wt.merge_cells(start_row=2, start_column=1, end_row=2, end_column=9)
    info["sheets"].append(wt.title)

    # --------------------------------------------------------------- 5 Execution Details
    wd = wb.create_sheet("Execution Details")
    wd.column_dimensions["A"].width = 34
    wd.column_dimensions["B"].width = 110
    units = summary.get("units") or []
    tested = [u for u in units if u.get("status") == "Tested"]
    asset = None
    try:
        asset = registry.get(run["asset_id"]) if run["asset_id"] else None
    except Exception:
        asset = None
    from .analysis import analyse
    ana = analyse(asset) if asset else None
    tol = Tolerances.from_dict(cfg.get("tolerances"))
    tol_text = ", ".join(f"{k}={getattr(tol, k)}" for k in tol.__dataclass_fields__) + (" (customised)" if cfg.get("tolerances") else " (defaults)")
    nv_rows = [x for x in rows if x["result"] in (R.NOT_VERIFIABLE, R.NOT_APPLICABLE, R.NOT_EXECUTED)]
    items = [
        ("Run ID", run_id), ("Design source", f"{run['source_file']} ({(asset or {}).get('asset_kind', 'unknown')}), version {run['version']}"),
        ("Design analysis", (ana or {}).get("summary", "")),
        ("Design analysis limitations", "\n".join((ana or {}).get("limitations", [])) or "none recorded"),
        ("Website URL", _target(run)), ("Browser used", run["browser"] + " (Playwright, headless, read-only)"),
        ("Actual viewport(s)", "; ".join(f"{u.get('screen', '')}: {u['viewport_px'][0]}x{u['viewport_px'][1]}" + (" (default desktop viewport: the design gives no reliable size)" if u.get("reference_only") else "")
                                         for u in tested if u.get("viewport_px")) or ", ".join(run["viewports"]) or "n/a"),
        ("Categories selected", ", ".join(UC.LABELS.get(k, k) for k in cfg.get("user_categories", [])) or "n/a"),
        ("Tolerances used", tol_text), ("Run start", started), ("Run end", ended), ("Execution status", run["status"] + (f" — {run['status_detail']}" if run["status_detail"] else "")),
        ("Screens tested / not tested", f"{len(tested)} tested; {len(units) - len(tested)} not tested" + ("".join(f"\n- {u.get('screen')}: {u.get('message', '')}" for u in units if u.get("status") != "Tested"))),
        ("Skipped or unsupported checks", f"{len(nv_rows)} row(s) are Not Verifiable / Not Applicable / Not Executed:\n" + "\n".join(f"- [{x['result']}] {x['category_label']}: {x['reason'][:200]}" for x in nv_rows[:60]) if nv_rows else "none"),
        ("Evidence provenance", "Actual-website screenshots were captured by Chromium during this run. Expected-design images are the stored design image or crops of it. Expected/actual/difference crops "
                                "were cut from those stored images around the measured element when the run executed; difference images are the pixel-comparison overlay produced by the same run. "
                                "Nothing was regenerated for this export."),
        ("Engine version", run.get("engine_version", "")),
    ]
    rr = 1
    for k, v in items:
        put(wd, rr, 1, k, bold=True, fill="F1F5F9")
        put(wd, rr, 2, v)
        rr += 1
    info["sheets"].append(wd.title)

    out = io.BytesIO()
    wb.save(out)
    return out.getvalue(), info


def _target(run: dict) -> str:
    from .clickup import page_url
    return page_url(run) or run.get("app_url", "")
