"""
QA AI Studio
LLM Cache

Version: 3.0
Production Ready
"""

import hashlib
import time
import threading

from Config import settings
from Core.logger import Logger


class LLMCache:

    _cache = {}

    _lock = threading.Lock()


    def __init__(

        self,

        ttl=None,

        max_size=None

    ):

        self.logger = Logger.get_logger()

        self.enabled = getattr(
            settings,
            "LLM_CACHE_ENABLED",
            True
        )

        self.ttl = ttl or getattr(
            settings,
            "LLM_CACHE_TTL",
            600
        )

        self.max_size = max_size or getattr(
            settings,
            "LLM_CACHE_MAX_SIZE",
            500
        )

        self.hits = 0

        self.misses = 0


        self.logger.info(
            "LLM Cache initialized | Enabled=%s | TTL=%ss | Max=%s",
            self.enabled,
            self.ttl,
            self.max_size
        )


    # --------------------------------------------------
    # Generate Key
    # --------------------------------------------------

    def _key(self, value):

        if not value:

            value = ""


        return hashlib.sha256(

            value.lower()
            .strip()
            .encode("utf-8")

        ).hexdigest()


    # --------------------------------------------------
    # Get Cache
    # --------------------------------------------------

    def get(self, question):

        if not self.enabled:

            return None


        key = self._key(question)


        with self._lock:

            item = self._cache.get(
                key
            )


            if item is None:

                self.misses += 1

                return None


            expired = (

                time.time()
                -
                item["time"]

                >

                self.ttl

            )


            if expired:

                del self._cache[key]

                self.misses += 1

                return None


            self.hits += 1


            self.logger.debug(
                "LLM Cache Hit"
            )


            return item["value"]


    # --------------------------------------------------
    # Save Cache
    # --------------------------------------------------

    def save(

        self,

        question,

        value

    ):

        if not self.enabled:

            return


        key = self._key(question)


        with self._lock:


            if len(self._cache) >= self.max_size:


                oldest_key = min(

                    self._cache,

                    key=lambda item:
                    self._cache[item]["time"]

                )


                del self._cache[oldest_key]


            self._cache[key] = {

                "time": time.time(),

                "value": value

            }



    # --------------------------------------------------
    # Remove Expired Items
    # --------------------------------------------------

    def cleanup(self):

        removed = 0


        with self._lock:

            current = time.time()


            expired_keys = [

                key

                for key, item in self._cache.items()

                if current - item["time"] > self.ttl

            ]


            for key in expired_keys:

                del self._cache[key]

                removed += 1



        if removed:

            self.logger.info(
                "LLM Cache cleanup removed %s items",
                removed
            )


        return removed



    # --------------------------------------------------
    # Clear Cache
    # --------------------------------------------------

    def clear(self):

        with self._lock:

            self._cache.clear()

            self.hits = 0

            self.misses = 0



        self.logger.info(
            "LLM Cache cleared"
        )


    # --------------------------------------------------
    # Statistics
    # --------------------------------------------------

    def stats(self):

        total = (

            self.hits

            +

            self.misses

        )


        hit_rate = 0


        if total:

            hit_rate = round(

                (

                    self.hits

                    /

                    total

                )

                *

                100,

                2

            )


        return {

            "enabled": self.enabled,

            "items": len(self._cache),

            "hits": self.hits,

            "misses": self.misses,

            "hit_rate": f"{hit_rate}%",

            "ttl": self.ttl

        }