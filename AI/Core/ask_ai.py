from Core.embedder import create_embedding
from Core.search import search_knowledge


def ask_ai(question):

    embedding = create_embedding(question)

    results = search_knowledge(embedding)

    if results["documents"] and len(results["documents"][0]) > 0:
        return results["documents"][0][0]

    return "No matching knowledge found."