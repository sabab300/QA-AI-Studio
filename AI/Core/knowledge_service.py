"""
QA AI Studio
Knowledge Service

Version: 1.0

Business Layer for Knowledge Hub.
"""
from pathlib import Path

from Core.logger import Logger
from Core.repository_manager import RepositoryManager
from Core.upload_pipeline import UploadPipeline



class KnowledgeService:


    def __init__(self):

        self.logger = Logger.get_logger()

        self.repository = RepositoryManager()

        self.pipeline = UploadPipeline()



    # --------------------------------------------------
    # Upload Knowledge
    # --------------------------------------------------

    def upload(
        self,
        domain,
        module,
        knowledge_name,
        version="1.0",
        document_type="",
        files=None,
        folders=None,
        urls=None,
        notes=None
    ):

        self.logger.info(
            f"Knowledge Upload Started: "
            f"{domain}/{module}/{knowledge_name}/{version}"
        )

        results = []

        # -----------------------------
        # Files parameter
        # (supports both files & folders)
        # -----------------------------
        for item in (files or []):

            try:

                path = Path(item)

                if not path.exists():
                    raise FileNotFoundError(str(path))

                if path.is_file():

                    result = self.pipeline.upload_file(
                        source_file=str(path),
                        domain=domain,
                        module=module,
                        knowledge_name=knowledge_name,
                        version=version,
                        document_type=document_type
                    )

                elif path.is_dir():

                    result = self.pipeline.upload_folder(
                        folder=str(path),
                        domain=domain,
                        module=module,
                        knowledge_name=knowledge_name,
                        version=version
                    )

                else:

                    raise Exception(f"Unsupported path: {path}")

                results.append(result)

            except Exception as e:

                self.logger.exception(
                    f"Knowledge upload failed: {e}"
                )

                results.append(
                    {
                        "success": False,
                        "file": item,
                        "error": str(e)
                    }
                )

        # -----------------------------
        # Explicit folders parameter
        # -----------------------------
        for folder in (folders or []):

            try:

                result = self.pipeline.upload_folder(
                    folder=folder,
                    domain=domain,
                    module=module,
                    knowledge_name=knowledge_name,
                    version=version
                )

                results.append(result)

            except Exception as e:

                self.logger.exception(
                    f"Knowledge upload failed: {e}"
                )

                results.append(
                    {
                        "success": False,
                        "file": folder,
                        "error": str(e)
                    }
                )

        primary_result = {}

        for item in results:

            if item.get("success", False):

                primary_result = item

                break

        return {

            "success": all(
                r.get("success", False)
                for r in results
            ) if results else True,

            "domain": domain,

            "module": module,

            "knowledge_name": knowledge_name,

            "version": version,

            "primary_result": primary_result,

            "results": results


        }


    # --------------------------------------------------
    # Search Knowledge
    # --------------------------------------------------

    def search(
        self,
        query,
        top_k=5
    ):

        return self.pipeline.search(

            query=query,

            top_k=top_k

        )

    # --------------------------------------------------
    # AI Smart Upload
    # --------------------------------------------------

    def smart_upload(
        self,
        source_file
    ):

        path = Path(source_file)

        if not path.exists():
            raise FileNotFoundError(source_file)

        text = self.pipeline.extractor.extract_text(
            str(path)
        )

        if not text.strip():

            raise Exception(
                "No readable text found in document."
            )

        analysis = self.pipeline.analyzer.analyze(text)

        return {

            "domain": analysis.get(
                "platform",
                "Unknown"
            ),

            "module": analysis.get(
                "business_process",
                ""
            ),

            "knowledge_name": (
                path.stem
                .replace("_", " ")
                .replace("-", " ")
                .strip()
            ),

            "version": "1.0",

            "document_type": analysis.get(
                "document_type",
                "General"
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
                0
            ),

            "category": analysis.get(
                    "category",
                    "General"
            ),

            "repository_path": ""

        }

    # --------------------------------------------------
    # List Knowledge
    # --------------------------------------------------

    def list_all(self):

        return []



    # --------------------------------------------------
    # Delete Knowledge
    # --------------------------------------------------

    def delete(
        self,
        knowledge_id
    ):

        return False