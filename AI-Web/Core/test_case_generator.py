# Replace: AI/Core/test_case_generator.py

"""
QA AI Studio
Test Case Generator

Version: 6.0

Fix Notes (v6.0):
    • generate() previously IGNORED whatever test types the caller
      wanted and always generated a hardcoded 5 types (Functional,
      Positive, Negative, Boundary, Validation) — the other 9 spec
      types (Integration, API, UI, Database, Security, Performance,
      Compatibility, Regression, Impact Analysis) were unreachable
      from any UI. test_types is now a real parameter.
    • Added output_formats so the caller can request any mix of
      Excel / Word / PDF instead of only ever getting Excel.
"""

from pathlib import Path
from datetime import datetime

from Core.logger import Logger
from Core.rag_engine import RAGEngine
from Core.prompt_builder import PromptBuilder
from Core.llm_engine import LLMEngine
from Core.excel_exporter import ExcelExporter
from Core.word_exporter import WordExporter
from Core.pdf_exporter import PdfExporter
from Core.test_case_repository import TestCaseRepository


# Full test type catalogue from the QA Engineering spec.
# Used whenever the caller doesn't explicitly select a subset.
ALL_TEST_TYPES = [
    "Functional",
    "Positive",
    "Negative",
    "Boundary",
    "Validation",
    "Integration",
    "API",
    "UI",
    "Database",
    "Security",
    "Performance",
    "Compatibility",
    "Regression",
    "Impact Analysis",
]


class TestCaseGenerator:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.rag = RAGEngine()

        self.prompt = PromptBuilder()

        self.llm = LLMEngine()

        self.excel_exporter = ExcelExporter()

        self.word_exporter = WordExporter()

        self.pdf_exporter = PdfExporter()

        self.repository = TestCaseRepository()

    # --------------------------------------------------
    # Generate
    # --------------------------------------------------

    def generate(

        self,

        requirement,

        top_k=5,

        number_of_cases="all",

        domain=None,

        module=None,

        knowledge_name=None,

        version=None,

        test_types=None,

        output_formats=None,

        output_folder="Output/TestCases"

    ):

        try:

            self.logger.info(
                "Generating test cases..."
            )

            if not test_types:

                test_types = ALL_TEST_TYPES

            if not output_formats:

                output_formats = ["Excel"]

            rag = self.rag.get_context(

                question=requirement,

                top_k=top_k,

                domain=domain,

                module=module,

                knowledge_name=knowledge_name,

                version=version

            )

            context = rag.get(
                "context",
                ""
            )

            if not context:

                return {

                    "success": False,

                    "error": "No knowledge found."

                }

            prompt = self.prompt.build_test_case_prompt(

                context=context,

                test_types=test_types,

                number_of_cases=number_of_cases

            )

            result = self.llm.generate(

                prompt=prompt,

                system_prompt=self.prompt.system_prompt(),

                temperature=0.1,

                max_tokens=2048

            )

            if not result.get("success"):

                return result

            output_text = result.get(

                "response",

                ""

            )

            print("\n================== LLM OUTPUT ==================\n")

            print(output_text)

            print("\n================================================\n")

            rows = self.excel_exporter.parse_llm_output(

                output_text

            )

            if not rows:

                return {

                    "success": False,

                    "error": "AI did not return any recognizable test cases. "
                              "Try again or select fewer test types."

                }

            Path(output_folder).mkdir(

                parents=True,

                exist_ok=True

            )

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            base_name = f"Generated_Test_Cases_{timestamp}"

            output_files = {}

            try:

                if "Excel" in output_formats:

                    excel_file = Path(output_folder) / f"{base_name}.xlsx"

                    self.excel_exporter.export_test_cases(
                        rows=rows,
                        output_file=str(excel_file)
                    )

                    output_files["excel_file"] = str(excel_file)


                if "Word" in output_formats:

                    word_file = Path(output_folder) / f"{base_name}.docx"

                    self.word_exporter.export_test_cases(
                        rows=rows,
                        output_file=str(word_file)
                    )

                    output_files["word_file"] = str(word_file)


                if "PDF" in output_formats:

                    pdf_file = Path(output_folder) / f"{base_name}.pdf"

                    self.pdf_exporter.export_test_cases(
                        rows=rows,
                        output_file=str(pdf_file)
                    )

                    output_files["pdf_file"] = str(pdf_file)

            except PermissionError:

                return {

                    "success": False,

                    "error": "Please close the generated output file(s) and try again."

                }

            test_case_ids = []

            # Only persist to the executable Test Case list when the
            # caller has given us a full Domain/Module/Knowledge Name
            # scope (e.g. the QA Engineering screen). Ad-hoc chat-driven
            # generations (AI Assistant) without that scope still get
            # their file output, they just won't show up in QA
            # Automation's Test Execution list.
            if domain and module and knowledge_name:

                try:

                    test_case_ids = self.repository.save_generated_cases(
                        domain=domain,
                        module=module,
                        knowledge_name=knowledge_name,
                        version=version,
                        rows=rows,
                    )

                except Exception:

                    self.logger.exception(
                        "Failed to save generated test cases to the "
                        "database. File export still succeeded."
                    )

            return {

                "success": True,

                "requirement": requirement,

                "test_types": test_types,

                "case_count": len(rows),

                "test_case_ids": test_case_ids,

                **output_files,

                "references": rag.get(

                    "references",

                    []

                ),

                "provider": result.get(

                    "provider"

                ),

                "model": result.get(

                    "model"

                )

            }

        except Exception as error:

            self.logger.exception(

                "Test case generation failed."

            )

            return {

                "success": False,

                "error": str(error)

            }