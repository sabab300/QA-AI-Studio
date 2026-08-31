"""
QA AI Studio
Global Context Builder

Version: 2.0
"""

from Config import settings
from Core.logger import Logger


class GlobalContextBuilder:

    def __init__(self):

        self.logger = Logger.get_logger()

        profile = getattr(
            settings,
            "LLM_PROFILE",
            "balanced"
        )

        limits = {
            "fast": 1000,
            "balanced": 2000,
            "quality": 4000
        }

        self.max_context_length = limits.get(
            profile,
            2000
        )

    # --------------------------------------------------
    # Build Context
    # --------------------------------------------------

    def build(

        self,

        context

    ):

        try:

            if not context:
                return ""

            if isinstance(context, list):

                context = "\n\n".join(

                    str(item)

                    for item in context

                    if item

                )

            context = str(context).strip()

            if len(context) > self.max_context_length:

                cut = context[:self.max_context_length]

                last_break = max(

                    cut.rfind("."),

                    cut.rfind("\n"),

                    cut.rfind(" ")

                )

                if last_break > 0:
                    cut = cut[:last_break]

                context = cut

            self.logger.info(

                f"Global context prepared ({len(context)} characters)."

            )

            return context

        except Exception as error:

            self.logger.exception(

                f"Global Context Builder failed: {error}"

            )

            return ""