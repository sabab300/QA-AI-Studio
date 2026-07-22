"""
QA AI Studio
Base AI Agent
Version: 1.0
"""

from abc import ABC
from abc import abstractmethod


class Agent(ABC):

    @property
    @abstractmethod
    def name(self):
        pass

    @abstractmethod
    def can_handle(self, intent):
        pass

    @abstractmethod
    def execute(self, request):
        pass