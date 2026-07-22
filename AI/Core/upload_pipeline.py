"""
QA AI Studio
Upload Pipeline

Version: 3.1
"""

from pathlib import Path

from Core.repository_manager import RepositoryManager
from Core.metadata_manager import MetadataManager
from Core.text_extractor import TextExtractor
from Core.knowledge_analyzer import KnowledgeAnalyzer
from Core.embedding_engine import EmbeddingEngine
from Core.vector_store import VectorStore
from Core.document_chunker import DocumentChunker
from Core.smart_upload import SmartUpload

class UploadPipeline:

    def __init__(self):

        self.repository = RepositoryManager()
        self.metadata = MetadataManager()
        self.extractor = TextExtractor()
        self.analyzer = KnowledgeAnalyzer()
        self.embedding = EmbeddingEngine()
        self.vector_store = VectorStore()
        self.chunker = DocumentChunker()
        self.smart_uploader = SmartUpload()

    # --------------------------------------------------
    # Upload File
    # --------------------------------------------------

    def upload_file(
        self,
        source_file,
        domain,
        module,
        knowledge_name,
        version="1.0"
    ):

        source_file = Path(source_file).resolve()

        file_info = self.repository.save_file(
            source_file=str(source_file),
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=version
        )

        text = self.extractor.extract_text(
            file_info["repository_path"]
        )

        analysis = self.analyzer.analyze(text)

        metadata_status = self.metadata.save_knowledge_item(
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=version,
            file_info=file_info,
            analysis=analysis
        )

        chunks = self.chunker.split(text)

        vectors_saved = 0

        for index, chunk in enumerate(chunks, start=1):

            embedding = self.embedding.generate_embedding(chunk)

            if embedding is None:
                continue

            chunk_id = f"{file_info['sha256']}_{index}"

            if self.vector_store.save_document(
                doc_id=chunk_id,
                text=chunk,
                embedding=embedding,
                metadata={
                    "domain": domain,
                    "module": module,
                    "knowledge_name": knowledge_name,
                    "version": version,

                    "file_name": file_info["file_name"],
                    "file_type": file_info["extension"],

                    "platform": analysis.get("platform", ""),
                    "category": analysis.get("category", ""),
                    "business_process": analysis.get("business_process", ""),
                    "document_type": analysis.get("document_type", ""),

                    "summary": analysis.get("summary", ""),

                    "tags": ",".join(
                        analysis.get("tags", [])
                    ),

                "confidence": analysis.get("confidence", 0),

                "chunk_number": index,
                "total_chunks": len(chunks)
            }
            ):
                vectors_saved += 1

        return {
            "success": True,
            "domain": domain,
            "module": module,
            "knowledge_name": knowledge_name,
            "version": version,
            "file_info": file_info,
            "analysis": analysis,
            "metadata_status": metadata_status,
            "total_chunks": len(chunks),
            "vectors_saved": vectors_saved
        }

    # --------------------------------------------------
    # Backward Compatibility
    # --------------------------------------------------

    def upload(
        self,
        source_file,
        domain,
        module,
        knowledge_name,
        version="1.0"
    ):

        return self.upload_file(
            source_file=source_file,
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=version
        )

    # --------------------------------------------------
    # Upload Folder
    # --------------------------------------------------

    def upload_folder(

        self,

        folder,

        domain,

        module,

        knowledge_name,

        version="v1.0"

    ):

        folder = Path(folder).resolve()

        if not folder.exists():

            raise FileNotFoundError(folder)

        if not folder.is_dir():

            raise NotADirectoryError(folder)

        supported_extensions = {

            ".pdf",

            ".doc",

            ".docx",

            ".txt",

            ".xlsx",

            ".xls",

            ".csv",

            ".png",

            ".jpg",

            ".jpeg"

        }

        results = []

        total = 0

        success = 0

        failed = 0

        for file in folder.rglob("*"):

            if not file.is_file():

                continue

            if file.suffix.lower() not in supported_extensions:

                continue

            total += 1

            try:

                result = self.upload_file(

                    source_file=str(file),

                    domain=domain,

                    module=module,

                    knowledge_name=knowledge_name,

                    version=version

                )

                results.append(result)

                success += 1

            except Exception as ex:

                results.append({

                    "success": False,

                    "file": str(file),

                    "error": str(ex)

                })

                failed += 1

        return {

            "success": failed == 0,

            "total_files": total,

            "uploaded": success,

            "failed": failed,

            "results": results

        }
    
    # --------------------------------------------------
    # AI Smart Upload
    # --------------------------------------------------

    def smart_upload(

        self,

        source_file

    ):

        source_file = Path(source_file).resolve()

        analysis = self.smart_uploader.analyze(

        str(source_file)

    )

        return analysis