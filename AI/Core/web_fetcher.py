"""
QA AI Studio
Web Fetcher

Version: 1.0
"""

import requests

from Core.logger import Logger


class WebFetcher:

    TIMEOUT = 20

    USER_AGENT = (
        "Mozilla/5.0 "
        "(Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 "
        "(KHTML, like Gecko) "
        "Chrome/137.0 Safari/537.36"
    )

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # GET
    # --------------------------------------------------

    def get(

        self,

        url,

        headers=None,

        params=None

    ):

        try:

            request_headers = {

                "User-Agent": self.USER_AGENT

            }

            if headers:

                request_headers.update(headers)

            response = requests.get(

                url,

                headers=request_headers,

                params=params,

                timeout=self.TIMEOUT

            )

            response.raise_for_status()

            return {

                "success": True,

                "status_code": response.status_code,

                "text": response.text,

                "url": response.url

            }

        except Exception as error:

            self.logger.exception(

                f"Web Fetch failed: {error}"

            )

            return {

                "success": False,

                "error": str(error)

            }