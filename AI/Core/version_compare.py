"""
QA AI Studio
Version Compare

Version: 1.0
"""


class VersionCompare:

    def compare(
    self,
    old_item,
    new_item
    ):

        mapping = {
            "domain": 1,
            "module": 2,
            "knowledge_name": 3,
            "version": 4,
            "status": 17,
            "platform": 21,
            "category": 22,
            "business_process": 23,
            "document_type": 24
        }

        changes = {}

        for field, index in mapping.items():

            old_value = old_item[index]
            new_value = new_item[index]

            if old_value != new_value:

                changes[field] = {
                    "old": old_value,
                    "new": new_value
                }

        return changes