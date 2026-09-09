# Create: AI-Web/run_web.py

"""
QA AI Studio — Web
Local Development Server Launcher

Version: 1.0

Prefer this over running `uvicorn Web.main:app --reload` directly.

ROOT CAUSE THIS FIXES: a plain `--reload` (no --reload-dir /
--reload-exclude) makes uvicorn watch the ENTIRE current working
directory for *.py changes. Core/playwright_runner.py's OUTPUT_FOLDER
(AI-Web/Output/AutomationRuns) writes a brand-new *.py script to disk
for every single Playwright execution (see playwright_runner.py's own
comment on OUTPUT_FOLDER). uvicorn's watcher sees that new .py file,
treats it as a source-code change, and restarts the whole server
process — silently killing the run that just started, mid-flight.
The next startup's AutomationExecutionRepository
.reconcile_stale_running_on_startup() then (correctly) marks that
orphaned run:

    "Interrupted: the server restarted while this run was in progress."

That message is doing its job — but the restart it is reporting on
was self-inflicted by an over-broad --reload watch, not a real crash.
Confirmed by matching two "Interrupted" runs in the UI directly to two
freshly-written Output/AutomationRuns/*.py files at the same
timestamps.

This launcher fixes it by explicitly restricting uvicorn's reload
watcher to the actual first-party Python source directories —
Core/, Web/, Database/, Config/ — and nothing else. It never watches
Output/ (execution scripts, evidence screenshots, logs),
Knowledge/ or Repository/ (uploaded knowledge, cloned/discovered
repos — themselves full of *.py files that are equally capable of
triggering this same restart loop), Models/, Templates/, Prompts/, or
dev-tools-not-for-production/. None of those ever need a code reload;
all of them receive routine runtime writes that must never be
mistaken for a source change.

A second, independent benefit: because the watched/import paths below
are anchored to THIS file's own location (Path(__file__).resolve()
.parent) rather than assumed from the process's current working
directory, `python run_web.py` behaves identically no matter which
directory it's launched from — the same class of CWD-dependent bug
already fixed for Database/db_manager.py and
Core/playwright_runner.py's OUTPUT_FOLDER (see their own comments).

Run with:
    python run_web.py
    python run_web.py --port 8001      (override the default port)
    python run_web.py --no-reload      (single process, no watcher — closer to production)

If you still want to invoke uvicorn directly instead of this script,
use the equivalent explicit flags rather than bare --reload:

    uvicorn Web.main:app --reload --port 8000 ^
        --reload-dir Core --reload-dir Web --reload-dir Database --reload-dir Config
"""

import argparse
from pathlib import Path

import uvicorn

THIS_DIR = Path(__file__).resolve().parent

# Only real Python source lives here — see the module docstring above
# for exactly why every other AI-Web subfolder is deliberately absent
# from this list.
SOURCE_DIRS = [
    str(THIS_DIR / "Core"),
    str(THIS_DIR / "Web"),
    str(THIS_DIR / "Database"),
    str(THIS_DIR / "Config"),
]


def main():

    parser = argparse.ArgumentParser(
        description="Run the QA AI Studio Web server for local development."
    )

    parser.add_argument("--host", default="127.0.0.1")

    parser.add_argument("--port", type=int, default=8000)

    parser.add_argument(
        "--no-reload",
        action="store_true",
        help="Disable the auto-reload watcher entirely (closer to how it runs in production).",
    )

    args = parser.parse_args()

    if args.no_reload:

        uvicorn.run("Web.main:app", host=args.host, port=args.port, reload=False)

    else:

        uvicorn.run(
            "Web.main:app",
            host=args.host,
            port=args.port,
            reload=True,
            reload_dirs=SOURCE_DIRS,
        )


if __name__ == "__main__":
    main()
