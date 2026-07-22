"""
QA AI Studio
Metadata Filter

Version: 2.0
"""

from Core.metadata_manager import MetadataManager
from Core.vector_store import VectorStore


class MetadataFilter:

    def __init__(self):

        self.metadata = MetadataManager()
        self.store = VectorStore()

    # --------------------------------------------------
    # Build Chroma Filter
    # --------------------------------------------------

    def build(

        self,

        domain=None,

        module=None,

        knowledge_name=None,

        version=None

    ):

        where = {}

        if domain:
            where["domain"] = domain

        if module:
            where["module"] = module

        if knowledge_name:
            where["knowledge_name"] = knowledge_name

        if version:
            where["version"] = version

        return where if where else None

    # --------------------------------------------------
    # Check Knowledge Exists
    # --------------------------------------------------

    def exists(

        self,

        domain,

        module,

        knowledge_name

    ):

        return self.metadata.get_knowledge_item(

            domain,

            module,

            knowledge_name

        ) is not None

    # --------------------------------------------------
    # Load Complete Knowledge
    # --------------------------------------------------

    def load_knowledge(

        self,

        domain,

        module,

        knowledge_name

    ):

        data = self.store.get_all()

        if not data:

            return []

        documents = data.get("documents", [])
        metadatas = data.get("metadatas", [])
        ids = data.get("ids", [])

        results = []

        for doc_id, document, metadata in zip(

            ids,

            documents,

            metadatas

        ):

            metadata = metadata or {}

            if metadata.get("domain") != domain:
                continue

            if metadata.get("module") != module:
                continue

            if metadata.get("knowledge_name") != knowledge_name:
                continue

            results.append({

                "id": doc_id,

                "document": document,

                "metadata": metadata

            })

        results.sort(

            key=lambda x: x["metadata"].get(

                "chunk_number",

                0

            )

        )

        return results