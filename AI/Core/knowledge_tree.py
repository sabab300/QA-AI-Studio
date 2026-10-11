"""
QA AI Studio
Knowledge Tree

Version: 1.0
"""

from Core.metadata_manager import MetadataManager


class KnowledgeTree:

    def __init__(self):

        self.metadata = MetadataManager()

    # --------------------------------------------------

    def build(self):
        from pathlib import Path
        from Shared.hierarchy_values import canonical_source_type
        tree={}
        for row in self.metadata.list_all():
            filename=row.get('file_name') or row.get('source_location')
            if not filename:continue
            values=[row.get('domain'),row.get('module'),row.get('knowledge_name'),row.get('document_type'),row.get('version'),
                canonical_source_type(row.get('source_type')),row.get('source_file_id') or Path(filename).stem,filename]
            node=tree
            for value in values:node=node.setdefault(str(value or 'Unspecified'),{})
        return tree

    # --------------------------------------------------

    def print_tree(self):

        tree = self.build()

        def show(nodes,depth=0):
            for name,children in nodes.items():
                print('  '*depth+name);show(children,depth+1)
        show(tree)
