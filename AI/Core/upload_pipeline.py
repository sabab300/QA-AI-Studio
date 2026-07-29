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
        self.chunker = DocumentChunker()
        self.smart_uploader = SmartUpload()
        self.vector_store = VectorStore()

    # --------------------------------------------------
    # Upload File
    # --------------------------------------------------

    def upload_file(
        self,
        source_file,
        domain,
        module,
        knowledge_name,
        version="1.0",
        document_type=""
        ):

        source_file = Path(source_file).resolve()

        file_info = self.repository.save_file(
            source_file=str(source_file),
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=version,
            document_type=document_type
        )

        text = self.extractor.extract_text(
            file_info["repository_path"]
        )

        analysis = self.analyzer.analyze(text)

        analysis = dict(analysis)

        print("SUMMARY:", analysis.get("summary"))

        if file_info.get("document_type"):
            analysis["document_type"] = file_info["document_type"]

        metadata_status = self.metadata.save_knowledge_item(
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=version,
            file_info=file_info,
            analysis=analysis
        )

# PATCH — AI/Core/upload_pipeline.py
#
# Replace the chunk loop (lines ~79-122 — from "chunks = self.chunker.split(text)"
# through the end of the "for index, chunk in enumerate..." loop) with this.
#
# Before: N chunks -> N separate model.encode() calls -> N separate
#         ChromaDB add() calls.
# After:  N chunks -> 1 batched model.encode() call -> 1 batched
#         ChromaDB add() call. Same result, much fewer round trips.

        chunks = self.chunker.split(text)

        vectors_saved = 0

        if chunks:

            embeddings = self.embedding.generate_embeddings(chunks)

            if embeddings is None:

                embeddings = [None] * len(chunks)

            batch_items = []

            for index, chunk in enumerate(chunks, start=1):

                embedding = embeddings[index - 1]

                if embedding is None:

                    continue

                chunk_id = f"{file_info['sha256']}_{index}"

                batch_items.append({
                    "doc_id": chunk_id,
                    "text": chunk,
                    "embedding": embedding,
                    "metadata": {
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
                        "total_chunks": len(chunks),
                    },
                })

            vectors_saved = self.vector_store.save_documents_batch(
                batch_items
            )

# Everything after this point (the "return { ... }" block) stays
# exactly the same — vectors_saved and len(chunks) are still set,
# just computed more efficiently.

        return {
            "success": True,

            "domain": domain,
            "module": module,
            "knowledge_name": knowledge_name,
            "version": version,

            # AI Analysis
            "platform": analysis.get("platform", ""),
            "category": analysis.get("category", ""),
            "business_process": analysis.get("business_process", ""),
            "document_type": analysis.get("document_type", ""),
            "summary": analysis.get("summary", ""),
            "tags": analysis.get("tags", []),
            "confidence": analysis.get("confidence", 0),

            # Repository/File Information
            "repository_path": file_info.get("repository_path", ""),
            "file_name": file_info.get("file_name", ""),
            "file_type": file_info.get("extension", ""),

            # Upload Statistics
            "total_chunks": len(chunks),
            "vectors_saved": vectors_saved,

            # Existing Objects (keep these for compatibility)
            "file_info": file_info,
            "analysis": analysis,
            "metadata_status": metadata_status
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
        version="1.0",
        document_type=""
    ):

        return self.upload_file(
            source_file=source_file,
            domain=domain,
            module=module,
            knowledge_name=knowledge_name,
            version=version,
            document_type=document_type
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

    # --------------------------------------------------
    # Delete Knowledge
    # --------------------------------------------------

    def delete_knowledge(
        self,
        domain,
        module,
        knowledge_name
    ):

        data = self.vector_store.get_all()

        if not data:
            return

        ids = data.get("ids", [])
        metadatas = data.get("metadatas", [])

        for doc_id, metadata in zip(ids, metadatas):

            if (
                metadata.get("domain") == domain
                and metadata.get("module") == module
                and metadata.get("knowledge_name") == knowledge_name
            ):

                self.vector_store.delete(doc_id)