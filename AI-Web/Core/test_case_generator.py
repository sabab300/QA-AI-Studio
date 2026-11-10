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
]
# "Regression" and "Impact Analysis" are no longer selectable or generated: regression selection now belongs to Change Impact Analysis.
# Test cases that already carry those values stay readable, editable and importable; nothing rewrites them.


def normalize_test_types(row, requested_types):
    """Keep AI output canonical and inside the types the user selected.

    A row keeps only the selected types it already carries. If it carries none, the single
    best-fitting selected type is chosen from its wording (never "all selected types").
    """
    requested = [value for value in (requested_types or []) if value in ALL_TEST_TYPES]
    raw = row.get("test_types") or row.get("test_type") or []
    if isinstance(raw, str):
        raw = [value.strip() for value in raw.replace(";", ",").split(",")]
    valid = []
    for value in raw:
        value = str(value).strip()
        if value in ALL_TEST_TYPES and value not in valid and (not requested or value in requested):
            valid.append(value)
    if not valid and requested:
        from Core.test_case_engine import classify
        guess = classify(row, requested)
        valid = [guess] if guess else requested[:1]
    row["test_types"] = valid
    row["test_type"] = ", ".join(valid)
    return row


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

        source_file_names=None,

        test_types=None,

        output_formats=None,
        persist=True,
        create_exports=True,

        output_folder="Output/TestCases",
        document_type=None,
        document_version_id=None,
        progress=None,

    ):

        try:

            self.logger.info(
                "Generating test cases..."
            )

            selected_types = [value for value in (test_types or ALL_TEST_TYPES) if value in ALL_TEST_TYPES]

            if not selected_types:

                return {
                    "success": False,
                    "error": "Select at least one supported Test Type."
                }

            test_types = selected_types

            if not output_formats:

                output_formats = ["Excel"]

            chunks, references = self._load_chunks(
                requirement, top_k, domain, module, knowledge_name, version,
                source_file_names, document_type, document_version_id,
            )

            if not chunks:

                return {

                    "success": False,

                    "error": "No knowledge found."

                }

            state = {"provider": None, "model": None, "error": None}
            llm_call = self._llm_call(state)
            focus = None if source_file_names else requirement

            from Core.test_case_engine import generate_cases

            outcome = generate_cases(
                chunks, test_types, number_of_cases, llm_call,
                label=(knowledge_name or "Selected knowledge"),
                progress=progress, focus=focus,
            )

            rows = outcome.get("rows") or []
            quality_report = outcome.get("report") or {}

            if not rows:

                if quality_report.get("model_calls") and quality_report.get("model_failures") == quality_report.get("model_calls") and state["error"]:

                    return {"success": False, "error": state["error"]}

                return {

                    "success": False,

                    "error": outcome.get("error") or (
                        "No test case passed the quality checks for the selected test types. "
                        + " ".join((quality_report.get("summary") or [])[:3])
                    ),

                    "quality_report": quality_report,

                }

            for row in rows:
                row.pop("_grounding", None)
                normalize_test_types(row, test_types)
                self._add_execution_metadata(row)

            if create_exports:
                Path(output_folder).mkdir(parents=True, exist_ok=True)

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

            base_name = f"Generated_Test_Cases_{timestamp}"

            output_files = {}

            try:

                if create_exports and "Excel" in output_formats:

                    excel_file = Path(output_folder) / f"{base_name}.xlsx"

                    self.excel_exporter.export_test_cases(
                        rows=rows,
                        output_file=str(excel_file)
                    )

                    output_files["excel_file"] = str(excel_file)


                if create_exports and "Word" in output_formats:

                    word_file = Path(output_folder) / f"{base_name}.docx"

                    self.word_exporter.export_test_cases(
                        rows=rows,
                        output_file=str(word_file)
                    )

                    output_files["word_file"] = str(word_file)


                if create_exports and "PDF" in output_formats:

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
            if persist and domain and module and knowledge_name:

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
                "rows": rows,

                "test_case_ids": test_case_ids,

                **output_files,

                "references": references,

                "quality_report": quality_report,

                "provider": state["provider"],

                "model": state["model"]

            }

        except Exception as error:

            self.logger.exception(

                "Test case generation failed."

            )

            return {

                "success": False,

                "error": str(error)

            }

    def _load_chunks(self, requirement, top_k, domain, module, knowledge_name, version,
                     source_file_names, document_type, document_version_id):
        """Return (chunks, references).

        With a selected source document the WHOLE document is read, in order. Without one
        (AI Assistant request) the most relevant chunks for the request are used.
        """
        if source_file_names:
            try:
                from Core.vector_store import VectorStore
                chunks = VectorStore().get_source_chunks(
                    domain=domain, module=module, knowledge_name=knowledge_name, version=version,
                    file_names=list(source_file_names), document_type=document_type,
                    document_version_id=document_version_id,
                )
            except Exception:
                self.logger.exception("Could not read the selected source document; using search instead.")
                chunks = []
            if chunks:
                seen, references = set(), []
                for chunk in chunks:
                    name = chunk.get("file_name")
                    if name not in seen:
                        seen.add(name)
                        references.append(dict(chunk.get("metadata") or {"file_name": name}))
                return chunks, references

        rag = self.rag.get_context(
            question=requirement, top_k=max(int(top_k or 5), 8),
            domain=domain, module=module, knowledge_name=knowledge_name, version=version,
            source_file_names=source_file_names, document_type=document_type,
            document_version_id=document_version_id,
        )
        documents = rag.get("documents") or []
        return [{"text": text, "chunk_number": index} for index, text in enumerate(documents)], rag.get("references", [])

    def _llm_call(self, state):
        """A direct model call for generation.

        It deliberately bypasses the chat pipeline (prompt optimizer, conversation history, answer
        cache, line de-duplication and formatting), which cut the prompt and rewrote the answer.
        """
        provider = None
        try:
            from Core.ai_platform.compat import GatewayLLMProvider
            provider = GatewayLLMProvider()
        except Exception:
            self.logger.exception("AI platform gateway unavailable; using the standard engine.")

        def call(prompt, system_prompt):
            if provider is not None:
                result = provider.generate(prompt=prompt, system_prompt=system_prompt,
                                           temperature=0.2, max_tokens=3000)
            else:
                result = self.llm.generate(prompt=prompt, system_prompt=system_prompt,
                                           temperature=0.2, max_tokens=3000)
            if not result.get("success"):
                state["error"] = result.get("error") or "The AI model call failed."
                raise RuntimeError(state["error"])
            state["provider"] = result.get("provider") or state["provider"]
            state["model"] = result.get("model") or state["model"]
            return result.get("response")

        return call

    def _add_execution_metadata(self, row):
        """Normalize the model suggestion without equating it with script state."""
        execution = str(row.get("execution") or "").strip()
        aliases = {"API Automation": "API", "SQL Automation": "SQL"}
        execution = aliases.get(execution, execution)
        if execution not in {"Manual", "Playwright", "API", "SQL"}:
            # Decide from what the steps actually do. A test TYPE (Functional, Positive ...) says nothing
            # about how a case can be automated, so it must not default every case to Playwright.
            from Core.test_case_engine import classify_execution
            execution, _note = classify_execution(row)
        row["execution"] = execution
