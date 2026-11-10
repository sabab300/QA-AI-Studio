"""Answer cache with permission / version / config-aware keys.

* Exact cache only by default. Keyed by: normalised question, scope filters,
  knowledge token (vector count + chroma file mtimes -> changes on ANY
  ingest/delete/update), model configuration, settings hash, mode and the
  requesting user's id + role. One user can therefore never receive another
  user's cached answer, and any knowledge change invalidates everything.
* Never stores failures, insufficient-evidence answers, tool-augmented
  answers or answers that used internet text (those change over time).
* Optional semantic cache (off by default): per-user + per-scope, cosine
  >= threshold on query embeddings, same invalidation key.
"""

import hashlib
import json
import threading
import time
from collections import OrderedDict


def settings_fingerprint(settings):
    """Stable hash of the settings that influence an answer."""
    relevant = {
        "rag": settings.get("rag"),
        "tokens": {k: v for k, v in (settings.get("tokens") or {}).items()
                   if k not in ("exact_cache", "semantic_cache", "usage_tracking")},
        "routing": {k: v for k, v in (settings.get("routing") or {}).items()
                    if k in ("task_routes", "default_provider_id", "default_model",
                             "fallback", "internet_search_enabled",
                             "hybrid_use_internet")},
    }
    raw = json.dumps(relevant, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


class AnswerCache:

    def __init__(self):
        self._lock = threading.Lock()
        self._items = OrderedDict()
        self.hits = 0
        self.misses = 0

    @staticmethod
    def make_key(question, scope, knowledge_token, model_cfg, settings_fp,
                 mode, user_id, role):
        raw = json.dumps([
            " ".join((question or "").lower().split()), scope, knowledge_token,
            model_cfg, settings_fp, mode, str(user_id), str(role)],
            sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, key, ttl_s):
        with self._lock:
            item = self._items.get(key)
            if not item:
                self.misses += 1
                return None
            if time.time() - item["t"] > ttl_s:
                del self._items[key]
                self.misses += 1
                return None
            self._items.move_to_end(key)
            self.hits += 1
            return json.loads(item["v"])

    def put(self, key, value, max_items):
        with self._lock:
            self._items[key] = {"t": time.time(), "v": json.dumps(value, default=str)}
            self._items.move_to_end(key)
            while len(self._items) > max_items:
                self._items.popitem(last=False)

    def clear(self):
        with self._lock:
            self._items.clear()

    def stats(self):
        with self._lock:
            return {"items": len(self._items), "hits": self.hits,
                    "misses": self.misses}


ANSWER_CACHE = AnswerCache()


class SemanticCache:
    """Per-user, per-scope near-duplicate question cache (opt-in)."""

    def __init__(self):
        self._lock = threading.Lock()
        self._items = []   # dicts: partition, vec, t, value

    @staticmethod
    def _cos(a, b):
        num = sum(x * y for x, y in zip(a, b))
        da = sum(x * x for x in a) ** 0.5
        db = sum(y * y for y in b) ** 0.5
        return num / (da * db) if da and db else 0.0

    def get(self, partition, vec, threshold, ttl_s):
        now = time.time()
        with self._lock:
            self._items = [i for i in self._items if now - i["t"] <= ttl_s]
            best, best_s = None, 0.0
            for item in self._items:
                if item["partition"] != partition:
                    continue
                s = self._cos(vec, item["vec"])
                if s > best_s:
                    best, best_s = item, s
            if best is not None and best_s >= threshold:
                return json.loads(best["value"]), best_s
        return None, best_s

    def put(self, partition, vec, value, max_items=200):
        with self._lock:
            self._items.append({"partition": partition, "vec": list(vec),
                                "t": time.time(),
                                "value": json.dumps(value, default=str)})
            self._items = self._items[-max_items:]

    def clear(self):
        with self._lock:
            self._items.clear()


SEMANTIC_CACHE = SemanticCache()
