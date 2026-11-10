"""Evidence and design-image storage under Output/UiUx with path-traversal protection."""
from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "Output" / "UiUx"


class StorageError(ValueError):
    pass


class Storage:
    def __init__(self, root=None):
        self.root = Path(root) if root else DEFAULT_ROOT
        self.root.mkdir(parents=True, exist_ok=True)
        self.root = self.root.resolve()

    @staticmethod
    def _segment(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9._-]", "_", str(value))[:120]
        if not cleaned or cleaned in (".", ".."):
            raise StorageError("Invalid storage name.")
        return cleaned

    def relative(self, *parts) -> str:
        return "/".join(self._segment(p) for p in parts)

    def resolve(self, relative: str) -> Path:
        path = (self.root / relative).resolve()
        if self.root != path and self.root not in path.parents:
            raise StorageError("Path escapes the evidence folder.")
        return path

    def write(self, relative: str, data: bytes) -> Path:
        path = self.resolve(relative)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".part-")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
            os.replace(tmp, path)
        except Exception:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise
        return path

    def read(self, relative: str) -> bytes:
        return self.resolve(relative).read_bytes()

    def exists(self, relative: str) -> bool:
        try:
            return self.resolve(relative).is_file()
        except StorageError:
            return False
