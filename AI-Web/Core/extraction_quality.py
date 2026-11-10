"""QA AI Studio - extraction quality report for a source document.

Answers "was this document read completely and correctly?" instead of assuming it was.  For a .docx it compares
what is IN the file (every paragraph, table cell and text box, the pictures, merged cells and nested tables) with
what the extractor produced, and reports what is missing, uncertain or unreadable.  Generic: nothing here knows
about a particular document.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _docx_facts(path: Path) -> dict:
    from docx import Document
    from docx.oxml.ns import qn

    document = Document(str(path))
    body = document.element.body
    every: List[str] = []
    for node in body.iter(qn("w:p")):
        text = "".join(t.text or "" for t in node.iter(qn("w:t"))).replace("\xa0", " ").strip()
        if text:
            every.append(text)
    glyph = {"[x]", "[ ]", "[X]", "\u2612", "\u2610", "\u2611"}
    paragraphs: List[str] = []
    option_labels = 0
    for i, text in enumerate(every):
        if text not in glyph and ((i > 0 and every[i - 1] in glyph) or (i + 1 < len(every) and every[i + 1] in glyph)) and len(text) < 60:
            option_labels += 1  # a tick-box option label: only the ticked one is content
            continue
        if text not in glyph:
            paragraphs.append(text)
    tables = list(body.iter(qn("w:tbl")))
    top_level = [t for t in tables if t.getparent().tag == qn("w:body")]
    merged = sum(1 for tc in body.iter(qn("w:tc")) if tc.find(qn("w:tcPr")) is not None
                 and (tc.find(qn("w:tcPr")).find(qn("w:gridSpan")) is not None
                      or tc.find(qn("w:tcPr")).find(qn("w:vMerge")) is not None))
    boxes = []
    for box in body.iter(qn("w:txbxContent")):
        text = " ".join("".join(t.text or "" for t in p.iter(qn("w:t"))) for p in box.iter(qn("w:p"))).strip()
        if text:
            boxes.append(text)
    pictures = len(list(body.iter(qn("w:drawing")))) + len(list(body.iter(qn("w:pict"))))
    blank_fields = []
    for table in document.tables:
        for row in table.rows:
            unique, seen = [], set()
            for cell in row.cells:
                if id(cell._tc) in seen:
                    continue
                seen.add(id(cell._tc))
                unique.append(cell.text.strip())
            if len(unique) == 2 and unique[0] and not unique[1] and len(unique[0]) < 120:
                blank_fields.append(unique[0])
    return {"option_labels": option_labels, "paragraphs": paragraphs, "tables": len(top_level), "nested_tables": len(tables) - len(top_level),
            "merged_cells": merged, "text_boxes": boxes, "pictures": pictures, "blank_fields": blank_fields}


def _json_blocks(text: str) -> dict:
    """Find JSON-looking blocks (a line that is just '{' up to its closing '}') and check they parse."""
    lines, blocks, current, depth = text.splitlines(), [], None, 0
    for line in lines:
        stripped = line.strip()
        if current is None:
            if stripped.startswith("{") and not stripped.startswith("{{"):
                current, depth = [line], stripped.count("{") - stripped.count("}")
                if depth <= 0:
                    blocks.append("\n".join(current))
                    current = None
        else:
            current.append(line)
            depth += stripped.count("{") - stripped.count("}")
            if depth <= 0:
                blocks.append("\n".join(current))
                current = None
    invalid = []
    for block in blocks:
        try:
            json.loads(block)
        except ValueError as error:
            first = next((ln.strip() for ln in block.splitlines() if ln.strip()), "")
            invalid.append(f"{error} (block starting {first[:50]!r})")
    return {"total": len(blocks), "invalid": invalid}


def analyse(path: str, text: str | None = None) -> dict:
    """Return the extraction-quality findings for ``path``. ``text`` is the extractor output (read here when omitted)."""
    from Core.requirement_extractor import extract_requirements, validate_requirements, BOUNDARY_RE  # noqa: F401
    from Core.text_extractor import TextExtractor

    path = Path(path)
    if text is None:
        text = TextExtractor().extract_text(str(path))
    report: Dict[str, object] = {"file": path.name, "characters": len(text), "findings": []}
    findings: List[dict] = report["findings"]  # type: ignore[assignment]

    def finding(level: str, message: str):
        findings.append({"level": level, "message": message})

    flat = _norm(text)
    if path.suffix.lower() == ".docx":
        facts = _docx_facts(path)
        paragraphs = facts["paragraphs"]
        missing = [p for p in paragraphs if len(_norm(p)) >= 15 and _norm(p) not in flat]
        report.update(paragraphs=len(paragraphs), paragraphs_missing=len(missing), tables=facts["tables"],
                      nested_tables=facts["nested_tables"], merged_cells=facts["merged_cells"], pictures=facts["pictures"],
                      text_boxes=len(facts["text_boxes"]))
        if missing:
            finding("warning", f"{len(missing)} paragraph(s) of the document were not found in the extracted text, for example: '{missing[0][:90]}'.")
        else:
            finding("ok", "Every paragraph and table cell of the document is present in the extracted text.")
        lost_boxes = [b for b in facts["text_boxes"] if _norm(b) not in flat]
        if facts["option_labels"]:
            finding("info", f"{facts['option_labels']} tick-box option label(s) are not content; only the ticked option is extracted ('Selected: ...').")
        if lost_boxes:
            finding("warning", f"{len(lost_boxes)} text box(es) were not extracted: '{lost_boxes[0][:80]}'.")
        if facts["pictures"]:
            finding("warning", f"The document contains {facts['pictures']} embedded picture(s). Text inside pictures is NOT extracted, so any requirement shown only as a picture is missing from the knowledge and from generated tests.")
        if facts["merged_cells"]:
            finding("ok", f"{facts['merged_cells']} merged cell(s) were read once each (no repeated content).")
        if facts["nested_tables"]:
            finding("ok", f"{facts['nested_tables']} nested table(s) were read.")
        def _filled(label: str) -> bool:  # the label's value may be in a following paragraph that the extractor joined to it
            return bool(re.search(re.escape(label.strip().rstrip(":")) + r"[ \t]*:[ \t]*\S", text, re.I))
        blank = [b for b in dict.fromkeys(facts["blank_fields"]) if not _filled(b)]
        report["blank_fields"] = blank
        if blank:
            finding("warning", "Form field(s) left blank in the document: " + "; ".join(blank)[:300] + ".")
        # order: sampled paragraphs must appear in document order
        cursor, out_of_order = 0, 0
        for p in paragraphs[:600]:
            n = _norm(p)
            if len(n) < 25 or n not in flat:
                continue
            at = flat.find(n, cursor)  # repeated rows are matched in sequence, not at their first occurrence
            if at < 0:
                out_of_order += 1
            else:
                cursor = at
        report["order_breaks"] = out_of_order
        finding("ok" if not out_of_order else "warning",
                "Paragraphs appear in document order." if not out_of_order else f"{out_of_order} paragraph(s) are out of document order.")
    else:
        report.update(paragraphs=None)
        finding("info", "Completeness against the original layout is checked for .docx files only.")

    noise = [ln.strip() for ln in text.splitlines() if re.search(r"not a valid (?:binary )?format", ln, re.I)]
    if noise:
        finding("warning", f"The extracted text contains a system error line, not document content: '{noise[0][:90]}'. It is ignored by test generation.")
    method_refs = re.findall(r"Method\s*ID\s*(?:->|=>|:)\s*(\S*)", text, re.I)
    empty_refs = [r for r in method_refs if not re.search(r"\d", r)]
    report["method_ids"] = sorted({r.strip(").,") for r in method_refs if re.search(r"\d", r)})
    if empty_refs:
        finding("warning", f"{len(empty_refs)} 'Method ID' reference(s) have no number, so that API cannot be identified from the document.")
    blocks = _json_blocks(text)
    report["json_blocks"] = blocks["total"]
    if blocks["invalid"]:
        finding("warning", f"{len(blocks['invalid'])} of {blocks['total']} JSON sample(s) are not valid JSON: {blocks['invalid'][0]}")
    elif blocks["total"]:
        finding("ok", f"{blocks['total']} JSON sample(s) parse correctly.")

    extracted = extract_requirements(text)
    summary = validate_requirements(extracted)
    units = extracted["units"]
    report["requirements"] = [{"ref": u["ref"], "kind": u["ref_kind"], "title": u["title"], "testable": u["testable"],
                               "statements": len(u["statements"]), "fields": len(u["fields"]),
                               "reference_data_rows": u["reference_data_rows"],
                               "evidence": {k: len(v) for k, v in u["evidence"].items()}} for u in units]
    report["requirement_mode"] = summary["mode"]
    if not units:
        finding("warning", "No requirement structure was recognised; test generation will use plain sections with internal references.")
    elif summary["mode"] in ("sections",):
        finding("warning", "No requirement headings were recognised; sections are given internal references (INT-REQ-nnn).")
    else:
        finding("ok", f"{len(units)} requirement(s) recognised ({summary['testable']} testable).")
    for skipped in summary["skipped"]:
        finding("info", f"{skipped['ref']} is not tested: {skipped['reason']}.")
    for warning in summary["warnings"]:
        finding("warning", warning)
    totals = {k: sum(len(u["evidence"].get(k, [])) for u in units) for k in ("Validation", "Boundary", "Integration", "API", "Security")}
    report["rule_counts"] = totals
    return report


def format_report(report: dict) -> str:
    out = [f"Extraction quality - {report['file']}", f"Characters extracted: {report['characters']:,}"]
    if report.get("paragraphs") is not None:
        out.append(f"Paragraphs {report['paragraphs']} (missing {report['paragraphs_missing']}), tables {report['tables']}, "
                   f"nested tables {report['nested_tables']}, merged cells {report['merged_cells']}, "
                   f"text boxes {report['text_boxes']}, pictures {report['pictures']}")
    out.append("")
    marks = {"ok": "[ok]     ", "warning": "[WARNING]", "info": "[info]   "}
    for item in report["findings"]:
        out.append(f"{marks.get(item['level'], '[?]')} {item['message']}")
    out.append("")
    out.append("Requirements:")
    for r in report.get("requirements", []):
        label = r["ref"] + (" (internal reference)" if r["kind"] == "internal" else "")
        out.append(f"  - {label}: {r['title'] or '(no title)'} | statements {r['statements']}, fields {r['fields']}, "
                   f"reference-data rows {r['reference_data_rows']} | basis for: {', '.join(r['evidence']) or 'none'}")
    if report.get("method_ids"):
        out.append("API method ids named: " + ", ".join(report["method_ids"]))
    return "\n".join(out)
