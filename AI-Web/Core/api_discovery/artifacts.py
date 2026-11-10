"""Download-only artifacts: generated clients are never automatically executed."""
import json
import re
from urllib.parse import urlsplit


def documentation(session):
    return {"title": session["name"], "session_id": session["id"],
            "scope": "Observed endpoints only; not complete application API coverage.",
            "privacy": "Values omitted; configure authentication and payloads independently.",
            "endpoints": session["endpoints"], "analysis": session["analysis"],
            "actions":session.get("actions",[]),"outputs":session.get("outputs",[]),
            "coverage":session.get("coverage",{}),"execution_history":session.get("execution_history",[]),
            "canonical_test_cases":session.get("canonical_test_cases",[])}


def markdown(session):
    # Do not produce executable HTML from untrusted endpoint/model text.
    rows = ["# API Discovery", "", "Observed endpoints only; this is not a complete API contract.", ""]
    for e in session["endpoints"]:
        rows += ["## " + e["method"] + " " + e["endpoint"].replace("<", "&lt;"),
                 "", "Observed status codes: " + ", ".join(map(str, e["observed_statuses"])),
                 "", "Evidence IDs: " + ", ".join(map(str, e["evidence_ids"])),
                 "", "Request schema (observed, required fields unknown):", "```json",
                 json.dumps(e["request_schema"], indent=2), "```", ""]
    return "\n".join(rows)


def openapi(session):
    paths = {}
    servers = set()
    for e in session["endpoints"]:
        url = urlsplit(e["endpoint"])
        servers.add(url.scheme + "://" + url.netloc)
        # Avoid merging same paths belonging to different origins into one contract.
        if len(servers) > 1:
            raise ValueError("Export one origin at a time; a combined multi-origin contract would be misleading.")
        paths.setdefault(url.path, {})[e["method"].lower()] = {
            "summary": "Observed request; incomplete contract",
            "x-evidence-ids": e["evidence_ids"],
            "parameters": [{"name": name, "in": "path", "required": True, "schema": {"type": "string"}}
                           for name in dict.fromkeys(re.findall(r"\{([^{}]+)\}", url.path))],
            "responses": ({str(s): {"description": "Observed response; body values omitted"} for s in e["observed_statuses"] if s}
                          or {"default": {"description": "Response not captured"}}),
        }
    return {"openapi": "3.0.3", "info": {"title": "Observed API subset", "version": "0.1",
            "description": "Incomplete traffic-derived contract. No inferred required fields or authentication credentials."},
            "servers": [{"url": s} for s in sorted(servers)], "paths": paths}


def postman(session):
    return {"info": {"name": "Discovery " + session["id"], "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json"},
            "item": [{"name": e["method"] + " " + e["endpoint"], "request": {
                "method": e["method"], "url": e["endpoint"],
                "description": "Review and replace path parameters, credentials and payload. Discovery evidence: " + str(e["evidence_ids"])
            }} for e in session["endpoints"]]}


def python_client(session):
    inventory = json.dumps([{k: e[k] for k in ("method", "endpoint", "key")} for e in session["endpoints"]])
    return '''"""Traffic-derived client. Review before use. No captured credentials."""
import json
import requests

ENDPOINTS = json.loads(%r)

class ApiClient:
    def __init__(self, timeout=30, headers=None):
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(headers or {})

    def call(self, key, *, url, params=None, payload=None):
        """Supply a reviewed concrete URL and your own authentication/payload."""
        endpoint = next(e for e in ENDPOINTS if e['key'] == key)
        response = self.session.request(endpoint['method'], url, params=params,
                                        json=payload, timeout=self.timeout)
        response.raise_for_status()
        return response.json() if 'json' in response.headers.get('content-type', '') else response.text

    def close(self):
        self.session.close()
''' % inventory


def javascript_client(session):
    return '''// Review before use. No captured credentials. Never executed by QA AI Studio.
export const endpoints = %s;
export async function callApi(key, url, {headers = {}, body, timeout = 30000} = {}) {
  const endpoint = endpoints.find(e => e.key === key);
  if (!endpoint) throw new Error('Unknown endpoint');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeout);
  try {
    const response = await fetch(url, {method: endpoint.method, headers, body, signal: controller.signal});
    if (!response.ok) throw new Error('HTTP ' + response.status);
    return (response.headers.get('content-type') || '').includes('json') ? await response.json() : await response.text();
  } finally { clearTimeout(timer); }
}
''' % json.dumps([{k: e[k] for k in ("method", "endpoint", "key")} for e in session["endpoints"]])
