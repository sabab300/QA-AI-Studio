"""
QA AI Studio
Test Metadata Enricher
"""

from Core.search_engine_v2 import SearchEngineV2
from Core.metadata_enricher import MetadataEnricher

search = SearchEngineV2()
enricher = MetadataEnricher()

results = search.search(
    "SD Warehousing",
    limit=3
)

results = enricher.enrich(results)

print("=" * 80)
print("METADATA ENRICHER")
print("=" * 80)

for item in results:

    print(f"ID               : {item.get('id')}")
    print(f"Score            : {item.get('score')}")
    print(f"Domain           : {item.get('domain')}")
    print(f"Module           : {item.get('module')}")
    print(f"Knowledge Name   : {item.get('knowledge_name')}")
    print(f"Version          : {item.get('version')}")
    print(f"Platform         : {item.get('platform')}")
    print(f"Category         : {item.get('category')}")
    print(f"Business Process : {item.get('business_process')}")
    print(f"Document Type    : {item.get('document_type')}")
    print(f"File             : {item.get('file_name')}")
    print("-" * 80)