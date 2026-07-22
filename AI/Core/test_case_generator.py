"""
QA AI Studio
Test Case Generator

Version: 5.0
"""

from pathlib import Path

from Core.logger import Logger
from Core.rag_engine import RAGEngine
from Core.prompt_builder import PromptBuilder
from Core.llm_engine import LLMEngine
from Core.excel_exporter import ExcelExporter


class TestCaseGenerator:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.rag = RAGEngine()

        self.prompt = PromptBuilder()

        self.llm = LLMEngine()

        self.exporter = ExcelExporter()

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

        output_folder="Output/TestCases"

    ):

        try:

            self.logger.info(
                "Generating test cases..."
            )

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

                test_types=[

                    "Functional",
                    "Positive",
                    "Negative",
                    "Boundary",
                    "Validation"

                ],

                number_of_cases=number_of_cases

            )

            result = self.llm.generate(

                prompt=prompt,

                system_prompt=self.prompt.system_prompt(),

                temperature=0.1,

                max_tokens=4096

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

            rows = self.exporter.parse_llm_output(

                output_text

            )

            Path(output_folder).mkdir(

                parents=True,

                exist_ok=True

            )

            from datetime import datetime

            excel_file = (
                Path(output_folder)
                / f"Generated_Test_Cases_{datetime.now():%Y%m%d_%H%M%S}.xlsx"
            )

            try:

                self.exporter.export_test_cases(

                    rows=rows,

                    output_file=str(excel_file)

                )

            except PermissionError:

                return {

                    "success": False,

                    "error": "Please close the generated Excel file and try again."

                }

            return {

                "success": True,

                "requirement": requirement,

                "excel_file": str(excel_file),

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