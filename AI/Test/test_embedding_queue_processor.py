from Core.embedding_queue_processor import EmbeddingQueueProcessor
from Core.embedding_manager import EmbeddingManager

processor = EmbeddingQueueProcessor()

print("=" * 80)
print("PROCESSING")
print("=" * 80)

count = processor.process()

print("Processed :", count)

print()

print("=" * 80)
print("PENDING AFTER PROCESS")
print("=" * 80)

manager = EmbeddingManager()

print(manager.pending())