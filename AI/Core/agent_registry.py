"""
QA AI Studio
Agent Registry
Version: 1.0
"""

from Core.logger import Logger


class AgentRegistry:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.agents = []

    # -----------------------------------------

    def register(self, agent):

        self.agents.append(agent)

        self.logger.info(
            f"Registered Agent: {agent.name}"
        )

    # -----------------------------------------

    def get(self, intent):

        for agent in self.agents:

            if agent.can_handle(intent):

                return agent

        return None