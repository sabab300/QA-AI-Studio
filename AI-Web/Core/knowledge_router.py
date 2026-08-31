"""
QA AI Studio
Knowledge Router

Version: 1.0
"""

from Core.logger import Logger


class KnowledgeRouter:

    def __init__(self):
        self.logger = Logger.get_logger()

    def route(self, question: str):

        if not question:
            return "local"

        q = question.lower()

        local_keywords = [
            "test case",
            "bug",
            "sql",
            "api",
            "automation",
            "selenium",
            "requirement",
            "validation",
            "workflow",
            "screen",
            "module",
            "crf",
            "srs",
            "warehouse",
            "warehousing",
            "single declaration",
            "gd",
            "tp",
            "psw system"
        ]

        global_keywords = [
            "what is",
            "history",
            "who is",
            "when",
            "why",
            "internet",
            "website",
            "world",
            "wco",
            "weboc",
            "customs",
            "organization",
            "country"
        ]

        for word in local_keywords:
            if word in q:
                self.logger.info("Knowledge Router -> LOCAL")
                return "local"

        for word in global_keywords:
            if word in q:
                self.logger.info("Knowledge Router -> GLOBAL")
                return "global"

        self.logger.info("Knowledge Router -> LOCAL (default)")
        return "local"