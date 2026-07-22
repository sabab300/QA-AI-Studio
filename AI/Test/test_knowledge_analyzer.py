from Core.text_extractor import TextExtractor
from Core.knowledge_analyzer import KnowledgeAnalyzer

extractor = TextExtractor()

text = extractor.extract_text("requirements.txt")

analyzer = KnowledgeAnalyzer()

result = analyzer.analyze(text)

print(result)