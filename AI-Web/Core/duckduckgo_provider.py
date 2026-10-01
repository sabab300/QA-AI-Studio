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

            # QA-AI-STUDIO-AI-ASSISTANT-PHASE2-INTERNET-SEARCH-FIX: this
            # used to send the bare header User-Agent: "Mozilla/5.0" — an
            # obviously non-browser signature — while Core/web_fetcher.py
            # (used a few lines below, to fetch each result's linked page)
            # already sends a full, realistic Chrome/Windows UA for the
            # exact same kind of request. DuckDuckGo's HTML endpoint
            # commonly responds to that bare signature with a 200 OK page
            # that simply contains zero `.result` elements (a block/
            # no-results page), which is indistinguishable at the
            # status-code level from a genuine no-results query — exactly
            # the silent failure this task reported. Reusing WebFetcher's
            # own already-working UA (imported, not duplicated) plus the
            # couple of standard headers a real browser sends brings this
            # request in line with the one that already works.
            response = requests.post(

                url,

                data={

                    "q": question

                },

                headers={

                    "User-Agent": WebFetcher.USER_AGENT,

                    "Accept":
                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",

                    "Accept-Language": "en-US,en;q=0.9",

                    "Referer": "https://duckduckgo.com/",

                },

                timeout=15

            )

            # [AI-DIAG] temporary diagnostic — exactly what DuckDuckGo
            # returned for this request (status, any redirect it followed,
            # content type, response size), so a future silent failure is
            # diagnosable from logs instead of re-reading this file.
            self.logger.info(
                "[AI-DIAG] DuckDuckGo HTTP response: status=%s final_url=%s "
                "content_type=%s bytes=%s" % (
                    response.status_code,
                    response.url,
                    response.headers.get("Content-Type"),
                    len(response.content or b""),
                )
            )


            if response.status_code != 200:

                return {

                    "success": False,

                    "context": "",

                    "references": [],

                    "provider": "duckduckgo",

                    "error":
                    f"DuckDuckGo request failed (HTTP {response.status_code})."

                }


            soup = BeautifulSoup(

                response.text,

                "html.parser"

            )


            results = soup.select(".result")[:5]

            # [AI-DIAG] temporary diagnostic — how many result elements
            # DuckDuckGo's HTML actually contained. Zero here means
            # DuckDuckGo itself returned a no-results/challenge page for
            # this request, not that this parser failed to read a normal
            # results page — the distinction Phase 3/4 asked to establish.
            self.logger.info(
                "[AI-DIAG] DuckDuckGo parsed result elements: %s"
                % len(results)
            )


            if not results:

                return {

                    "success": False,

                    "context": "",

                    "references": [],

                    "provider": "duckduckgo",

                    "error":
                    "No search results found."

                }


            # QA-AI-STUDIO-AI-ASSISTANT-PHASE2-INTERNET-SEARCH-FIX: this
            # used to keep a result ONLY if the linked external page could
            # be downloaded AND its extracted text was non-empty — so a
            # single slow, blocked, or JS-rendered source page discarded
            # that result entirely, even though DuckDuckGo's own results
            # page already handed back perfectly usable title + snippet
            # text for it at zero extra network cost. Each result's own
            # title/snippet is now captured first and used as a baseline;
            # a successful page fetch+extract enriches it, but a failed
            # fetch degrades gracefully to the snippet instead of dropping
            # the source. One bad source no longer invalidates the others,
            # and internet search stops failing merely because a page
            # download didn't work.
            contents = []

            references = []


            for result in results:

                link = result.select_one(

                    ".result__a"

                )

                if not link:

                    continue

                href = link.get("href")

                if not href:

                    continue

                title = link.get_text(strip=True)

                snippet_el = result.select_one(".result__snippet")

                snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                piece = "\n".join(
                    part for part in [title, snippet] if part
                ).strip()


                page = self.fetcher.get(

                    href

                )


                if page.get("success"):

                    text = self.parser.extract(

                        page.get("text", "")

                    )

                    clean = self.extractor.extract(

                        text

                    )

                    if clean:

                        piece = clean


                if piece:

                    contents.append(piece)

                    references.append(href)



            if not contents:

                return {

                    "success": False,

                    "context": "",

                    "references": [],

                    "provider": "duckduckgo",

                    "error":
                    "Search returned results but no usable title, snippet, "
                    "or page content."

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