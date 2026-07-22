"""
QA AI Studio
Cache Manager

Version: 1.0
"""

import hashlib
import time


class CacheManager:

    def __init__(self):

        self.cache = {}

    # --------------------------------------------------

    def _key(

        self,

        question

    ):

        return hashlib.sha256(

            question.strip().lower().encode()

        ).hexdigest()

    # --------------------------------------------------

    def get(

        self,

        question

    ):

        key = self._key(question)

        return self.cache.get(key)

    # --------------------------------------------------

    def save(

        self,

        question,

        response

    ):

        key = self._key(question)

        self.cache[key] = {

            "response": response,

            "timestamp": time.time()

        }

    # --------------------------------------------------

    def clear(self):

        self.cache.clear()