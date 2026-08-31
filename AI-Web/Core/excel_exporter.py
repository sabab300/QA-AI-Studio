"""
QA AI Studio
Excel Exporter

Version: 1.0
"""
import re

from openpyxl.styles import Font
from pathlib import Path
from openpyxl import Workbook
from Core.logger import Logger


class ExcelExporter:

    def __init__(self):

        self.logger = Logger.get_logger()

    def export_test_cases(

        self,

        rows,

        output_file

    ):

        wb = Workbook()

        ws = wb.active

        ws.title = "Test Cases"

        headers = [

            "S.No",
            "Name / Scenario / Requirement",
            "Importance (High/Medium/Low)",
            "Test Type",
            "Test Case",
            "Pre-Conditions",
            "Steps",
            "Expected Result",
            "Actual Result"

        ]

        ws.append(headers)

        for cell in ws[1]:

            cell.font = Font(bold=True)

        for index, row in enumerate(rows, start=1):

            ws.append([

                index,

                row.get(
                    "scenario",
                    ""
                ),

                row.get(
                    "importance",
                    ""
                ),

                row.get(
                    "test_type",
                    ""
                ),

                row.get(
                    "test_case",
                    ""
                ),

                row.get(
                    "pre_conditions",
                    ""
                ),

                row.get(
                    "steps",
                    ""
                ),

                row.get(
                    "expected_result",
                    ""
                ),

                ""

            ])

        Path(output_file).parent.mkdir(

            parents=True,

            exist_ok=True

        )

        wb.save(output_file)

        self.logger.info(

            f"Excel exported: {output_file}"

        )

        return output_file
    
    # --------------------------------------------------
    # Parse LLM Output
    # --------------------------------------------------

    def parse_llm_output(
        self,
        text
    ):

        rows = []

        lines = text.splitlines()

        for line in lines:

            line = line.strip()

            if not line.startswith("|"):
                continue

            if "---" in line:
                continue

            columns = [

                item.strip()

                for item in line.strip("|").split("|")

            ]

            if len(columns) < 7:
                continue

            if columns[0].lower().startswith("name"):
                continue

            rows.append({

                "scenario": columns[0],

                "importance": columns[1],

                "test_type": columns[2],

                "test_case": columns[3],

                "pre_conditions": columns[4],

                "steps": columns[5],

                "expected_result": columns[6]

            })

        self.logger.info(
            f"Parsed {len(rows)} test cases."
        )

        return rows


    def _value(

        self,

        block,

        field

    ):

        pattern = rf"{field}\s*:\s*(.*?)(?=\n[A-Za-z ]+\s*:|\Z)"

        match = re.search(

            pattern,

            block,

            re.I | re.S

        )

        if match:

            return match.group(1).strip()

        return ""


    def _detect_type(

        self,

        block

    ):

        text = block.lower()

        keywords = [

            "invalid",

            "negative",

            "fail",

            "error",

            "blank",

            "missing"

        ]

        for keyword in keywords:

            if keyword in text:

                return "Negative"

        return "Positive"