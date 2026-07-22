from Core.metadata_manager import MetadataManager

db = MetadataManager()

print("\nPlatforms")
print("----------------")
print(db.get_platforms())

print("\nCategories")
print("----------------")
print(db.get_categories())