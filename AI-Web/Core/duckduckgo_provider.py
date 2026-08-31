"""
QA AI Studio
DuckDuckGo Provider

Version: 2.0
"""

import requests
from bs4 import BeautifulSoup

from Core.logger import Logger
from Core.web_fetcher import WebFetcher
from Core.html_parser import HTMLParser
from Core.content_extractor import ContentExtractor


class DuckDuckGoProvider:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.fetcher = WebFetcher()

        self.parser = HTMLParser()

        self.extractor = ContentExtractor()


    # --------------------------------------------------
    # Search
    # --------------------------------------------------

    def search(

        self,

        question

    ):

        try:

            self.logger.info(

                f"DuckDuckGo Search: {question}"

            )


            url = "https://html.duckduckgo.com/html/"


            response = requests.post(

                url,

                data={

                    "q": question

                },

                headers={

                    "User-Agent":
                    "Mozilla/5.0"

                },

                timeout=15

            )


            if response.status_code != 200:

                return {

                    "success": False,

                    "context": "",

                    "references": [],

                    "provider": "duckduckgo",

                    "error":
                    "DuckDuckGo request failed."

                }


            soup = BeautifulSoup(

                response.text,

                "html.parser"

            )


            links = []


            for result in soup.select(".result")[:3]:

                link = result.select_one(

                    ".result__a"

                )

                if link:

                    href = link.get("href")

                    if href:

                        links.append(href)



            if not links:

                return {

                    "success": False,

                    "context": "",

                    "references": [],

                    "provider": "duckduckgo",

                    "error":
                    "No search results found."

                }


            contents = []

            references = []


            for link in links:


                page = self.fetcher.get(

                    link

                )


                if not page.get("success"):

                    continue



                text = self.parser.extract(

                    page.get("text","")

                )


                clean = self.extractor.extract(

                    text

                )


                if clean:

                    contents.append(clean)

                    references.append(link)



            if not contents:

                return {

                    "success": False,

                    "context": "",

                    "references": [],

                    "provider": "duckduckgo",

                    "error":
                    "Unable to extract web content."

                }



            return {

                "success": True,

                "context": "\n\n".join(contents),

                "references": references,

                "provider": "duckduckgo"

            }


        except Exception as error:


            self.logger.exception(

                f"DuckDuckGo Provider failed: {error}"

            )


            return {

                "success": False,

                "context": "",

                "references": [],

                "provider": "duckduckgo",

                "error": str(error)

            }