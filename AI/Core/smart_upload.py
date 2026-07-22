"""
QA AI Studio
Smart Upload

Version: 1.0
"""

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

                "success": False,

                "error": "Unable to extract text."

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