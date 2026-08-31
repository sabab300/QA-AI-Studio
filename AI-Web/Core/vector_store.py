"""
QA AI Studio
Vector Store Manager

Version: 3.0
Production Ready
"""

import chromadb

from Core.config_manager import ConfigManager
from Core.logger import Logger


class VectorStore:

    _client = None
    _collection = None
    _logger = None

    COLLECTION_NAME = "psw_knowledge"

    def __init__(self):

        self.config = ConfigManager()

        if VectorStore._logger is None:
            VectorStore._logger = Logger.get_logger()

        self.logger = VectorStore._logger

        if VectorStore._client is None:

            self.logger.info("Initializing ChromaDB...")

            VectorStore._client = chromadb.PersistentClient(
                path=str(self.config.chroma_db)
            )

        self.client = VectorStore._client

        if VectorStore._collection is None:

            VectorStore._collection = (
                self.client.get_or_create_collection(
                    name=self.COLLECTION_NAME
                )
            )

        self.collection = VectorStore._collection

    # --------------------------------------------------
    # Metadata Normalizer
    # --------------------------------------------------

    def _prepare_metadata(self, metadata):

        defaults = {

            "domain": "",
            "module": "",
            "knowledge_name": "",
            "version": "",

            "document_id": "",
            "file_name": "",
            "file_type": "",

            "platform": "",
            "category": "",
            "business_process": "",
            "document_type": "",

            "summary": "",
            "tags": "",

            "confidence": 0.0,

            "chunk_number": 0,
            "total_chunks": 0

        }

        if not isinstance(metadata, dict):
            metadata = {}

        defaults.update(metadata)

        normalized = {}

        for key, value in defaults.items():

            if value is None:

                normalized[key] = ""

            elif isinstance(value, bool):

                normalized[key] = value

            elif isinstance(value, (int, float)):

                normalized[key] = value

            elif isinstance(value, list):

                normalized[key] = ", ".join(
                    str(item)
                    for item in value
                )

            elif isinstance(value, dict):

                normalized[key] = str(value)

            else:

                normalized[key] = str(value).strip()

        return normalized

    # --------------------------------------------------
    # Save Document
    # --------------------------------------------------

    def save_document(

        self,
        doc_id,
        text,
        embedding,
        metadata=None

    ):

        try:

            if not doc_id:
                raise ValueError("Document id is empty.")

            if embedding is None:
                raise ValueError("Embedding is None.")

            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()

            metadata = self._prepare_metadata(metadata)

            try:
                self.collection.delete(
                    ids=[str(doc_id)]
                )
            except Exception:
                pass

            self.collection.add(

                ids=[str(doc_id)],

                documents=[str(text)],

                embeddings=[embedding],

                metadatas=[metadata]

            )

            self.logger.info(
                f"Vector saved: {doc_id}"
            )

            return True

        except Exception:

            self.logger.exception(
                "Vector save failed."
            )

            return False

# PATCH — AI/Core/vector_store.py
#
# Add this method to the VectorStore class, right after
# save_document() (around line 174, after its "return False").
#
# It does the exact same delete-then-add safety as save_document(),
# but as ONE batched ChromaDB call instead of N individual ones.

    # --------------------------------------------------
    # Save Documents (Batch)
    # --------------------------------------------------

    def save_documents_batch(self, items):
        """
        items: list of dicts, each with keys:
            doc_id, text, embedding, metadata
        Returns the number of documents successfully saved.
        """

        if not items:

            return 0

        try:

            ids = []
            documents = []
            embeddings = []
            metadatas = []

            for item in items:

                embedding = item["embedding"]

                if embedding is None:

                    continue

                if hasattr(embedding, "tolist"):

                    embedding = embedding.tolist()

                ids.append(str(item["doc_id"]))

                documents.append(str(item["text"]))

                embeddings.append(embedding)

                metadatas.append(
                    self._prepare_metadata(item.get("metadata"))
                )

            if not ids:

                return 0

            try:

                self.collection.delete(ids=ids)

            except Exception:

                pass

            self.collection.add(
                ids=ids,
                documents=documents,
                embeddings=embeddings,
                metadatas=metadatas,
            )

            self.logger.info(
                f"Vector batch saved: {len(ids)} chunk(s)."
            )

            return len(ids)

        except Exception:

            self.logger.exception(
                "Vector batch save failed."
            )

            return 0

    # --------------------------------------------------
    # Delete
    # --------------------------------------------------

    def delete(

        self,
        doc_id

    ):

        try:

            self.collection.delete(
                ids=[str(doc_id)]
            )

            return True

        except Exception:

            self.logger.exception(
                "Vector delete failed."
            )

            return False

    # --------------------------------------------------
    # Delete by Knowledge
    # --------------------------------------------------

    def delete_by_knowledge_name(
        self,
        knowledge_name
    ):

        try:

            data = self.collection.get()

            ids = data.get("ids", [])
            metadatas = data.get("metadatas", [])

            delete_ids = []

            for doc_id, metadata in zip(ids, metadatas):

                if metadata.get("knowledge_name") == knowledge_name:

                    delete_ids.append(doc_id)

            if delete_ids:

                self.collection.delete(
                    ids=delete_ids
                )

            return len(delete_ids)

        except Exception:

            self.logger.exception(
                "Vector delete failed."
            )

            return 0

    # --------------------------------------------------
    # Search
    # --------------------------------------------------

    def search(

        self,
        embedding,
        limit=5,
        where=None

    ):

        try:

            if embedding is None:
                return None

            if hasattr(embedding, "tolist"):
                embedding = embedding.tolist()

            query = {

                "query_embeddings": [embedding],

                "n_results": limit

            }

            if where:

                query["where"] = where

            result = self.collection.query(
                **query
            )

            if not result:
                return None

            return result

        except Exception:

            self.logger.exception(
                "Vector search failed."
            )

            return None

    # --------------------------------------------------
    # Count
    # --------------------------------------------------

    def count(self):

        try:

            return self.collection.count()

        except Exception:

            self.logger.exception(
                "Collection count failed."
            )

            return 0

    # --------------------------------------------------
    # Clear
    # --------------------------------------------------

    def clear(self):

        try:

            data = self.collection.get()

            ids = data.get(
                "ids",
                []
            )

            if ids:

                self.collection.delete(
                    ids=ids
                )

            self.logger.info(
                "Vector collection cleared."
            )

            return True

        except Exception:

            self.logger.exception(
                "Collection clear failed."
            )

            return False

    # --------------------------------------------------
    # Information
    # --------------------------------------------------

    def info(self):

        try:

            return {

                "collection_name": self.collection.name,

                "documents": self.collection.count()

            }

        except Exception:

            self.logger.exception(
                "Collection info failed."
            )

            return {}

    # --------------------------------------------------
    # Get All
    # --------------------------------------------------

    def get_all(self):

        try:

            return self.collection.get()

        except Exception:

            self.logger.exception(
                "Get all documents failed."
            )

            return None