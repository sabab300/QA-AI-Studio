"""
QA AI Studio
Smart Upload

Version: 1.0
"""

from pathlib import Path

from Core.text_extractor import TextExtractor
from Core.knowledge_analyzer import KnowledgeAnalyzer


class SmartUpload:

    def __init__(self):

        self.extractor = TextExtractor()

        self.analyzer = KnowledgeAnalyzer()

    # --------------------------------------------------

    def analyze(

        self,

        file_path

    ):

        text = self.extractor.extract_text(file_path)

        if not text:

            return {

                "success": True,
                "indexed": False,
                "requires_review": True,
                "error": (
                    "No extractable text was found. The source can be preserved after you "
                    "review its metadata, but it will not be indexed for RAG."
                ),
                "domain": "",
                "module": "",
                "knowledge_name": Path(file_path).stem,
                "version": "1.0",
                "document_type": "Other",
                "summary": "No extractable text; metadata review is required.",
                "tags": [],
                "confidence": 0,

            }

        analysis = self.analyzer.analyze(text)

        return {

            "success": True,

            "domain": analysis.get(
                "platform",
                "Unknown"
            ),

            "module": analysis.get(
                "business_process",
                "General"
            ),

            "knowledge_name": analysis.get(
                "document_name",
                analysis.get(
                    "document_type",
                    "Knowledge Document"
                )
            ),

            "version": analysis.get(
                "version",
                "1.0"
            ),

            "platform": analysis.get(
                "platform",
                ""
            ),

            "category": analysis.get(
                "category",
                ""
            ),

            "business_process": analysis.get(
                "business_process",
                ""
            ),

            "document_type": analysis.get(
                "document_type",
                ""
            ),

            "summary": analysis.get(
                "summary",
                ""
            ),

            "tags": analysis.get(
                "tags",
                []
            ),

            "confidence": analysis.get(
                "confidence",
                0.0
            )
        }
