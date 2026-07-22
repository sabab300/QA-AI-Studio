from Core.repository_cleanup import RepositoryCleanup
from Core.missing_file_detector import MissingFileDetector

cleanup = RepositoryCleanup()

print("=" * 80)
print("BEFORE")
print("=" * 80)

print(MissingFileDetector().scan())

removed = cleanup.remove_missing_records()

print("\nRemoved :", removed)

print("\n" + "=" * 80)
print("AFTER")
print("=" * 80)

print(MissingFileDetector().scan())