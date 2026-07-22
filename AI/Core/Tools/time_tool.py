"""
QA AI Studio
Time Tool
"""

from datetime import datetime

from Core.tool import Tool


class TimeTool(Tool):

    @property
    def name(self):
        return "Time Tool"

    def can_handle(self, prompt):

        prompt = prompt.lower().strip()

        # Only direct time/date questions
        valid_patterns = [
            "what time is it",
            "current time",
            "tell me the time",
            "what is the date today",
            "today's date",
            "current date",
            "what day is today"
        ]

        return any(
            pattern in prompt
            for pattern in valid_patterns
        )

    def execute(self, prompt):

        return {
            "success": True,
            "response": datetime.now().strftime(
                "%d-%b-%Y %I:%M %p"
            )
        }