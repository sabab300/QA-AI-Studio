from Core.missing_file_detector import MissingFileDetector

detector = MissingFileDetector()

print("=" * 80)
print("MISSING FILES")
print("=" * 80)

missing = detector.scan()

if not missing:

    print("No missing files.")

else:

    for item in missing:

        print(item)