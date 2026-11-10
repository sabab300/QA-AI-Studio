"""Knowledge Hub <-> AI UI/UX Testing bridge for the Figma source type.

Knowledge Hub -> Manual Upload offers Source Type "Figma" for Document Type "UI/UX Design". A Figma upload goes through the
same registration service the AI UI/UX Testing tab uses (``DesignRegistry.register_upload``), so there is one set of rules for
file inspection, SVG sanitising, duplicate detection and Knowledge Hub ownership, whichever screen the design came from.

Native ``.fig`` files are stored unchanged. Their structure is NOT read (no decoder exists); structural checks stay
"Not Verifiable". Nothing here invents design metadata.
"""
from __future__ import annotations

import threading
from pathlib import Path

FIGMA_SOURCE_TYPE = "Figma"
DESIGN_DOCUMENT_TYPE = "UI/UX Design"
FIGMA_EXTENSIONS = (".fig", ".json", ".png", ".jpg", ".jpeg", ".svg")

_LOCK = threading.Lock()
_REGISTRY = None


class DuplicateDesign(FileExistsError):
    """The same design content is already registered in this Knowledge Hub version."""


class DesignServiceUnavailable(RuntimeError):
    """A library the design service needs is not installed; the message says what to install."""


def is_figma(source_type) -> bool:
    return str(source_type or "").strip().casefold() == FIGMA_SOURCE_TYPE.casefold()


def registry():
    """One registry for Knowledge Hub uploads (the router keeps its own with the same stateless collaborators)."""
    global _REGISTRY
    with _LOCK:
        if _REGISTRY is None:
            from .hierarchy import Hierarchy
            from .registry import DesignRegistry
            from .schema import UiuxDatabase
            from .storage import Storage
            from .vault import Vault
            db = UiuxDatabase()
            _REGISTRY = DesignRegistry(db, Hierarchy(db.db_path), Storage(), Vault(db))
        return _REGISTRY


def set_registry(value):
    """Tests substitute a registry built on a throw-away database."""
    global _REGISTRY
    with _LOCK:
        _REGISTRY = value


def upload_from_knowledge_hub(*, file_bytes: bytes, original_filename: str, domain: str, module: str, knowledge_name: str,
                              version: str, document_type: str = "", created_by: str = "") -> dict:
    """Store one design uploaded through Knowledge Hub. Raises ValueError (400) or DuplicateDesign (409); never half-stores."""
    name = Path(str(original_filename or "")).name
    if not name.lower().endswith(FIGMA_EXTENSIONS):
        raise ValueError("Source Type Figma accepts .fig, Figma JSON (.json), .png, .jpg/.jpeg and .svg files only.")
    document_type = (document_type or DESIGN_DOCUMENT_TYPE).strip()
    if document_type.casefold() != DESIGN_DOCUMENT_TYPE.casefold():
        raise ValueError(f"Source Type Figma requires Document Type '{DESIGN_DOCUMENT_TYPE}'.")
    document_type = DESIGN_DOCUMENT_TYPE
    try:
        reg = registry()
        return _store(reg, file_bytes, name, domain, module, knowledge_name, version, document_type, created_by)
    except ImportError as error:
        raise DesignServiceUnavailable("Figma design upload could not start: a required library is missing (" + str(error)[:160] +
                                       "). Run 'pip install -r requirements-web.txt' and restart QA AI Studio.") from None


def _store(reg, file_bytes, name, domain, module, knowledge_name, version, document_type, created_by) -> dict:
    from .registry import RegistryError
    # Domain and Module are Knowledge Hub's own entities; the normal upload path creates them when missing, so this one does too.
    from Core.metadata_manager import MetadataManager
    meta = MetadataManager()
    meta.get_or_create_domain(domain)
    meta.get_or_create_module(domain, module)
    selection = {"domain": domain, "module": module, "knowledge_name": knowledge_name, "document_type": document_type, "version": version}
    try:
        outcome = reg.register_upload(selection, name, file_bytes, created_by or "knowledge-hub", create_missing=True)
    except RegistryError as error:
        text = str(error)
        if "already registered" in text:
            raise DuplicateDesign(text) from None
        raise ValueError(text) from None
    if outcome.get("duplicate"):
        raise DuplicateDesign(outcome["message"])
    asset = outcome["asset"]
    item = reg.hub.item(asset["knowledge_id"]) or {}
    structural = bool(asset.get("structural_available"))
    note = ("Design stored for AI UI/UX Testing. " + (asset.get("extraction_status") or "") + ". "
            + ("" if structural else "Structural checks will be reported as Not Verifiable. ") + "Designs are not text-indexed.").replace("..", ".")
    return {
        "success": True, "source_saved": True, "indexed": False, "processing_status": "FAILED_NO_TEXT", "processing_error": note,
        "knowledge_id": asset["knowledge_id"], "domain": domain, "module": module, "knowledge_name": knowledge_name, "version": version,
        "document_type": document_type, "source_type": FIGMA_SOURCE_TYPE, "summary": "UI/UX design asset registered for AI UI/UX Testing.", "tags": ["design", "ui-ux"],
        "confidence": 1.0, "repository_path": item.get("repository_path", ""), "file_name": asset["file_name"], "file_type": Path(asset["file_name"]).suffix.lower(),
        "total_chunks": 0, "vectors_saved": 0,
        "design": {"asset_id": asset["id"], "kind": asset["asset_kind"], "structural_available": structural, "extraction_status": asset["extraction_status"],
                   "status": asset["status"], "frames": asset.get("frame_count", 0)},
    }
