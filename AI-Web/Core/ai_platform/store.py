"""
PlatformStore - persistence for the AI platform.

All AI-platform configuration lives in its OWN SQLite file
(``Database/ai_platform.db``). The existing ``metadata.db`` (knowledge,
documents, ownership, test cases) and ChromaDB are never touched, so there is
no schema migration of existing data and switching LLMs cannot affect stored
knowledge or embeddings.
"""

import copy
import json
import sqlite3
import threading
import time
from pathlib import Path

from Core.ai_platform import constants as C
from Core.ai_platform.secret_box import SecretBox, mask_preview


def default_db_path():
    return Path(__file__).resolve().parents[2] / "Database" / "ai_platform.db"


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


_SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_providers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    adapter TEXT NOT NULL,
    kind TEXT NOT NULL,
    base_url TEXT NOT NULL,
    secret_blob TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    timeout_s INTEGER NOT NULL DEFAULT 300,
    params_json TEXT NOT NULL DEFAULT '{}',
    limits_json TEXT NOT NULL DEFAULT '{}',
    allow_private_data INTEGER NOT NULL DEFAULT 0,
    allow_public_data INTEGER NOT NULL DEFAULT 0,
    default_model TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'unknown',
    last_checked TEXT,
    last_error TEXT,
    is_builtin INTEGER NOT NULL DEFAULT 0,
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS llm_models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id INTEGER NOT NULL,
    model_id TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    capabilities_json TEXT NOT NULL DEFAULT '{}',
    context_limit INTEGER NOT NULL DEFAULT 0,
    max_output INTEGER NOT NULL DEFAULT 0,
    params_json TEXT NOT NULL DEFAULT '{}',
    discovered INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    UNIQUE(provider_id, model_id)
);
CREATE TABLE IF NOT EXISTS llm_settings (
    section TEXT PRIMARY KEY,
    value_json TEXT NOT NULL,
    modified_by TEXT, modified_at TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, username TEXT, task TEXT, mode TEXT,
    provider TEXT, model TEXT,
    prompt_tokens INTEGER, completion_tokens INTEGER,
    latency_ms INTEGER, cached INTEGER, success INTEGER,
    error TEXT, cloud INTEGER, fallback INTEGER
);
CREATE TABLE IF NOT EXISTS llm_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, username TEXT, action TEXT, target TEXT, detail TEXT
);
CREATE TABLE IF NOT EXISTS mcp_integrations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    transport TEXT NOT NULL,
    command TEXT NOT NULL DEFAULT '',
    args_json TEXT NOT NULL DEFAULT '[]',
    url TEXT NOT NULL DEFAULT '',
    secret_blob TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 0,
    allowed_modes_json TEXT NOT NULL DEFAULT '["local"]',
    allow_write INTEGER NOT NULL DEFAULT 0,
    tool_permissions_json TEXT NOT NULL DEFAULT '{}',
    timeout_s INTEGER NOT NULL DEFAULT 20,
    status TEXT NOT NULL DEFAULT 'unknown',
    last_checked TEXT, last_error TEXT,
    tools_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS mcp_tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT, username TEXT, integration TEXT, tool TEXT,
    args_summary TEXT, success INTEGER, duration_ms INTEGER,
    error TEXT, initiated_by TEXT
);
CREATE INDEX IF NOT EXISTS idx_usage_ts ON llm_usage(ts);
"""


class PlatformStore:

    def __init__(self, db_path=None, secret_box=None):

        self.db_path = Path(db_path) if db_path else default_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._box = secret_box
        self._settings_cache = None
        self._init()

    # ------------------------------------------------------------ plumbing

    @property
    def box(self):
        if self._box is None:
            self._box = SecretBox()
        return self._box

    def _conn(self):
        conn = sqlite3.connect(str(self.db_path), timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _init(self):
        with self._lock:
            conn = self._conn()
            try:
                conn.executescript(_SCHEMA)
                conn.commit()
            finally:
                conn.close()

    def _exec(self, sql, params=(), fetch=None):
        with self._lock:
            conn = self._conn()
            try:
                cur = conn.execute(sql, params)
                if fetch == "all":
                    return [dict(r) for r in cur.fetchall()]
                if fetch == "one":
                    row = cur.fetchone()
                    return dict(row) if row else None
                conn.commit()
                return cur.lastrowid
            finally:
                conn.close()

    # ------------------------------------------------------------ providers

    @staticmethod
    def _provider_row(row, include_secret=False, box=None):

        if row is None:
            return None

        out = {
            "id": row["id"],
            "name": row["name"],
            "adapter": row["adapter"],
            "kind": row["kind"],
            "base_url": row["base_url"],
            "enabled": bool(row["enabled"]),
            "timeout_s": row["timeout_s"],
            "params": json.loads(row["params_json"] or "{}"),
            "limits": json.loads(row["limits_json"] or "{}"),
            "permissions": {
                "allow_private_data": bool(row["allow_private_data"]),
                "allow_public_data": bool(row["allow_public_data"]),
            },
            "default_model": row["default_model"],
            "status": row["status"],
            "last_checked": row["last_checked"],
            "last_error": row["last_error"],
            "is_builtin": bool(row["is_builtin"]),
            "has_secret": bool(row["secret_blob"]),
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

        if include_secret:
            out["_api_key"] = box.decrypt(row["secret_blob"]) if box else ""

        return out

    def list_providers(self):
        rows = self._exec("SELECT * FROM llm_providers ORDER BY id", fetch="all")
        return [self._provider_row(r) for r in rows]

    def get_provider(self, provider_id, include_secret=False):
        row = self._exec("SELECT * FROM llm_providers WHERE id=?",
                         (provider_id,), fetch="one")
        return self._provider_row(row, include_secret, self.box)

    def get_provider_by_name(self, name):
        row = self._exec("SELECT * FROM llm_providers WHERE name=?",
                         (name,), fetch="one")
        return self._provider_row(row)

    def create_provider(self, data):

        adapter = data["adapter"]
        kind, default_url, _needs_key = C.ADAPTERS[adapter]
        now = _now()

        return self._exec(
            """INSERT INTO llm_providers
               (name, adapter, kind, base_url, secret_blob, enabled, timeout_s,
                params_json, limits_json, allow_private_data, allow_public_data,
                default_model, is_builtin, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                data["name"], adapter, data.get("kind") or kind,
                data.get("base_url") or default_url,
                self.box.encrypt(data.get("api_key") or ""),
                1 if data.get("enabled", True) else 0,
                int(data.get("timeout_s") or 300),
                json.dumps(data.get("params") or {}),
                json.dumps(data.get("limits") or {}),
                1 if (data.get("permissions") or {}).get("allow_private_data") else 0,
                1 if (data.get("permissions") or {}).get("allow_public_data") else 0,
                data.get("default_model") or "",
                1 if data.get("is_builtin") else 0,
                now, now,
            ),
        )

    def update_provider(self, provider_id, data):

        current = self._exec("SELECT * FROM llm_providers WHERE id=?",
                             (provider_id,), fetch="one")
        if not current:
            return False

        sets, params = [], []

        def put(column, value):
            sets.append(f"{column}=?")
            params.append(value)

        for key in ("name", "base_url", "default_model"):
            if key in data and data[key] is not None:
                put(key, data[key])
        if "kind" in data and data["kind"]:
            put("kind", data["kind"])
        if "enabled" in data and data["enabled"] is not None:
            put("enabled", 1 if data["enabled"] else 0)
        if "timeout_s" in data and data["timeout_s"] is not None:
            put("timeout_s", int(data["timeout_s"]))
        if "params" in data and data["params"] is not None:
            put("params_json", json.dumps(data["params"]))
        if "limits" in data and data["limits"] is not None:
            put("limits_json", json.dumps(data["limits"]))
        perms = data.get("permissions")
        if isinstance(perms, dict):
            if "allow_private_data" in perms:
                put("allow_private_data", 1 if perms["allow_private_data"] else 0)
            if "allow_public_data" in perms:
                put("allow_public_data", 1 if perms["allow_public_data"] else 0)
        # api_key: None/missing = keep, "" with clear_key = remove, value = replace
        if data.get("clear_key"):
            put("secret_blob", "")
        elif data.get("api_key"):
            put("secret_blob", self.box.encrypt(data["api_key"]))

        if not sets:
            return True

        put("updated_at", _now())
        params.append(provider_id)
        self._exec(f"UPDATE llm_providers SET {', '.join(sets)} WHERE id=?",
                   tuple(params))
        return True

    def set_provider_status(self, provider_id, status, error=None):
        self._exec(
            "UPDATE llm_providers SET status=?, last_checked=?, last_error=? WHERE id=?",
            (status, _now(), (error or "")[:300], provider_id),
        )

    def delete_provider(self, provider_id):
        row = self._exec("SELECT is_builtin FROM llm_providers WHERE id=?",
                         (provider_id,), fetch="one")
        if not row:
            return "missing"
        if row["is_builtin"]:
            return "builtin"
        self._exec("DELETE FROM llm_models WHERE provider_id=?", (provider_id,))
        self._exec("DELETE FROM llm_providers WHERE id=?", (provider_id,))
        return "deleted"

    # --------------------------------------------------------------- models

    @staticmethod
    def _model_row(r):
        return {
            "id": r["id"],
            "provider_id": r["provider_id"],
            "model_id": r["model_id"],
            "display_name": r["display_name"] or r["model_id"],
            "capabilities": json.loads(r["capabilities_json"] or "{}"),
            "context_limit": r["context_limit"],
            "max_output": r["max_output"],
            "params": json.loads(r["params_json"] or "{}"),
            "discovered": bool(r["discovered"]),
            "enabled": bool(r["enabled"]),
        }

    def list_models(self, provider_id=None):
        if provider_id is None:
            rows = self._exec("SELECT * FROM llm_models ORDER BY provider_id, model_id",
                              fetch="all")
        else:
            rows = self._exec(
                "SELECT * FROM llm_models WHERE provider_id=? ORDER BY model_id",
                (provider_id,), fetch="all")
        return [self._model_row(r) for r in rows]

    def get_model(self, provider_id, model_id):
        r = self._exec(
            "SELECT * FROM llm_models WHERE provider_id=? AND model_id=?",
            (provider_id, model_id), fetch="one")
        return self._model_row(r) if r else None

    def upsert_model(self, provider_id, model_id, **fields):

        existing = self.get_model(provider_id, model_id)

        if existing is None:
            self._exec(
                """INSERT INTO llm_models
                   (provider_id, model_id, display_name, capabilities_json,
                    context_limit, max_output, params_json, discovered, enabled)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    provider_id, model_id,
                    fields.get("display_name") or model_id,
                    json.dumps(fields.get("capabilities") or {}),
                    int(fields.get("context_limit") or 0),
                    int(fields.get("max_output") or 0),
                    json.dumps(fields.get("params") or {}),
                    1 if fields.get("discovered") else 0,
                    1 if fields.get("enabled", True) else 0,
                ),
            )
            return

        sets, params = [], []
        mapping = {
            "display_name": ("display_name", lambda v: v),
            "context_limit": ("context_limit", int),
            "max_output": ("max_output", int),
            "capabilities": ("capabilities_json", json.dumps),
            "params": ("params_json", json.dumps),
            "enabled": ("enabled", lambda v: 1 if v else 0),
            "discovered": ("discovered", lambda v: 1 if v else 0),
        }
        for key, (column, conv) in mapping.items():
            if key in fields and fields[key] is not None:
                sets.append(f"{column}=?")
                params.append(conv(fields[key]))
        if sets:
            params += [provider_id, model_id]
            self._exec(
                f"UPDATE llm_models SET {', '.join(sets)} "
                "WHERE provider_id=? AND model_id=?", tuple(params))

    def delete_model(self, provider_id, model_id):
        self._exec("DELETE FROM llm_models WHERE provider_id=? AND model_id=?",
                   (provider_id, model_id))

    # ------------------------------------------------------------- settings

    def get_settings(self):
        """Return all settings sections merged over defaults (deep copy)."""

        with self._lock:
            if self._settings_cache is not None:
                return copy.deepcopy(self._settings_cache)

            merged = copy.deepcopy(C.DEFAULT_SETTINGS)
            rows = self._exec("SELECT section, value_json FROM llm_settings",
                              fetch="all")
            for row in rows:
                if row["section"] in merged:
                    try:
                        stored = json.loads(row["value_json"])
                    except ValueError:
                        continue
                    _deep_merge(merged[row["section"]], stored)

            self._settings_cache = merged
            return copy.deepcopy(merged)

    def get_section(self, section):
        return self.get_settings()[section]

    def save_section(self, section, value, username=""):
        self._exec(
            """INSERT INTO llm_settings(section, value_json, modified_by, modified_at)
               VALUES (?,?,?,?)
               ON CONFLICT(section) DO UPDATE SET value_json=excluded.value_json,
                 modified_by=excluded.modified_by, modified_at=excluded.modified_at""",
            (section, json.dumps(value), username, _now()),
        )
        with self._lock:
            self._settings_cache = None

    def settings_meta(self):
        rows = self._exec(
            "SELECT section, modified_by, modified_at FROM llm_settings",
            fetch="all")
        return {r["section"]: r for r in rows}

    # -------------------------------------------------------- usage / audit

    def record_usage(self, **f):
        try:
            self._exec(
                """INSERT INTO llm_usage
                   (ts, username, task, mode, provider, model, prompt_tokens,
                    completion_tokens, latency_ms, cached, success, error,
                    cloud, fallback)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    _now(), f.get("username", ""), f.get("task", ""),
                    f.get("mode", ""), f.get("provider", ""), f.get("model", ""),
                    int(f.get("prompt_tokens") or 0),
                    int(f.get("completion_tokens") or 0),
                    int(f.get("latency_ms") or 0),
                    1 if f.get("cached") else 0,
                    1 if f.get("success") else 0,
                    (f.get("error") or "")[:300],
                    1 if f.get("cloud") else 0,
                    1 if f.get("fallback") else 0,
                ),
            )
        except Exception:
            pass  # usage tracking must never break an answer

    def usage_summary(self, days=7):
        since = time.strftime("%Y-%m-%d %H:%M:%S",
                              time.localtime(time.time() - days * 86400))
        total = self._exec(
            """SELECT COUNT(*) AS requests,
                      COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                      COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                      COALESCE(AVG(latency_ms),0) AS avg_latency_ms,
                      COALESCE(SUM(cached),0) AS cache_hits,
                      COALESCE(SUM(cloud),0) AS cloud_requests,
                      COALESCE(SUM(fallback),0) AS fallbacks,
                      COALESCE(SUM(1-success),0) AS failures
               FROM llm_usage WHERE ts>=?""", (since,), fetch="one")
        per_model = self._exec(
            """SELECT provider, model, COUNT(*) AS requests,
                      COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,
                      COALESCE(SUM(completion_tokens),0) AS completion_tokens,
                      COALESCE(AVG(latency_ms),0) AS avg_latency_ms
               FROM llm_usage WHERE ts>=? GROUP BY provider, model
               ORDER BY requests DESC LIMIT 20""", (since,), fetch="all")
        total["avg_latency_ms"] = int(total["avg_latency_ms"])
        for r in per_model:
            r["avg_latency_ms"] = int(r["avg_latency_ms"])
        return {"days": days, "totals": total, "per_model": per_model}

    def write_audit(self, username, action, target="", detail=""):
        try:
            self._exec(
                "INSERT INTO llm_audit(ts, username, action, target, detail) VALUES (?,?,?,?,?)",
                (_now(), username or "", action, target or "", (detail or "")[:500]),
            )
        except Exception:
            pass

    def list_audit(self, limit=100):
        return self._exec(
            "SELECT ts, username, action, target, detail FROM llm_audit "
            "ORDER BY id DESC LIMIT ?", (int(limit),), fetch="all")

    # ------------------------------------------------------------------ mcp

    def _mcp_row(self, r):
        return {
            "id": r["id"], "name": r["name"], "transport": r["transport"],
            "command": r["command"], "args": json.loads(r["args_json"] or "[]"),
            "url": r["url"], "has_secret": bool(r["secret_blob"]),
            "enabled": bool(r["enabled"]),
            "allowed_modes": json.loads(r["allowed_modes_json"] or "[]"),
            "allow_write": bool(r["allow_write"]),
            "tool_permissions": json.loads(r["tool_permissions_json"] or "{}"),
            "timeout_s": r["timeout_s"], "status": r["status"],
            "last_checked": r["last_checked"], "last_error": r["last_error"],
            "tools": json.loads(r["tools_json"] or "[]"),
            "created_at": r["created_at"], "updated_at": r["updated_at"],
        }

    def list_mcp(self):
        return [self._mcp_row(r) for r in
                self._exec("SELECT * FROM mcp_integrations ORDER BY id", fetch="all")]

    def get_mcp(self, integration_id, include_secret=False):
        r = self._exec("SELECT * FROM mcp_integrations WHERE id=?",
                       (integration_id,), fetch="one")
        if not r:
            return None
        out = self._mcp_row(r)
        if include_secret:
            raw = self.box.decrypt(r["secret_blob"]) if r["secret_blob"] else ""
            try:
                out["_secrets"] = json.loads(raw) if raw else {}
            except ValueError:
                out["_secrets"] = {}
        return out

    def save_mcp(self, data, integration_id=None):
        """Create or update an integration. ``secrets`` (dict of env vars or
        HTTP headers) is stored encrypted and never returned."""

        now = _now()
        fields = {
            "name": data.get("name"),
            "transport": data.get("transport"),
            "command": data.get("command"),
            "args_json": json.dumps(data["args"]) if "args" in data else None,
            "url": data.get("url"),
            "enabled": (1 if data["enabled"] else 0) if "enabled" in data else None,
            "allowed_modes_json": (json.dumps(data["allowed_modes"])
                                   if "allowed_modes" in data else None),
            "allow_write": (1 if data["allow_write"] else 0)
                           if "allow_write" in data else None,
            "tool_permissions_json": (json.dumps(data["tool_permissions"])
                                      if "tool_permissions" in data else None),
            "timeout_s": int(data["timeout_s"]) if data.get("timeout_s") else None,
        }
        if data.get("clear_secrets"):
            fields["secret_blob"] = ""
        elif data.get("secrets"):
            fields["secret_blob"] = self.box.encrypt(json.dumps(data["secrets"]))

        if integration_id is None:
            cols = {k: v for k, v in fields.items() if v is not None}
            cols.update({"created_at": now, "updated_at": now})
            names = ", ".join(cols)
            marks = ", ".join("?" for _ in cols)
            return self._exec(
                f"INSERT INTO mcp_integrations ({names}) VALUES ({marks})",
                tuple(cols.values()))

        cols = {k: v for k, v in fields.items() if v is not None}
        cols["updated_at"] = now
        sets = ", ".join(f"{k}=?" for k in cols)
        self._exec(f"UPDATE mcp_integrations SET {sets} WHERE id=?",
                   tuple(cols.values()) + (integration_id,))
        return integration_id

    def set_mcp_status(self, integration_id, status, error=None, tools=None):
        if tools is not None:
            self._exec(
                "UPDATE mcp_integrations SET status=?, last_checked=?, last_error=?, tools_json=? WHERE id=?",
                (status, _now(), (error or "")[:300], json.dumps(tools), integration_id))
        else:
            self._exec(
                "UPDATE mcp_integrations SET status=?, last_checked=?, last_error=? WHERE id=?",
                (status, _now(), (error or "")[:300], integration_id))

    def delete_mcp(self, integration_id):
        self._exec("DELETE FROM mcp_integrations WHERE id=?", (integration_id,))

    def log_tool_call(self, **f):
        try:
            self._exec(
                """INSERT INTO mcp_tool_calls
                   (ts, username, integration, tool, args_summary, success,
                    duration_ms, error, initiated_by)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (_now(), f.get("username", ""), f.get("integration", ""),
                 f.get("tool", ""), (f.get("args_summary") or "")[:300],
                 1 if f.get("success") else 0, int(f.get("duration_ms") or 0),
                 (f.get("error") or "")[:300], f.get("initiated_by", "user")))
        except Exception:
            pass

    def list_tool_calls(self, limit=100):
        return self._exec(
            "SELECT * FROM mcp_tool_calls ORDER BY id DESC LIMIT ?",
            (int(limit),), fetch="all")

    # ---------------------------------------------------------------- seed

    def ensure_default_provider(self):
        """Seed the built-in local Ollama provider from Config.settings so
        behaviour is unchanged until an administrator configures more."""

        if self.list_providers():
            return

        try:
            from Config import settings as cfg
            url = getattr(cfg, "OLLAMA_URL", "http://localhost:11434/api/generate")
            model = getattr(cfg, "LLM_MODEL", "qwen2.5:7b")
            timeout = int(getattr(cfg, "LLM_TIMEOUT", 300))
        except Exception:
            url, model, timeout = "http://localhost:11434/api/generate", "qwen2.5:7b", 300

        base = url.split("/api/")[0] if "/api/" in url else url.rstrip("/")

        pid = self.create_provider({
            "name": "Local Ollama", "adapter": "ollama", "base_url": base,
            "default_model": model, "timeout_s": timeout, "is_builtin": True,
            "permissions": {"allow_private_data": False, "allow_public_data": False},
        })
        self.upsert_model(pid, model, display_name=model, enabled=True)

        routing = self.get_section("routing")
        if not routing.get("default_provider_id"):
            routing["default_provider_id"] = pid
            routing["default_model"] = model
            self.save_section("routing", routing, "system")

    @staticmethod
    def mask(value):
        return mask_preview(value)


def _deep_merge(base, override):
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value
