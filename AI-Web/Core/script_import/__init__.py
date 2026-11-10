"""Universal Playwright script import (Task 1 / Phase 6).

Pipeline: Upload -> Detect -> Extract -> Validate -> Convert/Preserve ->
Preview -> Save -> Replay -> Verify -> Report.

This package is deliberately free of database / web dependencies: it turns
uploaded bytes into independent, individually-identifiable test records and a
reviewable import plan. Persistence is injected by the caller so it can reuse
the application's existing atomic script-store primitives.
"""
from Core.script_import.models import (  # noqa: F401
    ExtractedTest, Issue, FORMAT_QA_JSON, FORMAT_CHROME_RECORDER, FORMAT_PYTHON,
    FORMAT_JAVASCRIPT, FORMAT_TYPESCRIPT, FORMAT_UNKNOWN,
    STATUS_VALID, STATUS_CONVERSION_REQUIRED, STATUS_NEEDS_REVIEW, STATUS_INVALID,
)
from Core.script_import.detect import detect_format  # noqa: F401


def __getattr__(name):  # lazy: the engine pulls in every extractor
    if name == "ScriptImportEngine":
        from Core.script_import.batch import ScriptImportEngine
        return ScriptImportEngine
    raise AttributeError(name)
