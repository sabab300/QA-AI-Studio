"""
Minimal MCP (Model Context Protocol) client.

Transports
----------
* stdio             - newline-delimited JSON-RPC over a child process.
                      No shell is ever used; the executable must be a bare
                      command name on an administrator allow-list; the child
                      gets a minimal environment plus only the integration's
                      own (encrypted) variables, never the server's secrets.
* streamable_http   - JSON-RPC over HTTP POST (JSON or SSE responses),
                      Mcp-Session-Id honoured.

Only initialize / notifications/initialized / tools/list / tools/call are
implemented. Every call has a timeout and a response-size cap.
"""

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from urllib.parse import urlparse

PROTOCOL_VERSION = "2025-03-26"
MAX_RESULT_CHARS = 20000
DEFAULT_ALLOWED_COMMANDS = ("npx", "node", "python", "python3", "py", "uvx", "uv", "deno", "bun")
_ENV_PASSTHROUGH = ("PATH", "SYSTEMROOT", "COMSPEC", "PATHEXT", "TEMP", "TMP",
                    "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "LANG")


class MCPError(Exception):
    pass


def validate_command(command, args, allowed):
    """Raise MCPError unless the stdio command is safe to spawn."""

    command = (command or "").strip()
    if not command:
        raise MCPError("A command is required for a stdio integration.")
    if any(c in command for c in "/\\ \t\n;|&$`<>"):
        raise MCPError("Command must be a bare executable name (no path or shell syntax).")
    if command.lower().removesuffix(".exe") not in {a.lower() for a in allowed}:
        raise MCPError(f"'{command}' is not on the allowed command list "
                       f"({', '.join(sorted(allowed))}). An administrator can extend "
                       "the list in Settings → LLM → Security.")
    if not isinstance(args, list) or len(args) > 20 or any(
            (not isinstance(a, str)) or len(a) > 500 or "\x00" in a for a in args):
        raise MCPError("Arguments must be a list of up to 20 short strings.")
    if not shutil.which(command):
        raise MCPError(f"'{command}' was not found on this server's PATH.")
    return command


def validate_url(url):
    import ipaddress
    p = urlparse(url or "")
    if p.scheme not in ("http", "https") or not p.hostname:
        raise MCPError("URL must start with http:// or https://")
    if p.username or p.password:
        raise MCPError("Do not put credentials in the URL; use the headers field.")
    host = p.hostname.lower()
    local = host in ("localhost",) or host.endswith((".local", ".localhost")) or "." not in host
    try:
        ip = ipaddress.ip_address(host)
        if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
            raise MCPError("This address is not allowed.")
        local = ip.is_loopback or ip.is_private
    except ValueError:
        pass
    if host == "metadata.google.internal":
        raise MCPError("This address is not allowed.")
    if p.scheme == "http" and not local:
        raise MCPError("Remote MCP servers must use https://")
    return url


def _text_of(result):
    """Flatten an MCP tools/call result to bounded text."""

    parts = []
    for item in (result or {}).get("content") or []:
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        else:
            parts.append(f"[{item.get('type', 'content')} omitted]")
    if not parts and (result or {}).get("structuredContent") is not None:
        parts.append(json.dumps(result["structuredContent"])[:MAX_RESULT_CHARS])
    text = "\n".join(parts)
    return text[:MAX_RESULT_CHARS] + (" …[truncated]" if len(text) > MAX_RESULT_CHARS else "")


class _BaseClient:

    def __init__(self, timeout=20):
        self.timeout = timeout
        self._id = 0
        self.server_info = {}

    def _next(self):
        self._id += 1
        return self._id

    def initialize(self):
        res = self._request("initialize", {
            "protocolVersion": PROTOCOL_VERSION, "capabilities": {},
            "clientInfo": {"name": "qa-ai-studio", "version": "1.0"}})
        self.server_info = (res or {}).get("serverInfo") or {}
        self._notify("notifications/initialized", {})
        return self.server_info

    def list_tools(self):
        tools, cursor = [], None
        for _ in range(5):
            params = {"cursor": cursor} if cursor else {}
            res = self._request("tools/list", params) or {}
            for t in res.get("tools") or []:
                tools.append({
                    "name": str(t.get("name", ""))[:100],
                    "description": str(t.get("description", ""))[:400],
                    "inputSchema": t.get("inputSchema") or {"type": "object"},
                    "readOnlyHint": bool((t.get("annotations") or {}).get("readOnlyHint")),
                })
            cursor = res.get("nextCursor")
            if not cursor:
                break
        return [t for t in tools if t["name"]]

    def call_tool(self, name, arguments):
        res = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return {"is_error": bool((res or {}).get("isError")), "text": _text_of(res)}


class StdioMCPClient(_BaseClient):

    def __init__(self, command, args, env=None, timeout=20, allowed=DEFAULT_ALLOWED_COMMANDS):
        super().__init__(timeout)
        self.command = validate_command(command, args, allowed)
        self.args = list(args)
        base = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
        for k, v in (env or {}).items():
            if not str(k).replace("_", "").isalnum():
                raise MCPError("Invalid environment variable name.")
            base[str(k)] = str(v)
        self._env = base
        self._proc = None
        self._q = queue.Queue()
        self._stderr = []

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        try:
            self._proc = subprocess.Popen(
                [shutil.which(self.command)] + self.args, stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self._env,
                shell=False, text=True, encoding="utf-8", bufsize=1)
        except OSError as exc:
            raise MCPError(f"Could not start the MCP server ({type(exc).__name__}).")
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read_out(self):
        try:
            for line in self._proc.stdout:
                line = line.strip()
                if line:
                    try:
                        self._q.put(json.loads(line))
                    except ValueError:
                        pass
        except Exception:
            pass
        self._q.put(None)

    def _read_err(self):
        try:
            for line in self._proc.stderr:
                if len(self._stderr) < 20:
                    self._stderr.append(line.strip()[:200])
        except Exception:
            pass

    def _send(self, obj):
        try:
            self._proc.stdin.write(json.dumps(obj) + "\n")
            self._proc.stdin.flush()
        except Exception:
            raise MCPError("The MCP server closed the connection.")

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params):
        rid = self._next()
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        deadline = time.time() + self.timeout
        while True:
            left = deadline - time.time()
            if left <= 0:
                raise MCPError(f"MCP server did not answer '{method}' within {self.timeout}s.")
            try:
                msg = self._q.get(timeout=left)
            except queue.Empty:
                continue
            if msg is None:
                raise MCPError("The MCP server exited unexpectedly.")
            if msg.get("id") == rid:
                if "error" in msg:
                    raise MCPError("MCP error: " + str((msg["error"] or {}).get("message", ""))[:200])
                return msg.get("result")
            # server->client requests/notifications are ignored (no sampling/roots)
            if "method" in msg and "id" in msg:
                self._send({"jsonrpc": "2.0", "id": msg["id"],
                            "error": {"code": -32601, "message": "Not supported"}})

    def close(self):
        p = self._proc
        if not p:
            return
        try:
            p.stdin.close()
        except Exception:
            pass
        try:
            p.terminate()
            p.wait(timeout=3)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass


class HttpMCPClient(_BaseClient):

    def __init__(self, url, headers=None, timeout=20):
        super().__init__(timeout)
        self.url = validate_url(url)
        self.headers = {str(k): str(v) for k, v in (headers or {}).items()}
        self.session_id = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def start(self):
        return None

    def _post(self, payload):
        import requests
        h = {"Content-Type": "application/json",
             "Accept": "application/json, text/event-stream",
             "MCP-Protocol-Version": PROTOCOL_VERSION, **self.headers}
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        try:
            r = requests.post(self.url, json=payload, headers=h,
                              timeout=(5, self.timeout), stream=True)
        except requests.exceptions.RequestException as exc:
            raise MCPError(f"Cannot reach the MCP server ({type(exc).__name__}).")
        with r:
            if r.status_code in (401, 403):
                raise MCPError("The MCP server rejected the credentials.")
            if r.status_code >= 400:
                raise MCPError(f"MCP server returned HTTP {r.status_code}.")
            sid = r.headers.get("Mcp-Session-Id")
            if sid:
                self.session_id = sid
            ctype = r.headers.get("Content-Type", "")
            body = b""
            for chunk in r.iter_content(8192):
                body += chunk
                if len(body) > 2_000_000:
                    raise MCPError("MCP response too large.")
        return ctype, body.decode("utf-8", "replace")

    def _parse(self, ctype, text, rid):
        if "text/event-stream" in ctype:
            for block in text.split("\n\n"):
                data = "".join(l[5:].strip() for l in block.splitlines() if l.startswith("data:"))
                if data:
                    try:
                        msg = json.loads(data)
                    except ValueError:
                        continue
                    if msg.get("id") == rid:
                        return msg
            raise MCPError("No response in event stream.")
        try:
            return json.loads(text)
        except ValueError:
            raise MCPError("Unreadable MCP response.")

    def _notify(self, method, params):
        self._post({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params):
        rid = self._next()
        ctype, text = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        msg = self._parse(ctype, text, rid)
        if "error" in msg:
            raise MCPError("MCP error: " + str((msg["error"] or {}).get("message", ""))[:200])
        return msg.get("result")

    def close(self):
        return None
