import sys
import os

sys.path.append("AI")

from Core.embedder import create_embedding

text = "Pakistan Single Window improves trade."

vector = create_embedding(text)

print("\nEmbedding Created Successfully")

print(f"Vector Length : {len(vector)}")