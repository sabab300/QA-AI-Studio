"""
QA AI Studio
Retrieval Cache

Version: 1.0
"""

import hashlib
import time


class RetrievalCache:

    _cache = {}

    def __init__(self, ttl=600):
        self.ttl = ttl

    def _key(self, question):

        return hashlib.md5(
            question.lower().strip().encode()
        ).hexdigest()

    def get(self, question):

        key = self._key(question)

        item = self._cache.get(key)

        if item is None:
            return None

        if time.time() - item["time"] > self.ttl:

            del self._cache[key]

            return None

        return item["value"]

    def save(self, question, value):

        key = self._key(question)

        self._cache[key] = {

            "time": time.time(),

            "value": value

        }

    def clear(self):

        self._cache.clear()