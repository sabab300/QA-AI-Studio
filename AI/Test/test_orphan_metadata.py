from Core.orphan_metadata import OrphanMetadata

checker = OrphanMetadata()

print("=" * 80)
print("ORPHAN METADATA")
print("=" * 80)

for item in checker.scan():

    print(item)