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

        # BUGFIX: this used to map each field to a raw positional tuple
        # index (domain=1, status=17, platform=21, ...) into
        # "SELECT * FROM knowledge_items" — correct only by coincidence
        # of the schema migrations' current apply order. Any future
        # column added earlier in the table, or a table-recreate that
        # reorders columns, would silently compare the WRONG fields
        # against each other with no error. old_item/new_item are
        # sqlite3.Row objects (from MetadataManager.get(), row_factory
        # set there), which support name-based access exactly like a
        # dict — so compare by column name instead of position.
        fields = [
            "domain", "module", "knowledge_name", "version", "status",
            "platform", "category", "business_process", "document_type",
        ]

        changes = {}

        for field in fields:

            old_value = old_item[field]
            new_value = new_item[field]

            if old_value != new_value:

                changes[field] = {
                    "old": old_value,
                    "new": new_value
                }

        return changes