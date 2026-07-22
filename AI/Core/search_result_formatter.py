"""
QA AI Studio
Search Result Formatter
Version: 1.0
"""


class SearchResultFormatter:

    def format(self, results):

        if not results:

            return []

        ids = results.get("ids", [[]])[0]
        documents = results.get("documents", [[]])[0]
        metadatas = results.get("metadatas", [[]])[0]
        distances = results.get("distances", [[]])[0]

        formatted = []

        for i in range(len(ids)):

            metadata = {}

            if i < len(metadatas) and metadatas[i]:

                metadata = metadatas[i]

            formatted.append({

                "id": ids[i],

                "score": round(
                    1 - distances[i],
                    4
                ) if i < len(distances) else None,

                "document": documents[i] if i < len(documents) else "",

                "domain": metadata.get("domain"),

                "module": metadata.get("module"),

                "knowledge_name": metadata.get("knowledge_name"),

                "version": metadata.get("version"),

                "file_name": metadata.get("file_name"),

                "chunk": metadata.get("chunk"),

                "total_chunks": metadata.get("total_chunks")

            })

        return formatted