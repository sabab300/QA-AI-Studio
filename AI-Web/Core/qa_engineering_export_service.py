"""Server-side Test Case exports and local export-location configuration."""

import csv
import html
import json
import re
from datetime import datetime
from pathlib import Path

from openpyxl import Workbook


AI_WEB_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXPORT_DIR = AI_WEB_ROOT / "Output" / "Test Cases"
CONFIG_FILE = AI_WEB_ROOT / "Config" / "qa_engineering_export.json"
EXPORT_FIELDS = [
    ("tc_number", "Test Case ID"), ("domain", "Domain"),
    ("module", "Module"), ("knowledge_name", "Knowledge"),
    ("version", "Version"), ("scenario", "Scenario"),
    ("test_type", "Test Types"), ("pre_conditions", "Preconditions"),
    ("test_case", "Test Case"), ("steps", "Test Steps"),
    ("expected_result", "Expected Result"), ("importance", "Importance"),
    ("execution_type", "Execution Type"),
    ("execution_tool", "Execution Tool"),
]


class QaEngineeringExportService:
    def get_location(self, relative_folder=None):
        if not relative_folder:
            return DEFAULT_EXPORT_DIR.resolve()
        return self._validate_location((AI_WEB_ROOT / "Output" / relative_folder).resolve())

    def list_locations(self):
        output_root = (AI_WEB_ROOT / "Output").resolve()
        DEFAULT_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        return [
            {"value": str(path.relative_to(output_root)), "path": str(path)}
            for path in sorted((item for item in output_root.rglob("*") if item.is_dir()), key=lambda item: str(item).lower())
        ]

    def set_location(self, value):
        location = self._validate_location(value)
        location.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps({"path": str(location)}, indent=2), encoding="utf-8")
        return location

    def export(self, rows, file_format, scope, file_name=None, relative_folder=None, allow_empty=False):
        fmt = file_format.lower()
        if fmt not in {"xlsx", "csv", "pdf", "html"}:
            raise ValueError("Unsupported export format.")
        if not rows and not allow_empty:
            raise ValueError("No saved test cases match the selected scope or filter.")
        folder = self.get_location(relative_folder)
        folder.mkdir(parents=True, exist_ok=True)
        default_stem = "TC_" + "_".join(
            str(scope.get(key) or "NA")
            for key in ("domain", "module", "knowledge_name", "version", "document_type")
        )
        requested = Path(file_name).name if file_name else default_stem
        suffix = f".{fmt}"
        if requested.lower().endswith(suffix):
            requested = requested[:-len(suffix)]
        stem = self._safe_name(requested)
        path = self._available_path(folder, stem, fmt)
        if fmt == "xlsx":
            self._xlsx(rows, path)
        elif fmt == "csv":
            self._csv(rows, path)
        elif fmt == "pdf":
            self._pdf(rows, path, scope)
        else:
            self._html(rows, path, scope)
        if not path.is_file() or path.stat().st_size == 0:
            raise RuntimeError("Export file was not created successfully.")
        relative_path = str(path.relative_to((AI_WEB_ROOT / "Output").resolve()))
        return {"format": fmt.upper(), "file_name": path.name,
                "relative_path": relative_path,
                "saved_location": str(folder), "count": len(rows)}

    def resolve_download(self, file_name):
        if Path(file_name).name != file_name:
            raise ValueError("Invalid export file name.")
        path = (self.get_location() / file_name).resolve()
        if path.parent != self.get_location().resolve() or not path.is_file():
            raise ValueError("Export file not found.")
        return path

    def resolve_relative_download(self, relative_path):
        path = ((AI_WEB_ROOT / "Output") / str(relative_path)).resolve()
        allowed_root = (AI_WEB_ROOT / "Output").resolve()
        if allowed_root not in path.parents or not path.is_file():
            raise ValueError("Export file not found.")
        return path

    @staticmethod
    def _validate_location(value):
        path = Path(str(value)).expanduser()
        if not path.is_absolute():
            path = AI_WEB_ROOT / path
        path = path.resolve()
        allowed_root = (AI_WEB_ROOT / "Output").resolve()
        if path != allowed_root and allowed_root not in path.parents:
            raise ValueError("Export location must remain under AI-Web/Output.")
        if path.exists() and not path.is_dir():
            raise ValueError("Export location must be a directory.")
        return path

    @staticmethod
    def _safe_name(value):
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")
        return cleaned[:140] or "Test_Cases"

    @staticmethod
    def _available_path(folder, stem, extension):
        candidate = folder / f"{stem}.{extension}"
        index = 1
        while candidate.exists():
            candidate = folder / f"{stem}_{index:02d}.{extension}"
            index += 1
        return candidate

    @staticmethod
    def _xlsx(rows, path):
        book, sheet = Workbook(), None
        sheet = book.active
        sheet.title = "Test Cases"
        sheet.append([label for _, label in EXPORT_FIELDS])
        for row in rows:
            sheet.append([row.get(key, "") for key, _ in EXPORT_FIELDS])
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        book.save(path)

    @staticmethod
    def _csv(rows, path):
        with path.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow([label for _, label in EXPORT_FIELDS])
            writer.writerows([[row.get(key, "") for key, _ in EXPORT_FIELDS] for row in rows])

    @staticmethod
    def _html(rows, path, scope):
        esc = lambda value: html.escape(str(value or ""))
        headings = "".join(f"<th>{esc(label)}</th>" for _, label in EXPORT_FIELDS)
        body = "".join("<tr>" + "".join(f"<td>{esc(row.get(key))}</td>" for key, _ in EXPORT_FIELDS) + "</tr>" for row in rows)
        created = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        scope_text = " / ".join(esc(scope.get(key)) for key in ("domain", "module", "knowledge_name") if scope.get(key))
        document = f"""<!doctype html><html><head><meta charset="utf-8"><title>QA AI Studio Test Cases</title>
<style>body{{font:13px Arial;margin:24px;color:#172033}}h1{{margin:0 0 6px}}.meta{{color:#596579;margin-bottom:16px}}table{{border-collapse:collapse;width:100%}}th,td{{border:1px solid #ccd3df;padding:6px;text-align:left;vertical-align:top;white-space:pre-wrap}}th{{background:#24364f;color:white;position:sticky;top:0}}tr:nth-child(even){{background:#f6f8fb}}@media print{{th{{position:static}}body{{margin:8mm}}}}</style></head>
<body><h1>QA AI Studio — Test Cases</h1><div class="meta">Exported {esc(created)} · {scope_text} · {len(rows)} test case(s)</div><table><thead><tr>{headings}</tr></thead><tbody>{body}</tbody></table></body></html>"""
        path.write_text(document, encoding="utf-8")

    @staticmethod
    def _pdf(rows, path, scope):
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

        styles = getSampleStyleSheet()
        cell = ParagraphStyle("QECell", parent=styles["BodyText"], fontSize=8, leading=10)
        doc = SimpleDocTemplate(str(path), pagesize=A4, leftMargin=12*mm, rightMargin=12*mm,
                                topMargin=12*mm, bottomMargin=12*mm)
        story = [Paragraph("QA AI Studio — Test Cases", styles["Title"]),
                 Paragraph(html.escape(" / ".join(str(scope.get(k) or "") for k in ("domain","module","knowledge_name"))), styles["Normal"]),
                 Paragraph(f"Exported {datetime.now():%Y-%m-%d %H:%M:%S} · {len(rows)} test case(s)", styles["Normal"]), Spacer(1, 6)]
        for index, row in enumerate(rows):
            title = html.escape(f"{row.get('tc_number') or index + 1} — {row.get('test_case') or 'Untitled'}")
            story.append(Paragraph(title, styles["Heading2"]))
            data = []
            for key, label in EXPORT_FIELDS[1:]:
                data.append([Paragraph(f"<b>{html.escape(label)}</b>", cell),
                             Paragraph(html.escape(str(row.get(key) or "")).replace("\n", "<br/>"), cell)])
            table = Table(data, colWidths=[34*mm, 146*mm], repeatRows=0)
            table.setStyle(TableStyle([("GRID", (0,0), (-1,-1), .3, colors.HexColor("#cbd3df")),
                                       ("VALIGN", (0,0), (-1,-1), "TOP"),
                                       ("BACKGROUND", (0,0), (0,-1), colors.HexColor("#edf1f6")),
                                       ("LEFTPADDING", (0,0), (-1,-1), 5), ("RIGHTPADDING", (0,0), (-1,-1), 5)]))
            story.extend([table, Spacer(1, 8)])
            if index < len(rows) - 1:
                story.append(PageBreak())
        doc.build(story)
