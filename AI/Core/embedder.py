from sentence_transformers import SentenceTransformer

# Load embedding model (downloads only first time)
print("Loading AI Embedding Model...")

model = SentenceTransformer("all-MiniLM-L6-v2")

print("Model Loaded Successfully.")


def create_embedding(text):
    """
    Convert text into embedding vector
    """

    embedding = model.encode(text)

    return embedding