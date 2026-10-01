"""Disposable real ingestion -> retrieval -> QA generation verification."""

import tempfile
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from docx import Document

from Core.knowledge_web_repository import KnowledgeRepository
from Core.metadata_manager import MetadataManager
from Core.rag_engine import RAGEngine
from Core.test_case_generator import TestCaseGenerator
from Core.text_extractor import TextExtractor
from Core.upload_pipeline import UploadPipeline
from Core.vector_store import VectorStore


def main():
    suffix = uuid.uuid4().hex[:8]
    domain = f"Codex AI Verification {suffix}"
    module = "Login"
    knowledge = "Login Requirements"
    version = "1.0"
    knowledge_id = None
    with tempfile.TemporaryDirectory(prefix="qaais-ai-positive-") as temp:
        source = Path(temp) / "login-requirements.docx"
        document = Document()
        document.add_heading("Login Requirements", level=1)
        document.add_paragraph("The user shall be able to log in using a valid username and password.")
        document.add_paragraph("Invalid password shall display an authentication error.")
        document.add_paragraph("The account shall be locked after five consecutive failed login attempts.")
        document.add_paragraph("A successfully authenticated user shall be redirected to the dashboard.")
        document.save(source)
        extracted = TextExtractor().extract_text(source)
        assert len(extracted.strip()) > 0
        try:
            uploaded = UploadPipeline().upload_file(
                source, domain, module, knowledge, version=version,
                document_type="SRS", source_type="Files",
                reviewed_analysis={
                    "summary": "Disposable login requirements integration verification.",
                    "tags": ["login", "authentication"], "confidence": 1.0,
                },
            )
            knowledge_id = int(uploaded["metadata_status"])
            assert uploaded["total_chunks"] > 0
            assert uploaded["vectors_saved"] > 0
            assert VectorStore().has_source_vectors(
                domain=domain, module=module, knowledge_name=knowledge,
                version=version, file_name=uploaded["file_name"],
            )
            context = RAGEngine().get_context(
                "What happens after five failed login attempts?", top_k=3,
                domain=domain, module=module, knowledge_name=knowledge,
                version=version, source_file_names=[uploaded["file_name"]],
            )
            context_text = str(context.get("context") or context.get("combined_context") or "")
            assert "locked" in context_text.lower(), context
            generated = TestCaseGenerator().generate(
                requirement="Generate login test cases from the selected requirements.",
                number_of_cases=2, domain=domain, module=module,
                knowledge_name=knowledge, version=version,
                source_file_names=[uploaded["file_name"]],
                test_types=["Functional", "Negative"],
                output_formats=[], persist=False, create_exports=False,
            )
            print("AI-POS extracted_chars", len(extracted))
            print("AI-POS chunks", uploaded["total_chunks"])
            print("AI-POS vectors", uploaded["vectors_saved"])
            print("AI-POS retrieval_chars", len(context_text))
            print("AI-POS generation", generated)
            assert generated.get("success") and generated.get("rows")
        finally:
            if knowledge_id is not None:
                KnowledgeRepository().delete_item(knowledge_id)
            metadata = MetadataManager()
            try:
                metadata.delete_module_by_name(domain, module)
                metadata.delete_domain_by_name(domain)
            except Exception:
                pass


if __name__ == "__main__":
    main()
