"""
QA AI Studio
Content Extractor

Version: 1.0
"""

from Core.logger import Logger


class ContentExtractor:

    MAX_CHARACTERS = 5000

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------
    # Extract Clean Content
    # --------------------------------------------------

    def extract(

        self,

        text

    ):

        try:

            if not text:

                return ""

            lines = []

            seen = set()

            for line in text.splitlines():

                line = line.strip()

                if not line:

                    continue

                normalized = line.lower()

                if normalized in seen:

                    continue

                seen.add(normalized)

                lines.append(line)


            content = "\n".join(lines)


            if len(content) > self.MAX_CHARACTERS:

                content = content[:self.MAX_CHARACTERS]


            return content.strip()


        except Exception as error:

            self.logger.exception(

                f"Content extraction failed: {error}"

            )

            return ""