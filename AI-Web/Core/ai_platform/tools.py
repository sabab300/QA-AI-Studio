"""
Plugins / MCP integrations and the AI tool broker.

Safety model
------------
* Integrations are configured by an administrator (Settings -> LLM -> Plugins /
  MCP); they are DISABLED until enabled and tested.
* stdio commands: bare executable on an allow-list, no shell, minimal env.
* Every tool has a permission: "read", "write" or "blocked". Unknown tools
  default to "write" (the safe assumption) unless the server marks them
  readOnlyHint. The AI can only ever call "read" tools - never write tools.
* A write tool can only be run manually (Settings, settings.execute), needs the
  integration's ``allow_write`` switch AND an explicit per-call confirmation.
* Arguments are validated against the tool's inputSchema before any call.
* At most ``security.tool_max_calls`` calls per question; each bounded by the
  integration timeout; result text is capped and treated as untrusted data.
* Every call (AI- or user-initiated) is logged to mcp_tool_calls and audited.
* Tool use is only offered in the AI modes the integration allows.
"""

import json
import re
import time

from Core.ai_platform import schema_lite
from Core.ai_platform.mcp_client import (
    HttpMCPClient, MCPError, StdioMCPClient, validate_url,
)
from Core.ai_platform.sanitize import scrub

BUILTIN = "QA AI Studio (built-in)"

_NAME = re.compile(r"^[\w .:+()-]{1,60}$")


class ToolError(Exception):
    pass


# ----------------------------------------------------------- built-in tools

def _ro_connection():
    from Database.db_manager import DatabaseManager
    conn = DatabaseManager().get_connection()
    conn.execute("PRAGMA query_only = ON")
    conn.row_factory = None
    return conn


def _builtin_search_test_cases(args):
    q = f"%{args['query']}%"
    sql = ("SELECT tc_number, domain, module, knowledge_name, version, scenario, "
           "status, last_result FROM test_cases "
           "WHERE (scenario LIKE ? OR test_case LIKE ? OR tc_number LIKE ?)")
    params = [q, q, q]
    if args.get("domain"):
        sql += " AND domain = ?"
        params.append(args["domain"])
    if args.get("module"):
        sql += " AND module = ?"
        params.append(args["module"])
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(args.get("limit", 10)))
    conn = _ro_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    cols = ["tc_number", "domain", "module", "knowledge_name", "version",
            "scenario", "status", "last_result"]
    return "\n".join(
        " | ".join(f"{c}={str(v)[:120]}" for c, v in zip(cols, r)) for r in rows
    ) or "No matching test cases."


def _builtin_recent_executions(args):
    sql = ("SELECT tc_number, status, outcome, duration_seconds, finished_at "
           "FROM automation_runs")
    params = []
    if args.get("tc_number"):
        sql += " WHERE tc_number = ?"
        params.append(args["tc_number"])
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(args.get("limit", 10)))
    conn = _ro_connection()
    try:
        rows = conn.execute(sql, params).fetchall()
    finally:
        conn.close()
    cols = ["tc_number", "status", "outcome", "duration_s", "finished_at"]
    return "\n".join(
        " | ".join(f"{c}={str(v)[:80]}" for c, v in zip(cols, r)) for r in rows
    ) or "No automation runs found."


BUILTIN_TOOLS = {
    "qa.search_test_cases": {
        "description": "Search saved test cases by text (scenario, steps, TC number).",
        "inputSchema": {"type": "object", "required": ["query"], "additionalProperties": False,
                        "properties": {"query": {"type": "string", "minLength": 2, "maxLength": 120},
                                       "domain": {"type": "string", "maxLength": 80},
                                       "module": {"type": "string", "maxLength": 80},
                                       "limit": {"type": "integer", "minimum": 1, "maximum": 20}}},
        "needs": ("qa_engineering", "view"), "fn": _builtin_search_test_cases,
    },
    "qa.recent_executions": {
        "description": "Recent automation run results, optionally for one test case number.",
        "inputSchema": {"type": "object", "additionalProperties": False,
                        "properties": {"tc_number": {"type": "string", "maxLength": 40},
                                       "limit": {"type": "integer", "minimum": 1, "maximum": 20}}},
        "needs": ("automation", "view"), "fn": _builtin_recent_executions,
    },
}


class McpService:
    """Administration + execution of MCP integrations."""

    def __init__(self, store, audit=None, client_factory=None):
        self.store = store
        self.audit = audit or (lambda user, action, target="", detail="": None)
        self._factory = client_factory or self._default_factory

    # ---------------------------------------------------------- admin CRUD

    @staticmethod
    def public(i):
        return {k: v for k, v in i.items() if not k.startswith("_")}

    def list(self):
        return [self.public(i) for i in self.store.list_mcp()]

    def _validate(self, data, creating):
        from Core.ai_platform.service import ValidationError, _num

        out = {}
        if creating or "name" in data:
            name = (data.get("name") or "").strip()
            if not _NAME.match(name):
                raise ValidationError("Name must be 1-60 characters.")
            out["name"] = name
        transport = data.get("transport") if "transport" in data or creating else None
        if transport is not None:
            if transport not in ("stdio", "streamable_http"):
                raise ValidationError("Transport must be stdio or streamable_http.")
            out["transport"] = transport
        if "args" in data:
            if not isinstance(data["args"], list):
                raise ValidationError("Arguments must be a list.")
            out["args"] = data["args"]
        for key in ("command", "url"):
            if key in data:
                out[key] = (data[key] or "").strip()
        if "enabled" in data:
            out["enabled"] = bool(data["enabled"])
        if "allow_write" in data:
            out["allow_write"] = bool(data["allow_write"])
        if "allowed_modes" in data:
            modes = data["allowed_modes"]
            if not isinstance(modes, list) or any(m not in ("local", "hybrid") for m in modes):
                raise ValidationError("Allowed modes must be a list of local and/or hybrid "
                                      "(tool use is not offered in GLOBAL mode).")
            out["allowed_modes"] = modes
        if "tool_permissions" in data:
            tp = data["tool_permissions"]
            if not isinstance(tp, dict) or any(v not in ("read", "write", "blocked") for v in tp.values()):
                raise ValidationError("Tool permissions must be read, write or blocked.")
            out["tool_permissions"] = tp
        if "timeout_s" in data:
            out["timeout_s"] = _num(data["timeout_s"], 2, 120, "Timeout", True)
        if "secrets" in data and data["secrets"]:
            if not isinstance(data["secrets"], dict) or len(data["secrets"]) > 20:
                raise ValidationError("Secrets must be an object of up to 20 entries.")
            out["secrets"] = {str(k): str(v) for k, v in data["secrets"].items()}
        if data.get("clear_secrets"):
            out["clear_secrets"] = True
        return out

    def _validate_target(self, merged):
        from Core.ai_platform.service import ValidationError
        allowed = (self.store.get_section("security").get("mcp_allowed_commands") or [])
        try:
            if merged.get("transport") == "stdio":
                cmd = (merged.get("command") or "").strip()
                # policy only; presence on PATH and argument limits are
                # enforced again when the process is started.
                if not cmd or any(c in cmd for c in "/\\ \t\n;|&$`<>") or \
                        cmd.lower().removesuffix(".exe") not in {a.lower() for a in allowed}:
                    raise MCPError(f"'{cmd}' is not on the allowed command list "
                                   f"({', '.join(sorted(allowed))}).")
                args = merged.get("args") or []
                if len(args) > 20 or any((not isinstance(a, str)) or len(a) > 500 or "\x00" in a for a in args):
                    raise MCPError("Arguments must be a list of up to 20 short strings.")
            else:
                validate_url(merged.get("url"))
        except MCPError as exc:
            raise ValidationError(str(exc))

    def create(self, data, username):
        from Core.ai_platform.service import ValidationError
        out = self._validate(data, True)
        if "transport" not in out:
            raise ValidationError("Choose a transport.")
        if any(i["name"].lower() == out["name"].lower() for i in self.store.list_mcp()):
            raise ValidationError(f"An integration named '{out['name']}' already exists.")
        self._validate_target(out)
        out.setdefault("enabled", False)
        out.setdefault("allowed_modes", ["local"])
        iid = self.store.save_mcp(out)
        self.audit(username, "mcp_create", out["name"], f"transport={out['transport']}")
        return self.public(self.store.get_mcp(iid))

    def update(self, iid, data, username):
        from Core.ai_platform.service import ValidationError
        cur = self.store.get_mcp(iid)
        if not cur:
            raise ValidationError("Integration not found.")
        out = self._validate(data, False)
        if out.get("enabled") and cur["status"] != "ok" and not data.get("force_enable"):
            # enabling requires a successful test first
            raise ValidationError("Run 'Test connection' successfully before enabling this integration.")
        merged = {**cur, **out}
        if any(k in out for k in ("command", "url", "transport", "args")):
            self._validate_target(merged)
        if any(k in out for k in ("command", "url", "transport", "args", "secrets")):
            # target changed: needs re-test before it can stay enabled
            out["enabled"] = out.get("enabled", False) if "enabled" in out else False
            self.store.set_mcp_status(iid, "unknown", "Configuration changed; test again.")
        self.store.save_mcp(out, iid)
        self.audit(username, "mcp_update", cur["name"],
                   f"fields={sorted(k for k in out if k != 'secrets')} secrets_changed={'secrets' in out}")
        return self.public(self.store.get_mcp(iid))

    def delete(self, iid, username):
        cur = self.store.get_mcp(iid)
        if cur:
            self.store.delete_mcp(iid)
            self.audit(username, "mcp_delete", cur["name"])

    # --------------------------------------------------------- connectivity

    def _default_factory(self, integ):
        secrets = integ.get("_secrets") or {}
        timeout = integ.get("timeout_s") or 20
        if integ["transport"] == "stdio":
            allowed = self.store.get_section("security").get("mcp_allowed_commands")
            return StdioMCPClient(integ["command"], integ["args"], env=secrets,
                                  timeout=timeout, allowed=allowed)
        return HttpMCPClient(integ["url"], headers=secrets, timeout=timeout)

    def _connect(self, integ):
        client = self._factory(integ)
        client.start()
        client.initialize()
        return client

    def test(self, iid, username):
        integ = self.store.get_mcp(iid, include_secret=True)
        if not integ:
            raise ToolError("Integration not found.")
        secrets = list((integ.get("_secrets") or {}).values())
        client = None
        try:
            client = self._connect(integ)
            tools = client.list_tools()
        except MCPError as exc:
            msg = scrub(str(exc), secrets)
            self.store.set_mcp_status(iid, "error", msg)
            self.audit(username, "mcp_test", integ["name"], "ok=False")
            return {"ok": False, "error": msg}
        finally:
            if client:
                client.close()

        perms = integ["tool_permissions"]
        enriched = []
        for t in tools:
            inferred = "read" if t.get("readOnlyHint") else "write"
            enriched.append({**t, "permission": inferred,
                             "effective_permission": perms.get(t["name"]) or inferred})
        self.store.set_mcp_status(iid, "ok", None, enriched)
        self.audit(username, "mcp_test", integ["name"], f"ok=True tools={len(enriched)}")
        return {"ok": True, "tools": enriched}

    # ------------------------------------------------------------ execution

    @staticmethod
    def effective_permission(integ, tool):
        perms = integ.get("tool_permissions") or {}
        if tool["name"] in perms:
            return perms[tool["name"]]
        return tool.get("permission") or ("read" if tool.get("readOnlyHint") else "write")

    def _authorize(self, integ, tool_name, arguments, initiated_by, confirm, mode):
        if not integ["enabled"]:
            raise ToolError("This integration is disabled.")
        tool = next((t for t in integ["tools"] if t["name"] == tool_name), None)
        if tool is None:
            raise ToolError(f"Tool '{tool_name}' is not known for this integration. Run 'Test connection' to refresh the tool list.")
        perm = self.effective_permission(integ, tool)

        if perm == "blocked":
            raise ToolError("This tool is blocked by an administrator.")
        if initiated_by == "ai":
            if perm != "read":
                raise ToolError("The AI is not allowed to run tools that can change data.")
            if mode and mode not in integ["allowed_modes"]:
                raise ToolError(f"This integration is not allowed in {mode.upper()} mode.")
        elif perm == "write":
            if not integ["allow_write"]:
                raise ToolError("This tool can change data. Enable 'Allow write tools' on the integration first.")
            if not confirm:
                raise ToolError("This tool can change data. Explicit confirmation is required.")

        errors = schema_lite.validate(arguments or {}, tool.get("inputSchema") or {"type": "object"})
        if errors:
            raise ToolError("Invalid arguments: " + "; ".join(errors[:4]))
        return tool, perm

    def call(self, iid, tool_name, arguments, username, initiated_by="user",
             confirm=False, mode=None):
        """Execute one tool. AI-initiated calls may only use read tools."""

        integ = self.store.get_mcp(iid, include_secret=True)
        if not integ:
            raise ToolError("Integration not found.")
        try:
            tool, perm = self._authorize(integ, tool_name, arguments, initiated_by,
                                         confirm, mode)
        except ToolError as exc:
            self.audit(username, "mcp_tool_denied", f"{integ['name']}.{tool_name}",
                       f"by={initiated_by} reason={str(exc)[:120]}")
            raise

        started = time.time()
        secrets = list((integ.get("_secrets") or {}).values())
        client, ok, err, text = None, False, "", ""
        try:
            client = self._connect(integ)
            res = client.call_tool(tool_name, arguments or {})
            ok = not res["is_error"]
            text = res["text"]
            if not ok:
                err = scrub(text, secrets, limit=200)
        except MCPError as exc:
            err = scrub(str(exc), secrets)
        finally:
            if client:
                client.close()

        self.store.log_tool_call(
            username=username, integration=integ["name"], tool=tool_name,
            args_summary=scrub(json.dumps(arguments or {}, default=str), secrets, limit=200),
            success=ok, duration_ms=int((time.time() - started) * 1000),
            error=err, initiated_by=initiated_by)
        self.audit(username, "mcp_tool_call", f"{integ['name']}.{tool_name}",
                   f"by={initiated_by} perm={perm} ok={ok}")

        if not ok:
            raise ToolError(err or "The tool call failed.")
        return {"text": scrub(text, secrets, limit=0)[:20000], "permission": perm,
                "duration_ms": int((time.time() - started) * 1000)}


class ToolBroker:
    """Lets the answer pipeline use read-only tools for one question."""

    def __init__(self, platform, mcp=None):
        self.platform = platform
        self.mcp = mcp or McpService(platform.store, audit=platform._audit)

    def catalog(self, mode, permissions):
        """Read-only tools available to this user in this mode."""

        cat = []
        for name, spec in BUILTIN_TOOLS.items():
            res, act = spec["needs"]
            if act in (permissions or {}).get(res, ()):
                cat.append({"integration_id": None, "integration": BUILTIN,
                            "name": name, "description": spec["description"],
                            "schema": spec["inputSchema"]})
        for integ in self.platform.store.list_mcp():
            if not integ["enabled"] or integ["status"] != "ok" or mode not in integ["allowed_modes"]:
                continue
            for t in integ["tools"]:
                if self.mcp.effective_permission(integ, t) == "read":
                    cat.append({"integration_id": integ["id"], "integration": integ["name"],
                                "name": t["name"], "description": t["description"],
                                "schema": t["inputSchema"]})
        return cat

    def hook(self, prompt, mode, user, request_id=None):
        """Plan (LLM) and run up to N read-only tool calls. Returns
        {"context", "sources", "status"} for the assistant pipeline."""

        sec = self.platform.store.get_section("security")
        empty = {"context": "", "sources": [], "status": []}
        if not sec.get("ai_tools_enabled", True):
            return {**empty, "status": [{"tool": "-", "ok": False,
                                         "error": "AI tool use is switched off in Settings → LLM → Security."}]}

        cat = self.catalog(mode, user.get("permissions") or {})
        if not cat:
            return empty

        max_calls = int(sec.get("tool_max_calls", 3))
        listing = "\n".join(
            f"- {c['name']}: {c['description'][:160]} | args schema: "
            f"{json.dumps(c['schema'].get('properties', {}))[:300]}" for c in cat[:25])
        planner_prompt = (
            "You may call read-only tools to help answer the user's question.\n"
            f"Tools:\n{listing}\n\nQuestion: {prompt[:600]}\n\n"
            f'Reply with ONLY JSON: {{"calls":[{{"tool":"<name>","arguments":{{...}}}}]}} '
            f"with at most {max_calls} calls, or {{\"calls\":[]}} if no tool is needed.")

        try:
            plan = self.platform.gateway.generate(
                task="tool_planner", prompt=planner_prompt,
                system="You select tools. Output JSON only.", data_class="public",
                params={"max_tokens": 300, "temperature": 0}, username=user.get("username", ""),
                mode=mode, request_id=request_id)
            calls = self._parse_plan(plan.text, cat)[:max_calls]
        except Exception as exc:  # noqa: BLE001
            return {**empty, "status": [{"tool": "planner", "ok": False,
                                         "error": scrub(str(exc), limit=160)}]}

        sources, parts, status = [], [], []
        by_name = {c["name"]: c for c in cat}

        for n, call in enumerate(calls, start=1):
            spec = by_name[call["tool"]]
            started = time.time()
            try:
                if spec["integration_id"] is None:
                    text = self._run_builtin(call["tool"], call["arguments"], user)
                else:
                    text = self.mcp.call(spec["integration_id"], call["tool"], call["arguments"],
                                         user.get("username", ""), "ai", mode=mode)["text"]
                label = f"T{n}"
                sources.append({"id": label, "kind": "tool", "tool": call["tool"],
                                "integration": spec["integration"], "text": text[:3000]})
                parts.append(f"[{label}] {spec['integration']} / {call['tool']}\n{text[:3000]}")
                status.append({"tool": call["tool"], "integration": spec["integration"], "ok": True,
                               "duration_ms": int((time.time() - started) * 1000)})
            except (ToolError, MCPError, Exception) as exc:  # noqa: BLE001
                status.append({"tool": call["tool"], "integration": spec["integration"], "ok": False,
                               "error": scrub(str(exc), limit=160)})

        return {"context": "\n\n".join(parts), "sources": sources, "status": status}

    def _run_builtin(self, name, args, user):
        spec = BUILTIN_TOOLS[name]
        errors = schema_lite.validate(args, spec["inputSchema"])
        if errors:
            raise ToolError("Invalid arguments: " + "; ".join(errors[:3]))
        started = time.time()
        try:
            text = spec["fn"](args)
            ok, err = True, ""
        except Exception as exc:  # noqa: BLE001
            text, ok, err = "", False, type(exc).__name__
        self.platform.store.log_tool_call(
            username=user.get("username", ""), integration=BUILTIN, tool=name,
            args_summary=json.dumps(args, default=str)[:200], success=ok,
            duration_ms=int((time.time() - started) * 1000), error=err, initiated_by="ai")
        if not ok:
            raise ToolError("The built-in tool failed.")
        return text

    @staticmethod
    def _parse_plan(text, catalog):
        m = re.search(r"\{.*\}", text or "", re.S)
        if not m:
            return []
        try:
            obj = json.loads(m.group(0))
        except ValueError:
            return []
        names = {c["name"] for c in catalog}
        out = []
        for c in (obj.get("calls") or []) if isinstance(obj, dict) else []:
            if isinstance(c, dict) and c.get("tool") in names and isinstance(c.get("arguments", {}), dict):
                out.append({"tool": c["tool"], "arguments": c.get("arguments", {})})
        return out
