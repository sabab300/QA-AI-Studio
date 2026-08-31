"""
QA AI Studio
Conversation Manager

Version: 2.0
Production Ready
"""

from collections import deque
from datetime import datetime
import uuid



class ConversationManager:


    def __init__(

        self,

        max_messages=20,

        max_content_length=4000

    ):


        self.conversation_id = str(
            uuid.uuid4()
        )


        self.max_messages = max_messages

        self.max_content_length = max_content_length


        self.messages = deque(
            maxlen=max_messages
        )



    # --------------------------------------------------
    # Start New Conversation
    # --------------------------------------------------

    def start_conversation(self):


        self.conversation_id = str(
            uuid.uuid4()
        )


        self.messages.clear()



    # --------------------------------------------------
    # Add User Message
    # --------------------------------------------------

    def add_user_message(

        self,

        message

    ):


        self._add_message(

            role="user",

            message=message

        )



    # --------------------------------------------------
    # Add AI Message
    # --------------------------------------------------

    def add_ai_message(

        self,

        message

    ):


        self._add_message(

            role="assistant",

            message=message

        )



    # --------------------------------------------------
    # Internal Add Message
    # --------------------------------------------------

    def _add_message(

        self,

        role,

        message

    ):


        if not message:

            return



        message = str(message).strip()



        if len(message) > self.max_content_length:

            message = (

                message[:self.max_content_length]

                +

                "\n...[truncated]"

            )



        self.messages.append(


            {


                "role": role,


                "content": message,


                "timestamp":

                datetime.now().isoformat()


            }


        )



    # --------------------------------------------------
    # Get History
    # --------------------------------------------------

    def get_history(self):


        if not self.messages:

            return ""



        history = []



        for item in self.messages:


            history.append(

                f'{item["role"].title()}: '
                f'{item["content"]}'

            )



        return "\n\n".join(history)



    # --------------------------------------------------
    # Get Raw Messages
    # --------------------------------------------------

    def get_messages(self):


        return list(
            self.messages
        )



    # --------------------------------------------------
    # Clear Conversation
    # --------------------------------------------------

    def clear_conversation(self):


        self.messages.clear()



    # --------------------------------------------------
    # Conversation Information
    # --------------------------------------------------

    def info(self):


        return {


            "conversation_id":

            self.conversation_id,


            "messages":

            len(self.messages),


            "max_messages":

            self.max_messages

        }