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

        tree = {}

        domains = self.metadata.get_domains()

        for domain in domains:

            tree[domain] = {}

            modules = self.metadata.get_modules(domain)

            for module in modules:

                tree[domain][module] = {}

                knowledge_names = self.metadata.get_knowledge_names(
                    domain,
                    module
                )

                for knowledge_name in knowledge_names:

                    versions = self.metadata.get_versions(
                        domain,
                        module,
                        knowledge_name
                    )

                    tree[domain][module][knowledge_name] = versions

        return tree

    # --------------------------------------------------

    def print_tree(self):

        tree = self.build()

        for domain, modules in tree.items():

            print(domain)

            for module, knowledge in modules.items():

                print(f"  └── {module}")

                for name, versions in knowledge.items():

                    print(f"      └── {name}")

                    for version in versions:

                        print(f"          └── {version}")