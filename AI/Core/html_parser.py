"""
QA AI Studio
HTML Parser

Version: 1.0
"""

from bs4 import BeautifulSoup

from Core.logger import Logger


class HTMLParser:

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # Extract Text
    # --------------------------------------------------

    def extract(

        self,

        html

    ):

        try:

            if not html:

                return ""

            soup = BeautifulSoup(

                html,

                "html.parser"

            )

            for tag in soup(

                [

                    "script",

                    "style",

                    "noscript",

                    "header",

                    "footer",

                    "nav",

                    "svg"

                ]

            ):

                tag.decompose()

            text = soup.get_text(

                separator="\n"

            )

            lines = []

            previous = ""

            for line in text.splitlines():

                line = line.strip()

                if not line:

                    continue

                if line == previous:

                    continue

                previous = line

                lines.append(line)

            return "\n".join(lines)

        except Exception as error:

            self.logger.exception(

                f"HTML Parser failed: {error}"

            )

            return ""