"""Read-only Impact Analysis exports from one authoritative stored result.

The generic test-case exporters use a different schema; reusing their
reportlab/openpyxl dependencies here keeps QA artifact exports unchanged.
"""
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from Core.impact_report_builder import ImpactReportBuilder
from Core import impact_management


def export_impact_analysis(result, format):
    if format not in ("xlsx", "pdf"):
        raise ValueError("Unsupported Impact Analysis export format")
    report = ImpactReportBuilder().build(result)
    if report == "Impact Analysis report could not be formatted.":
        raise ValueError(report)
    if format == "pdf":
        return _pdf(report), "application/pdf"
    return _xlsx(result, report), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _xlsx(result, report):
    import json
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    wb.remove(wb.active)

    def sheet(name, headers, rows):
        ws = wb.create_sheet(name)
        ws.append(headers)
        for row in rows:
            # Never silently truncate Excel's cell limit. Continuation rows
            # retain the first-column identifier and every character.
            values = [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in row]
            parts = max([1] + [(len(v) + 29999) // 30000 for v in values if isinstance(v, str)])
            for part in range(parts):
                ws.append([v[part * 30000:(part + 1) * 30000] if isinstance(v, str) and len(v) > 30000 else v if part == 0 or i == 0 else "" for i, v in enumerate(values)])
                for cell in ws[ws.max_row]:
                    if isinstance(cell.value, str):
                        cell.data_type = "s"  # source text must never execute as an Excel formula
                    cell.alignment = Alignment(vertical="top", wrap_text=True)
        for cell in ws[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="17365D")
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for col in ws.columns:
            ws.column_dimensions[col[0].column_letter].width = 24 if col[0].column == 1 else 55
        return ws

    coverage = result.get("coverage") or {}
    summary = [[k, v] for k, v in (result.get("summary") or {}).items()]
    summary += [["Source", result.get("source")], ["Target", result.get("target")]]
    summary += [[k, v] for k, v in coverage.items() if k not in ("source_requirements", "warnings", "warning_details", "automation")]
    block = result.get("management") or impact_management.management_block(result, "impact_analysis")
    summary += [["Decision readiness", block["decision"]["state"]]]
    automation = coverage.get("automation") or {}
    summary += [["Automation impact", automation.get("status")], ["Automation note", automation.get("note")]]
    sheet("Summary", ["Measurement", "Value"], summary)
    findings = result.get("findings") or []
    sheet("Findings", ["Finding ID", "Requirement IDs", "Source requirement", "Verified source quotations", "Potential functionality (not confirmation)", "Target impact", "Target processing", "Version status", "Existing target behaviour", "Required target change", "Test coverage", "Test status note"],
          [[f.get("finding_id"), f.get("requirement_ids", []), (f.get("source_change") or {}).get("name"), "\n".join(f.get("source_evidence") or []),
            (f.get("potential_affected_functionality") or {}).get("description"), f.get("classification"), f.get("target_assessment"), f.get("change_status"),
            f.get("existing_target_behaviour"), f.get("required_target_change"), (f.get("test_case_coverage") or {}).get("classification"), (f.get("test_case_coverage") or {}).get("note")] for f in findings])
    auto_rows = []
    for f in findings:
        ai = f.get("automation_impact") or {}
        if ai.get("status") in (None, "NOT_APPLICABLE"):
            continue
        if not ai.get("tests"):
            auto_rows.append([f.get("finding_id"), ai.get("status"), None, None, None, None, None, None, None, "No", None, ai.get("note")])
        for e in ai.get("tests") or []:
            base = [f.get("finding_id"), ai.get("status"), e.get("test"), e.get("test_case_version_id"), e.get("scenario")]
            if not e.get("assets"):
                auto_rows.append(base + ["No automation of its own", None, None, None, None, None, ai.get("note")])
            for a in e.get("assets") or []:
                auto_rows.append(base + [a.get("kind") + " - " + str(a.get("label")), a.get("state"), a.get("basis"), ", ".join(a.get("matched_terms") or []), "Yes" if a.get("text_checked") else "No", "Scenario", ai.get("note")])
            for a in e.get("version_assets") or []:
                auto_rows.append(base + [a.get("kind") + " - " + str(a.get("label")), "VERSION LEVEL (not this scenario's own)", a.get("basis"), ", ".join(a.get("matched_terms") or []), "Yes", "Version", ai.get("note")])
    sheet("Automation Impact", ["Finding ID", "Automation status", "Existing test", "Test case version", "Scenario", "Asset", "Link state", "Basis of link", "Changed terms found in asset", "Asset text checked", "Level", "Note"], auto_rows)
    matched_rows = []
    for f in findings:
        cov = f.get("test_case_coverage") or {}
        for kind, items in (("Counted", cov.get("matches") or []), ("Not counted (older workbook version)", cov.get("superseded_matches") or [])):
            for m in items:
                matched_rows.append([f.get("finding_id"), m.get("test_case_id"), m.get("scenario"), m.get("knowledge_name"), ", ".join(str(i) for i in m.get("source_knowledge_ids") or []),
                                     m.get("workbook_version"), m.get("latest_workbook_version"), m.get("document_version_id"), m.get("scope_status"), m.get("identity_basis"),
                                     "Yes" if m.get("in_target_knowledge") else "No", kind])
    sheet("Matched Tests", ["Finding ID", "Test case", "Scenario", "Knowledge", "Source id", "Workbook version", "Current workbook version", "Document version", "Version status", "Identity basis", "In target", "Counted"], matched_rows)
    removed_rows = []
    for r in result.get("removed_requirements") or []:
        cov, ai = r.get("test_case_coverage") or {}, r.get("automation_impact") or {}
        matches = cov.get("matches") or []
        assets = [a for e in ai.get("tests") or [] for a in e.get("assets") or []]
        removed_rows.append([r.get("statement"), cov.get("classification"), ", ".join(str(m.get("test_case_id")) for m in matches),
                             ", ".join("v%s/source %s" % (m.get("workbook_version"), ", ".join(str(i) for i in m.get("source_knowledge_ids") or [])) for m in matches),
                             ", ".join(str(a.get("label")) for a in assets), ai.get("status")])
    sheet("Removed Requirements", ["Removed requirement", "Test coverage", "Matching current tests", "Workbook version / source", "Automation of those tests", "Automation status"], removed_rows)
    inventory = ((coverage.get("source_requirements") or {}).get("inventory") or {})
    sheet("Source Requirements", ["Requirement ID", "Kind", "Chunk", "Statement", "Assessment", "Finding IDs", "Version statuses", "Duplicate of"],
          [[i.get("statement_id"), i.get("kind"), i.get("chunk"), i.get("text"), i.get("assessment"), i.get("finding_ids", []), i.get("version_statuses", []), i.get("duplicate_of")] for i in inventory.get("items", []) + inventory.get("duplicates", [])])
    evidence = []
    for f in findings:
        for e in f.get("source_evidence_detail") or []:
            evidence.append([f.get("finding_id"), "Source", e.get("file_name"), e.get("version"), e.get("chunk_number"), e.get("quote"), e.get("verified")])
        if not f.get("source_evidence_detail"):
            evidence += [[f.get("finding_id"), "Source (historical)", None, None, None, q, None] for q in f.get("source_evidence") or []]
        e = f.get("supporting_quote") or {}
        if e:
            evidence.append([f.get("finding_id"), e.get("evidence_kind"), e.get("file_name"), e.get("version"), e.get("chunk_number"), e.get("text"), e.get("exact")])
    sheet("Evidence", ["Finding ID", "Evidence kind", "Document", "Version", "Chunk", "Quotation", "Verified"], evidence)
    diff = result.get("version_difference") or {}
    rows = [[kind.upper(), i.get("current"), i.get("prior"), i.get("current_chunk"), i.get("prior_chunk"), i.get("polarity", [])] for kind in ("changed", "added", "removed") for i in diff.get(kind, [])]
    sheet("Version Comparison", ["Kind", "Current", "Prior", "Current chunk", "Prior chunk", "Setting differences"], rows)
    sheet("Coverage Warnings", ["Code", "Finding ID", "Message"], [[w.get("code"), w.get("finding_id"), w.get("message")] for w in coverage.get("warning_details", [])] or [["Historical warning", "", w] for w in coverage.get("warnings", [])])
    effort = block["effort"]
    management_rows = [["Decision readiness", block["decision"]["state"], "", ""]]
    management_rows += [["Reason", r, "", ""] for r in block["decision"]["reasons"] + block["decision"]["also_reasons"]]
    management_rows += [["Management question", q, a, ""] for q, a in block["questions"]]
    management_rows += [["Manual baseline - " + r["label"], r["units"], r["minutes_per_unit"], r["manual_minutes"]] for r in effort["manual"]["rows"]]
    management_rows += [["Manual baseline total (minutes)", effort["manual"]["minutes"], effort["manual"]["label"], ""],
                        ["AI processing (minutes)", effort["ai_assisted"]["ai_processing_minutes"], effort["ai_assisted"]["ai_processing_label"], ""],
                        ["Human review allowance (minutes)", effort["ai_assisted"]["human_review_minutes"], effort["ai_assisted"]["human_review_label"], ""],
                        ["AI-assisted total (minutes)", effort["ai_assisted"]["total_minutes"], "", ""],
                        ["Time saved (minutes)", effort["saving"]["minutes"], effort["saving"]["label"], effort["saving"]["percent"]],
                        ["Cost saved", effort["cost"].get("amount"), effort["cost"].get("currency"), effort["cost"].get("label")]]
    management_rows += [["Measured - " + k.replace("_", " "), v.get("value", v.get("value_minutes")), v.get("label"), ""] for k, v in effort["measured"].items()]
    management_rows += [["Not measured", n, "", ""] for n in effort["not_measured"]] + [["Formula", effort["formula"], "", ""], ["Baseline status", effort["baseline_status"], effort["baseline_note"], ""]]
    sheet("Management", ["Item", "Value", "Detail", "Extra"], management_rows)
    sheet("Full Report", ["Line", "Report text"], [[i, line] for i, line in enumerate(report.splitlines(), 1)])
    output = BytesIO()
    wb.save(output)
    return output.getvalue()


def _pdf(report):
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    font = "Helvetica"
    for path in (Path("C:/Windows/Fonts/arial.ttf"), Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")):
        if path.is_file():
            font = "ImpactReportUnicode"
            if font not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(font, str(path)))
            break
    styles = getSampleStyleSheet()
    body = ParagraphStyle("ImpactBody", fontName=font, fontSize=9, leading=12, spaceAfter=4, splitLongWords=True)
    heading = ParagraphStyle("ImpactHeading", parent=styles["Heading2"], fontName=font, fontSize=11, leading=15)
    elements = []
    for line in report.splitlines():
        if not line.strip():
            elements.append(Spacer(1, 4))
        elif set(line.strip()) != {"-"}:
            elements.append(Paragraph(escape(line), heading if line.isupper() and len(line) < 80 else body))
    output = BytesIO()
    def footer(canvas, doc):
        canvas.setFont(font, 8)
        canvas.drawString(36, 20, "QA AI Studio | Impact Analysis")
        canvas.drawRightString(A4[0] - 36, 20, "Page %d" % doc.page)
    SimpleDocTemplate(output, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=36, bottomMargin=36,
                      title="QA AI Studio - Impact Analysis").build(elements, onFirstPage=footer, onLaterPages=footer)
    return output.getvalue()
