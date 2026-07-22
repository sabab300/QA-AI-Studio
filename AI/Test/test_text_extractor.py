from Core.text_extractor import TextExtractor

extractor = TextExtractor()

text = extractor.extract_text("requirements.txt")

print("=" * 60)
print(text[:500])
print("=" * 60)