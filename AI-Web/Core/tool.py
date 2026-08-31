"""
QA AI Studio
Tool Base Class
Version: 1.0
"""

from abc import ABC
from abc import abstractmethod


class Tool(ABC):

    @property
    @abstractmethod
    def name(self):
        pass

    @abstractmethod
    def can_handle(self, prompt):
        pass

    @abstractmethod
    def execute(self, prompt):
        pass