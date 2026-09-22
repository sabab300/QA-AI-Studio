"""Focused extraction and zero-text preservation regression checks."""

import shutil
import sys
import tempfile
from pathlib import Path

from docx import Document
from pypdf import PdfWriter
from reportlab.pdfgen.canvas import Canvas

WEB_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = WEB_ROOT.parent
sys.path[:0] = [str(PROJECT_ROOT), str(WEB_ROOT)]

from Core.smart_upload import SmartUpload
from Core.text_extractor import TextExtractor
from Core.upload_pipeline import UploadPipeline


class FakeRepository:
    def __init__(self, root):
        self.root = Path(root)

    def save_file(self, source_file, **_metadata):
        source = Path(source_file)
        destination = self.root / source.name
        shutil.copy2(source, destination)
        return {
            "repository_path": str(destination), "repository_relative_path": source.name,
            "file_name": source.name, "extension": source.suffix.lower(),
            "sha256": "test", "document_type": _metadata.get("document_type", "CRF"),
            "source_file_id": source.stem + "-V1",
        }


class FakeMetadata:
    def __init__(self):
        self.saved = None
        self.status = None

    def save_knowledge_item(self, **values):
        self.saved = values
        return 1

    def update_embedding_status(self, knowledge_id, status):
        self.status = (knowledge_id, status)


def run_checks():
    with tempfile.TemporaryDirectory(prefix="qaais-extraction-") as temporary:
        root = Path(temporary)
        docx = root / "sample.docx"
        document = Document(); document.add_paragraph("DOCX regression text"); document.save(docx)

        text_pdf = root / "text.pdf"
        canvas = Canvas(str(text_pdf)); canvas.drawString(72, 720, "Extractable PDF text"); canvas.save()

        blank_pdf = root / "blank.pdf"
        writer = PdfWriter(); writer.add_blank_page(width=612, height=792)
        with blank_pdf.open("wb") as stream: writer.write(stream)

        extractor = TextExtractor()
        assert "DOCX regression text" in extractor.extract_text(docx)
        assert "Extractable PDF text" in extractor.extract_text(text_pdf)
        assert extractor.extract_text(blank_pdf) == ""

        smart = SmartUpload.__new__(SmartUpload)
        smart.extractor = extractor
        smart.analyzer = None
        smart_result = smart.analyze(blank_pdf)
        assert smart_result["success"] and smart_result["indexed"] is False
        assert smart_result["requires_review"] is True

        managed = root / "managed"; managed.mkdir()
        pipeline = UploadPipeline.__new__(UploadPipeline)
        pipeline.repository = FakeRepository(managed)
        pipeline.metadata = FakeMetadata()
        pipeline.extractor = extractor
        result = pipeline.upload_file(blank_pdf, "D1", "M1", "KN1", "1.0", "CRF")
        assert result["source_saved"] is True and result["indexed"] is False
        assert result["processing_status"] == "FAILED_NO_TEXT"
        assert Path(result["repository_path"]).is_file()
        assert pipeline.metadata.status == (1, "FAILED_NO_TEXT")


if __name__ == "__main__":
    run_checks()
    print("Upload extraction/zero-text preservation: PASS")
