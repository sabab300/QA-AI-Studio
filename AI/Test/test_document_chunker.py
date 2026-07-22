from Core.document_chunker import DocumentChunker

chunker = DocumentChunker(
    chunk_size=50,
    overlap=10
)

text = """
Pakistan Single Window is developing an AI-powered QA Assistant.
This assistant can upload SRS, CRF, APIs, SQL files and Test Cases.
It generates embeddings, stores vectors and performs semantic search.
"""

chunks = chunker.split(text)

print("Total Chunks:", len(chunks))

for i, chunk in enumerate(chunks, start=1):

    print(f"\nChunk {i}")

    print(chunk)

print()

print(chunker.statistics(chunks))