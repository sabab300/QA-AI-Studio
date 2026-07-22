from Core.version_manager import VersionManager

manager = VersionManager()

manager.add_version(
    4,
    "1.1",
    {
        "sha256": "TEST123",
        "repository_path": "Repository/Test.docx"
    }
)

print("=" * 80)
print("VERSIONS")
print("=" * 80)

for version in manager.versions(4):
    print(version)