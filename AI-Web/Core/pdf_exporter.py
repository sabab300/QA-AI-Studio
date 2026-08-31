# Create: AI/Core/pdf_exporter.py

"""
QA AI Studio
PDF Exporter

Version: 1.0

Exports generated test cases to a formatted PDF using reportlab.

NOTE: reportlab is a new dependency — add it to AI/requirements.txt:
    reportlab

Row schema matches ExcelExporter.parse_llm_output() output:
    scenario, importance, test_type, test_case,
    pre_conditions, steps, expected_result
"""

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate,
    Table,
    TableStyle,
    Paragraph,
    Spacer,
)

from Core.logger import Logger


class PdfExporter:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.styles = getSampleStyleSheet()

        self.cell_style = ParagraphStyle(
            "Cell",
            parent=self.styles["Normal"],
            fontSize=8,
            leading=10,
        )

        self.header_style = ParagraphStyle(
            "CellHeader",
            parent=self.styles["Normal"],
            fontSize=8,
            leading=10,
            textColor=colors.white,
            fontName="Helvetica-Bold",
        )

    # --------------------------------------------------

    def export_test_cases(

        self,

        rows,

        output_file,

        title="QA AI Studio — Generated Test Cases"

    ):

        Path(output_file).parent.mkdir(
            parents=True,
            exist_ok=True
        )

        doc = SimpleDocTemplate(
            output_file,
            pagesize=landscape(A4),
            leftMargin=1 * cm,
            rightMargin=1 * cm,
            topMargin=1 * cm,
            bottomMargin=1 * cm,
        )

        elements = []

        elements.append(
            Paragraph(title, self.styles["Title"])
        )

        elements.append(
            Paragraph(
                f"Total Test Cases: {len(rows)}",
                self.styles["Normal"]
            )
        )

        elements.append(Spacer(1, 12))

        headers = [
            "S.No",
            "Scenario / Requirement",
            "Importance",
            "Test Type",
            "Test Case",
            "Pre-Conditions",
            "Steps",
            "Expected Result",
        ]

        table_data = [
            [Paragraph(h, self.header_style) for h in headers]
        ]

        for index, row in enumerate(rows, start=1):

            table_data.append([
                Paragraph(str(index), self.cell_style),
                Paragraph(row.get("scenario", ""), self.cell_style),
                Paragraph(row.get("importance", ""), self.cell_style),
                Paragraph(row.get("test_type", ""), self.cell_style),
                Paragraph(row.get("test_case", ""), self.cell_style),
                Paragraph(row.get("pre_conditions", ""), self.cell_style),
                Paragraph(row.get("steps", ""), self.cell_style),
                Paragraph(row.get("expected_result", ""), self.cell_style),
            ])

        col_widths = [
            1.2 * cm,
            4.5 * cm,
            2.2 * cm,
            2.5 * cm,
            4.5 * cm,
            4 * cm,
            5 * cm,
            5 * cm,
        ]

        table = Table(
            table_data,
            colWidths=col_widths,
            repeatRows=1
        )

        table.setStyle(
            TableStyle([
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1F4E78")),
                ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F2F2")]),
            ])
        )

        elements.append(table)

        doc.build(elements)

        self.logger.info(
            f"PDF exported: {output_file}"
        )

        return output_file