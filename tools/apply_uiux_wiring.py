#!/usr/bin/env python3
"""Anchored, idempotent, line-ending-preserving wiring of the AI UI/UX Testing module into index.html and main.py.

Usage: apply_uiux_wiring.py <index.html> <main.py>
Every edit names an exact anchor that must occur exactly once; if an anchor is missing nothing is written for that file.
"""
import sys

MARK = "QA-AI-STUDIO-UIUX"


def edit(path, edits):
    raw = open(path, "rb").read()
    crlf = b"\r\n" in raw
    text = raw.decode("utf-8")
    nl = "\r\n" if crlf else "\n"
    if MARK in text:
        print(f"{path}: already wired (marker present) - unchanged")
        return False
    out = text
    for name, anchor, replacement in edits:
        a = anchor.replace("\n", nl)
        count = out.count(a)
        if count != 1:
            raise SystemExit(f"{path}: anchor for '{name}' found {count} times (expected 1); nothing written")
        out = out.replace(a, replacement.replace("\n", nl), 1)
    open(path, "wb").write(out.encode("utf-8"))
    print(f"{path}: {len(edits)} edits applied ({'CRLF' if crlf else 'LF'} preserved)")
    return True


INDEX = [
    ("stylesheet", '<link rel="stylesheet" href="/ai_platform.css">',
     '<link rel="stylesheet" href="/ai_platform.css">\n<link rel="stylesheet" href="/uiux.css"> <!-- QA-AI-STUDIO-UIUX -->'),
    ("tab button", '<button class="tab" data-tab="auto-api-discovery">AI API Discovery</button>',
     '<button class="tab" data-tab="auto-api-discovery">AI API Discovery</button>\n        <button class="tab" data-tab="auto-uiux">AI UI/UX Testing</button>'),
    ("panel", '<div class="panel" id="auto-api-discovery" style="min-width:0;overflow:auto;"></div>',
     '<div class="panel" id="auto-api-discovery" style="min-width:0;overflow:auto;"></div>\n        <div class="panel" id="auto-uiux" style="min-width:0;overflow:auto;"></div>'),
    ("reports tab button", '<button class="tab" data-tab="rep-publish">Test Cases Publish</button>',
     '<button class="tab" data-tab="rep-publish">Test Cases Publish</button>\n        <button class="tab" data-tab="rep-uiux">UI/UX Quality</button>'),
    ("reports panel", '<div class="panel flush" id="rep-publish">',
     '<div class="panel" id="rep-uiux" style="min-width:0;overflow:auto;"></div>\n        <div class="panel flush" id="rep-publish">'),
    ("script", '<script src="api_discovery.js"></script>',
     '<script src="api_discovery.js"></script>\n<script src="uiux.js"></script>'),
    ("install", "if(window.installApiDiscovery)window.installApiDiscovery({apiFetch:apiFetch,hasPermission:hasPermission,apiBase:API_BASE,token:function(){return authState.token;}});",
     "if(window.installApiDiscovery)window.installApiDiscovery({apiFetch:apiFetch,hasPermission:hasPermission,apiBase:API_BASE,token:function(){return authState.token;}});\n  if(window.installUiux)window.installUiux({apiFetch:apiFetch,hasPermission:hasPermission,apiBase:API_BASE,token:function(){return authState.token;}}); // QA-AI-STUDIO-UIUX"),
    ("shared filters", "tcpEl('repSharedFilters').hidden=tab.getAttribute('data-tab')==='rep-publish';",
     "tcpEl('repSharedFilters').hidden=['rep-publish','rep-uiux'].indexOf(tab.getAttribute('data-tab'))>=0;"),
]

MAIN = [
    ("import", "from Web.routers.api_discovery_router import router as api_discovery_router",
     "from Web.routers.api_discovery_router import router as api_discovery_router\n"
     "try:  # QA-AI-STUDIO-UIUX: a missing optional dependency must never stop the rest of QA AI Studio from starting\n"
     "    from Web.routers.uiux_router import router as uiux_router, reconcile_on_startup as uiux_reconcile\n"
     "except Exception as _uiux_error:  # noqa: BLE001\n"
     "    from fastapi import APIRouter as _UiuxRouter, HTTPException as _UiuxHTTPError\n"
     "    uiux_reconcile = None\n"
     "    uiux_router = _UiuxRouter(prefix=\"/api/uiux\")\n"
     "    _uiux_message = (\"AI UI/UX Testing could not start: \" + type(_uiux_error).__name__ + \": \" + str(_uiux_error)[:200] +\n"
     "                     \". Run 'pip install -r requirements-web.txt' and restart QA AI Studio.\")\n"
     "    print(\"WARNING: \" + _uiux_message)\n"
     "\n"
     "    @uiux_router.api_route(\"/{uiux_rest_path:path}\", methods=[\"GET\", \"POST\", \"PUT\", \"PATCH\", \"DELETE\"])\n"
     "    def _uiux_unavailable(uiux_rest_path: str):\n"
     "        raise _UiuxHTTPError(503, _uiux_message)"),
    ("startup", "    DiscoveryRepository().reconcile_interrupted()\n",
     "    DiscoveryRepository().reconcile_interrupted()\n    try:  # QA-AI-STUDIO-UIUX: a run still active at startup was interrupted by a restart; mark it so, never resume silently\n        uiux_reconcile()\n    except Exception:\n        pass\n"),
    ("router", "app.include_router(api_discovery_router)\n",
     "app.include_router(api_discovery_router)\napp.include_router(uiux_router)\n"),
]

if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    edit(sys.argv[1], INDEX)
    edit(sys.argv[2], MAIN)
