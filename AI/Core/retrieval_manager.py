"""
QA AI Studio
Retrieval Manager

Version: 1.0
"""

from Core.logger import Logger
from Core.knowledge_router import KnowledgeRouter
from Core.rag_engine import RAGEngine
from Core.internet_search import InternetSearch


class RetrievalManager:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.router = KnowledgeRouter()

        self.rag = RAGEngine()

        self.internet = InternetSearch()

    # --------------------------------------------------
    # Retrieve Knowledge
    # --------------------------------------------------

    def retrieve(

        self,

        question

    ):

        route = self.router.route(question)

        self.logger.info(

            f"Retrieval Route: {route}"

        )

        # ---------------------------------------------
        # LOCAL KNOWLEDGE BASE
        # ---------------------------------------------

        if route == "local":

            result = self.rag.get_context(
                question=question
            )

            if isinstance(result, dict):

                result["route"] = "local"

            return result

        # ---------------------------------------------
        # GLOBAL KNOWLEDGE
        # ---------------------------------------------

        result = self.global_ai.answer(question)

        if isinstance(result, dict):

            result["route"] = "global"

        return result