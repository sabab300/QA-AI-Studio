from Core.search_engine_v2 import SearchEngineV2

engine = SearchEngineV2()

results = engine.search(

    "What is SD Warehousing?",

    limit=3

)

print("=" * 80)
print("SEARCH ENGINE V2")
print("=" * 80)

for item in results:

    print(item)
    print("-" * 80)