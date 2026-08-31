"""
QA AI Studio
Internet Search Provider

Version: 2.0
"""

from Core.logger import Logger
from Core.duckduckgo_provider import DuckDuckGoProvider


class InternetSearch:

    DEFAULT_PROVIDER = "duckduckgo"

    def __init__(self):

        self.logger = Logger.get_logger()

        self.duckduckgo = DuckDuckGoProvider()

    # --------------------------------------------------
    # Search
    # --------------------------------------------------

    def search(

        self,

        question,

        provider=None

    ):

        if provider is None:

            provider = self.DEFAULT_PROVIDER

        self.logger.info(

            f"Internet Search ({provider}): {question}"

        )

        try:

            provider = provider.lower()

            if provider == "duckduckgo":

                return self._search_duckduckgo(question)

            if provider == "tavily":

                return self._search_tavily(question)

            if provider == "serpapi":

                return self._search_serpapi(question)

            if provider == "psw":

                return self._search_psw(question)

            return {

                "success": False,

                "context": "",

                "references": [],

                "provider": provider,

                "error": "Unknown internet provider."

            }

        except Exception as error:

            self.logger.exception(

                f"Internet Search failed: {error}"

            )

            return {

                "success": False,

                "context": "",

                "references": [],

                "provider": provider,

                "error": str(error)

            }

    # --------------------------------------------------
    # DuckDuckGo
    # --------------------------------------------------

    def _search_duckduckgo(

    self,

    question

    ):

        return self.duckduckgo.search(

        question

    )

    # --------------------------------------------------
    # Tavily
    # --------------------------------------------------

    def _search_tavily(

        self,

        question

    ):

        return {

            "success": False,

            "context": "",

            "references": [],

            "provider": "tavily",

            "error": "Tavily provider is not implemented yet."

        }

    # --------------------------------------------------
    # SerpAPI
    # --------------------------------------------------

    def _search_serpapi(

        self,

        question

    ):

        return {

            "success": False,

            "context": "",

            "references": [],

            "provider": "serpapi",

            "error": "SerpAPI provider is not implemented yet."

        }

    # --------------------------------------------------
    # PSW Search
    # --------------------------------------------------

    def _search_psw(

        self,

        question

    ):

        return {

            "success": False,

            "context": "",

            "references": [],

            "provider": "psw",

            "error": "PSW Search provider is not implemented yet."

        }