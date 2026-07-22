"""
QA AI Studio
Tool Registry

Version: 1.1
"""

from Core.logger import Logger


class ToolRegistry:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.tools = []

    # ------------------------------------------

    def register(self, tool):

        self.tools.append(tool)

        self.logger.info(
            f"Registered Tool: {tool.name}"
        )

    # ------------------------------------------

    def unregister(self, tool_name):

        self.tools = [

            tool

            for tool in self.tools

            if tool.name != tool_name

        ]

        self.logger.info(
            f"Unregistered Tool: {tool_name}"
        )

    # ------------------------------------------

    def get_tool(self, prompt):

        for tool in self.tools:

            if tool.can_handle(prompt):

                return tool

        return None

    # ------------------------------------------

    def list_tools(self):

        return [

            tool.name

            for tool in self.tools

        ]

    # ------------------------------------------

    def exists(self, tool_name):

        return any(

            tool.name == tool_name

            for tool in self.tools

        )