from Core.version_manager import VersionManager

manager = VersionManager()

print("=" * 80)
print("DUPLICATE CHECK")
print("=" * 80)

result = manager.exists(
    "bede540b110bf3fc5c3499c1fafdce963a18ccae0d4b8f0f4f9fd7eb4040767d"
)

print(result)