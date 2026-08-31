"""
QA AI Studio
Generator Registry

Version: 1.0
"""

from Core.logger import Logger


class GeneratorRegistry:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.generators = {}

    # --------------------------------------------------

    def register(

        self,

        intent,

        generator

    ):

        self.generators[intent] = generator

        self.logger.info(
            f"Registered Generator: {intent}"
        )

    # --------------------------------------------------

    def get(

        self,

        intent

    ):

        return self.generators.get(intent)

    # --------------------------------------------------

    def exists(

        self,

        intent

    ):

        return intent in self.generators

    # --------------------------------------------------

    def unregister(

        self,

        intent

    ):

        if intent in self.generators:

            del self.generators[intent]

            self.logger.info(
                f"Unregistered Generator: {intent}"
            )

    # --------------------------------------------------

    def list_generators(self):

        return list(

            self.generators.keys()

        )

    # --------------------------------------------------

    def all(self):

        return self.generators