from Core.version_manager import VersionManager

manager = VersionManager()

print("=" * 80)
print("LATEST VERSION")
print("=" * 80)

print(
    manager.latest(4)
)