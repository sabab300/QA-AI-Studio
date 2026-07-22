import sys

sys.path.append("AI")

from AI.Core.vector_store import save_document
from Core.embedder import create_embedding

text = "Pakistan Single Window"

embedding = create_embedding(text)

save_document(
    "Doc001",
    text,
    embedding
)

print("Database Test Successful")