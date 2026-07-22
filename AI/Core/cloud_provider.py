"""
QA AI Studio
Cloud AI Provider

Version: 1.0
"""

from Core.ai_provider import AIProvider
from Core.logger import Logger


class CloudProvider(AIProvider):

    def __init__(self):

        self.logger = Logger.get_logger()

    # --------------------------------------------------

    @property
    def provider_name(self):

        return "Cloud"

    # --------------------------------------------------

    def is_available(self):

        return False

    # --------------------------------------------------

    def generate(

        self,

        prompt,

        system_prompt=None,

        temperature=0.2,

        max_tokens=2048

    ):

        return {

            "success": False,

            "provider": self.provider_name,

            "error": "Cloud provider is not configured."

        }