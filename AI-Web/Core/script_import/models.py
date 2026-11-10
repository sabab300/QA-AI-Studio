"""Data model shared by every importer stage."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict

FORMAT_QA_JSON = "qa_studio_json"
FORMAT_CHROME_RECORDER = "chrome_recorder_json"
FORMAT_PYTHON = "playwright_python"
FORMAT_JAVASCRIPT = "playwright_javascript"
FORMAT_TYPESCRIPT = "playwright_typescript"
FORMAT_UNKNOWN = "unknown"

ALL_FORMATS = (
    FORMAT_QA_JSON, FORMAT_CHROME_RECORDER, FORMAT_PYTHON,
    FORMAT_JAVASCRIPT, FORMAT_TYPESCRIPT,
)

STATUS_VALID = "VALID"
STATUS_CONVERSION_REQUIRED = "CONVERSION REQUIRED"
STATUS_NEEDS_REVIEW = "NEEDS REVIEW"
STATUS_INVALID = "INVALID"

SEVERITY_ERROR = "error"
SEVERITY_WARNING = "warning"
SEVERITY_INFO = "info"

RUNTIME_PYTHON = "python-playwright"
RUNTIME_NODE = "node-playwright-test"

# A Test Case ID such as TC_POR_0003 or TC_D42_M50_KN74_V64_CRF_FIL_0004:
# "TC" + optional alphanumeric segments + a final all-digit segment.
TEST_ID_PATTERN = re.compile(
    r"(?<![A-Za-z0-9])(TC[_-](?:[A-Za-z0-9]+[_-])*\d+)(?![A-Za-z0-9])", re.IGNORECASE
)


def find_test_id(*texts) -> str | None:
    for text in texts:
        match = TEST_ID_PATTERN.search(str(text or ""))
        if match:
            return match.group(1)
    return None


def fingerprint(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    step: int | None = None

    def to_dict(self):
        return asdict(self)


@dataclass
class ExtractedTest:
    """ONE independently identifiable test case extracted from an upload."""

    test_id: str                      # TC ID found in the source (or generated)
    name: str
    source_filename: str
    source_format: str
    original_source: str              # exact text of THIS test (never merged)
    index_in_file: int = 0
    group: str = ""                   # describe() / class / recording title
    id_generated: bool = False        # True when no TC ID was found in the source
    source_runtime: str = RUNTIME_PYTHON
    executable_source: str | None = None   # canonical Python replay script, if lossless
    executable_runtime: str | None = None
    native_source: str | None = None       # self-contained native test (node) when applicable
    steps: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    status: str = STATUS_NEEDS_REVIEW
    requires: list = field(default_factory=list)
    assertion_count: int = 0
    skipped_in_source: bool = False
    validation_scope: str = "static"       # static validation never claims live success
    notes: dict = field(default_factory=dict)

    def add(self, severity, code, message, step=None):
        self.issues.append(Issue(severity, code, message, step))

    @property
    def key(self):
        return f"{self.source_filename}#{self.index_in_file}"

    @property
    def errors(self):
        return [i for i in self.issues if i.severity == SEVERITY_ERROR]

    @property
    def warnings(self):
        return [i for i in self.issues if i.severity == SEVERITY_WARNING]

    @property
    def source_fingerprint(self):
        return fingerprint(self.original_source)

    @property
    def validation_fingerprint(self):
        basis = (self.executable_source or self.native_source or "") + "|" + self.status
        return fingerprint(basis)

    def to_dict(self, include_sources=True):
        data = {
            "test_id": self.test_id, "name": self.name, "key": self.key,
            "source_filename": self.source_filename, "source_format": self.source_format,
            "source_runtime": self.source_runtime, "index_in_file": self.index_in_file,
            "group": self.group, "id_generated": self.id_generated,
            "status": self.status, "validation_scope": self.validation_scope,
            "executable_runtime": self.executable_runtime,
            "has_converted_script": self.executable_source is not None,
            "has_native_source": self.native_source is not None,
            "requires": list(self.requires), "assertion_count": self.assertion_count,
            "skipped_in_source": self.skipped_in_source,
            "existing_test_case": self.notes.get("existing_test_case"),
            "duplicate_in_batch": bool(self.notes.get("duplicate_in_batch")),
            "step_count": len(self.steps),
            "steps": [{**st, "target": mask_secrets(st.get("target", "")), "description": mask_secrets(st.get("description", ""))}
                      for st in self.steps],
            "issues": [{**i.to_dict(), "message": mask_secrets(i.message)} for i in self.issues],
            "source_fingerprint": self.source_fingerprint,
            "validation_fingerprint": self.validation_fingerprint,
        }
        if include_sources:
            data["original_source"] = mask_secrets(self.original_source)
            data["converted_source"] = mask_secrets(self.executable_source) if self.executable_source else None
        return data


_SECRET_FILL = re.compile(
    r"""(?P<head>(?:password|passwd|pwd|secret|token|otp|pin|api[_-]?key|authorization)[^\n]{0,200}?
        (?:\.fill\(|\.type\(|\.press_sequentially\(|\.fill\s*\(|=\s*))(?P<q>['"])(?P<val>[^'"\n]{1,200})(?P=q)""",
    re.IGNORECASE | re.VERBOSE,
)


def mask_secrets(text: str | None) -> str | None:
    """Mask obvious literal credentials in text shown to a reviewer / logged."""
    if text is None:
        return None
    return _SECRET_FILL.sub(lambda m: f"{m.group('head')}{m.group('q')}[REDACTED]{m.group('q')}", text)
