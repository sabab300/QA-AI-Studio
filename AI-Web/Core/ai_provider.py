"""
QA AI Studio
AI Provider Interface

Version: 1.0
"""

from abc import ABC
from abc import abstractmethod


class AIProvider(ABC):

    """
    Base interface for every AI provider.

    Examples
    --------
    - Ollama
    - OpenAI
    - Gemini
    - Claude
    """

    @property
    @abstractmethod
    def provider_name(self):
        pass

    @abstractmethod
    def is_available(self):
        pass

    @abstractmethod
    def generate(

        self,

        prompt,

        system_prompt=None,

        temperature=0.2,

        max_tokens=2048

    ):
        pass