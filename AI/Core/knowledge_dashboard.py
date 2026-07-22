from Core.metadata_manager import MetadataManager


class KnowledgeDashboard:

    def __init__(self):

        self.metadata = MetadataManager()

    def summary(self):

        items = self.metadata.list_all()

        total = len(items)

        uploaded = 0
        embedded = 0
        failed = 0

        domains = set()
        modules = set()
        categories = set()
        platforms = set()

        for item in items:

            status = item[17]

            if status == "UPLOADED":
                uploaded += 1

            elif status == "COMPLETED":
                embedded += 1

            elif status == "FAILED":
                failed += 1

            if item[1]:
                domains.add(item[1])

            if item[2]:
                modules.add(item[2])

            if len(item) > 21 and item[21]:
                platforms.add(item[21])

            if len(item) > 22 and item[22]:
                categories.add(item[22])

        return {
            "total_documents": total,
            "uploaded": uploaded,
            "embedded": embedded,
            "failed": failed,
            "domains": len(domains),
            "modules": len(modules),
            "platforms": len(platforms),
            "categories": len(categories),
        }