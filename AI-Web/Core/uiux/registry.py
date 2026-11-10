"""Design asset registration: Knowledge Hub owns the asset, this module only records testing-side metadata.

Registration reuses the canonical Knowledge Hub services (RepositoryManager.save_file and
MetadataManager.save_knowledge_item -> DocumentHierarchy.ensure_context). The text/vector pipeline is deliberately
not used: a design image has no text to index, and the item is marked FAILED_NO_TEXT exactly like any other
non-indexable source so existing screens already treat it correctly ("no indexed text").
"""
from __future__ import annotations

import io
import json
import logging
import re
import sqlite3
import shutil
import tempfile
from pathlib import Path

from . import files as F
from .figma_client import FigmaClient, FigmaError, parse_figma_url
from .figma_parser import extract_frames, parse_frame_node, resolve_variable_names, classify_viewport
from .hierarchy import Hierarchy, HierarchyError, LEVELS, LABELS
from .schema import UiuxDatabase
from .storage import Storage
from .util import dumps, loads, new_id, now_iso, preserve_filename, safe_filename, sha256_bytes

log = logging.getLogger(__name__)
SOURCE_TYPE = "Figma"
DEFAULT_DOC_TYPE = "UI/UX Design"
MAX_FRAMES_PER_ANALYSIS = 25


class RegistryError(ValueError):
    """User-safe registration failure."""


class KnowledgeHubPort:
    """Adapter over canonical Knowledge Hub services. Tests substitute an in-memory fake with the same two methods."""

    def store(self, sel: dict, filename: str, data: bytes, summary: str, tags: list) -> dict:
        from Core.repository_manager import RepositoryManager
        from Core.metadata_manager import MetadataManager
        tmp = tempfile.mkdtemp(prefix="qaais_design_")
        path = Path(tmp) / filename
        path.write_bytes(data)
        repo, meta = RepositoryManager(), MetadataManager()
        try:
            try:
                info = repo.save_file(source_file=str(path), domain=sel["domain"], module=sel["module"], knowledge_name=sel["knowledge_name"],
                                      version=sel["version"], document_type=sel["document_type"], source_type=SOURCE_TYPE)
            except FileExistsError:
                raise RegistryError(f"A design file named '{filename}' is already registered in this hierarchy. Rename the file or use a new Version.") from None
            except ValueError as error:
                raise RegistryError(str(error)) from None
            analysis = {"summary": summary, "tags": tags, "confidence": 1.0, "document_type": sel["document_type"], "platform": "", "category": "UI/UX Design", "business_process": ""}
            try:
                knowledge_id = meta.save_knowledge_item(domain=sel["domain"], module=sel["module"], knowledge_name=sel["knowledge_name"],
                                                        version=sel["version"], file_info=info, analysis=analysis, source_type=SOURCE_TYPE)
                meta.update_embedding_status(knowledge_id, "FAILED_NO_TEXT")
            except Exception:
                try:
                    Path(info["repository_path"]).unlink()
                except OSError:
                    pass
                raise
            ownership = meta.get_document_context(knowledge_id)
            return {"knowledge_id": int(knowledge_id), "document_version_id": int(ownership["document_version_id"]),
                    "source_file_id": info.get("source_file_id", ""), "file_name": info["file_name"],
                    "repository_path": info["repository_path"], "sha256": info["sha256"]}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def item(self, knowledge_id: int):
        from Core.metadata_manager import MetadataManager
        row = MetadataManager().get(int(knowledge_id))
        return dict(row) if row else None

    def figma_items(self) -> list:
        """Every Knowledge Hub source whose Source Type is Figma (the owner's view; read-only)."""
        from Core.metadata_manager import MetadataManager
        conn = MetadataManager().db.get_connection()
        try:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT id,document_version_id,domain,module,knowledge_name,document_type,version,file_name,repository_path,"
                                "repository_relative_path,sha256,source_file_id,extension FROM knowledge_items WHERE lower(source_type)=lower(?) ORDER BY id", (SOURCE_TYPE,)).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    def read_bytes(self, item: dict, limit: int = F.MAX_FIG_BYTES) -> bytes:
        """Read a managed source file; refuses anything outside the Central Repository."""
        from Shared.central_repository import CentralRepository
        central = CentralRepository()
        path = central.resolve_managed(item.get("repository_path"), item.get("repository_relative_path") or "")
        try:
            path.resolve().relative_to(central.root.resolve())
        except ValueError:
            raise RegistryError("The source file is outside the managed Knowledge Hub repository.") from None
        if not path.is_file():
            raise RegistryError("The source file is missing from the Knowledge Hub repository.")
        if path.stat().st_size > limit:
            raise RegistryError("The source file is larger than the supported design size.")
        return path.read_bytes()

    def discard(self, knowledge_id: int):
        from Core.metadata_manager import MetadataManager
        try:
            MetadataManager().delete(int(knowledge_id))
        except Exception:
            pass


class DesignRegistry:
    def __init__(self, db: UiuxDatabase, hierarchy: Hierarchy, storage: Storage, vault, hub=None, figma_factory=None, rasterizer=None):
        self.db, self.hierarchy, self.storage, self.vault = db, hierarchy, storage, vault
        self.hub = hub or KnowledgeHubPort()
        self.figma_factory = figma_factory or (lambda token: FigmaClient(token))
        self.rasterizer = rasterizer

    # ------------------------------------------------------------------ reads
    def _asset_row(self, c, asset_id):
        row = c.execute("SELECT * FROM uiux_design_assets WHERE id=?", (asset_id,)).fetchone()
        if not row:
            raise RegistryError("The design does not exist.")
        return row

    def _public(self, row, c=None, with_frames=True):
        d = dict(row)
        d["metadata"] = loads(d.pop("metadata_json"), {})
        if with_frames and c is not None:
            frames = c.execute("SELECT id,node_id,page_name,name,width,height,viewport_class,image_path,image_source,selected,nodes_json FROM uiux_frames WHERE asset_id=? ORDER BY page_name,name", (d["id"],)).fetchall()
            d["frames"] = [self._frame_public(f) for f in frames]
            d["frame_count"] = len(d["frames"])
        # the Knowledge Hub row is the owner: report when it has been removed there
        item = None
        try:
            item = self.hub.item(d["knowledge_id"]) if d.get("knowledge_id") else None
        except Exception:
            item = None
        d["source_status"] = "Available" if item else "Missing in Knowledge Hub"
        return d

    @staticmethod
    def _frame_public(f):
        nodes = loads(f["nodes_json"], []) if "nodes_json" in f.keys() else []
        return {"id": f["id"], "node_id": f["node_id"], "page": f["page_name"], "name": f["name"], "width": f["width"], "height": f["height"],
                "viewport_class": f["viewport_class"], "has_image": bool(f["image_path"]), "image_source": f["image_source"],
                "node_count": len(nodes), "has_structure": bool(nodes), "selected": bool(f["selected"])}

    def list_designs(self, document_version_id: int) -> list:
        try:
            self.sync_with_knowledge_hub(document_version_id)
        except Exception as error:  # the list must stay usable; the reason is logged, never shown with internals
            log.warning("Knowledge Hub design sync failed: %s", type(error).__name__)
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM uiux_design_assets WHERE document_version_id=? ORDER BY created_at DESC", (int(document_version_id),)).fetchall()
            return [self._public(r, c) for r in rows]

    def get(self, asset_id: str) -> dict:
        with self.db.connect() as c:
            return self._public(self._asset_row(c, asset_id), c)

    def frame(self, frame_id: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM uiux_frames WHERE id=?", (frame_id,)).fetchone()
            if not row:
                raise RegistryError("The frame does not exist.")
            d = dict(row)
            d["nodes"] = loads(d.pop("nodes_json"), [])
            d["variables"] = loads(d.pop("variables_json"), {})
            return d

    def frame_image_bytes(self, frame_id: str) -> bytes:
        f = self.frame(frame_id)
        if not f["image_path"]:
            raise RegistryError("This frame has no design image.")
        return self.storage.read(f["image_path"])

    # ------------------------------------------------------------------ hierarchy
    def _selection(self, selection: dict, create_missing: bool) -> dict:
        sel = {k: str(selection.get(k) or "").strip() for k in LEVELS[:5]}
        missing = [LABELS[k] for k in LEVELS[:5] if not sel[k]]
        if missing:
            raise RegistryError("Select " + ", ".join(missing) + " before registering a design.")
        try:
            self.hierarchy.resolve(sel)
        except HierarchyError:
            if not create_missing:
                raise RegistryError("That Knowledge / Document Type / Version does not exist yet. Choose 'create' to add it to Knowledge Hub with this design.") from None
            # Domain and Module must already exist; Knowledge / Document Type / Version are created by the canonical
            # ownership service when the first source is stored.
            if sel["module"] not in self.hierarchy.options("module", {"domain": sel["domain"]}):
                raise RegistryError("The Domain and Module must already exist in Knowledge Hub.") from None
        return sel

    # ------------------------------------------------------------------ registration
    def _insert_asset(self, sel, stored, kind, name_hint, content_hash, user, extraction_status, note, structural, metadata, figma_key="", figma_url="", owned=True, status="Registered"):
        asset_id = new_id("DSN")
        now = now_iso()
        try:
            with self.db.connect() as c:
                c.execute("""INSERT INTO uiux_design_assets(id,knowledge_id,document_version_id,domain,module,knowledge_name,document_type,version,source_type,source_file,
                    asset_kind,file_name,content_hash,figma_file_key,figma_url,extraction_status,extraction_note,structural_available,status,created_by,created_at,metadata_json)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (asset_id, stored["knowledge_id"], stored["document_version_id"], sel["domain"], sel["module"], sel["knowledge_name"], sel["document_type"], sel["version"],
                           SOURCE_TYPE, stored["file_name"], kind, stored["file_name"], content_hash, figma_key, figma_url, extraction_status, note, 1 if structural else 0,
                           status, user, now, dumps(metadata)))
        except sqlite3.IntegrityError:
            with self.db.connect() as c:
                existing = c.execute("SELECT id FROM uiux_design_assets WHERE knowledge_id=?", (stored["knowledge_id"],)).fetchone()
            if existing:
                return existing["id"]   # a concurrent Knowledge Hub sync registered the same source first: reuse it, never discard the shared file
            if owned:
                self.hub.discard(stored["knowledge_id"])
            raise
        except Exception:
            if owned:
                self.hub.discard(stored["knowledge_id"])  # compensate: do not leave an orphan Knowledge Hub item (only when this call created it)
            raise
        return asset_id

    def _duplicate_in_version(self, document_version_id, *content_hashes):
        hashes = [h for h in dict.fromkeys(content_hashes) if h]
        if not hashes:
            return None
        with self.db.connect() as c:
            return c.execute("SELECT id,file_name FROM uiux_design_assets WHERE document_version_id=? AND content_hash IN (%s)" % ",".join("?" * len(hashes)),
                             (int(document_version_id), *hashes)).fetchone()

    def _save_frame(self, c, asset_id, frame: dict, image_png: bytes | None = None, image_source: str = "", selected=1, variables=None):
        frame_id = f"{asset_id}:{re.sub(r'[^A-Za-z0-9]', '_', frame['node_id'])}"
        image_path, image_hash = "", ""
        if image_png:
            rel = self.storage.relative("designs", asset_id, frame_id.split(":", 1)[1] + ".png")
            self.storage.write(rel, image_png)
            image_path, image_hash = rel, sha256_bytes(image_png)
        existing = c.execute("SELECT image_path,image_hash,image_source FROM uiux_frames WHERE id=?", (frame_id,)).fetchone()
        if existing and not image_png:
            image_path, image_hash, image_source = existing["image_path"], existing["image_hash"], existing["image_source"]
        c.execute("""INSERT INTO uiux_frames(id,asset_id,node_id,page_name,name,width,height,viewport_class,image_path,image_hash,image_source,nodes_json,variables_json,selected,updated_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(asset_id,node_id) DO UPDATE SET page_name=excluded.page_name,name=excluded.name,width=excluded.width,height=excluded.height,
              viewport_class=excluded.viewport_class,image_path=excluded.image_path,image_hash=excluded.image_hash,image_source=excluded.image_source,
              nodes_json=CASE WHEN excluded.nodes_json='[]' THEN uiux_frames.nodes_json ELSE excluded.nodes_json END,
              variables_json=excluded.variables_json,updated_at=excluded.updated_at""",
                  (frame_id, asset_id, frame["node_id"], frame.get("page", ""), frame["name"], frame["width"], frame["height"], frame.get("viewport_class", classify_viewport(frame["width"])),
                   image_path, image_hash, image_source, dumps(frame.get("nodes", [])), dumps(variables or {}), selected, now_iso()))
        return frame_id

    @staticmethod
    def _to_png(data: bytes) -> tuple[bytes, int, int]:
        from PIL import Image
        with Image.open(io.BytesIO(data)) as im:
            im = im.convert("RGBA") if im.mode in ("P", "LA", "RGBA") else im.convert("RGB")
            buf = io.BytesIO()
            im.save(buf, "PNG")
            return buf.getvalue(), im.width, im.height

    def _analyse(self, kind: str, filename: str, data: bytes) -> dict:
        """Inspect one design file without storing anything. Shared by direct registration and by adoption of a Knowledge Hub source."""
        metadata, frames_to_add, structural = {}, [], False
        extraction_status, note, store_bytes = "", "", data
        try:
            if kind in ("png", "jpeg"):
                w, h = F.validate_raster(data)
                png, _, _ = self._to_png(data)
                frames_to_add.append(({"node_id": "image", "page": "", "name": Path(filename).stem, "width": w, "height": h, "nodes": []}, png, "upload"))
                extraction_status = "Image only"
                note = "Visual comparison is available. The image carries no structural properties, so size, colour and typography checks will be reported as Not Verifiable."
            elif kind == "svg":
                clean, removed = F.sanitize_svg(data)
                store_bytes = clean
                metadata["svg_sanitized"] = removed[:50]
                if self.rasterizer is None:
                    from .browser import render_svg_png
                    self.rasterizer = render_svg_png
                try:
                    png = self.rasterizer(clean)
                    w, h = F.validate_raster(png)
                    frames_to_add.append(({"node_id": "image", "page": "", "name": Path(filename).stem, "width": w, "height": h, "nodes": []}, png, "svg-raster"))
                    extraction_status = "Image only (SVG rasterised)"
                    note = "SVG was sanitised (scripts and external references removed) and rasterised for visual comparison. SVG shape properties are not used for structural checks."
                except Exception:
                    extraction_status = "Preserved - rasterisation unavailable"
                    note = "The sanitised SVG is stored but could not be rasterised on this server, so it cannot be used for visual comparison."
            elif kind == "fig":
                info = F.inspect_fig(data)
                metadata["fig"] = {"container": info["container"], "meta": info["meta"], "entries": info["entries"][:50], "notes": info["notes"]}
                if info["thumbnail"]:
                    w, h = F.validate_raster(info["thumbnail"])
                    png, _, _ = self._to_png(info["thumbnail"])
                    frames_to_add.append(({"node_id": "thumbnail", "page": "", "name": "Embedded thumbnail", "width": w, "height": h, "nodes": []}, png, "fig-thumbnail"))
                extraction_status = "Preserved - structural analysis unavailable"
                note = " ".join(info["notes"])
            elif kind == "figma_json":
                document = json.loads(data.decode("utf-8"))
                frames = extract_frames(document)
                if not frames:
                    raise RegistryError("The Figma JSON contains no top-level frames with bounding boxes.")
                for fr in frames[:200]:
                    frames_to_add.append((fr, None, ""))
                structural = True
                extraction_status = f"Structure extracted ({len(frames)} frame(s))"
                note = "Frame images are not part of a JSON export. Attach a PNG export to each frame, or connect the Figma file, to enable visual comparison."
                metadata["figma"] = {"name": str(document.get("name", ""))[:200], "version": str(document.get("version", ""))[:60], "lastModified": str(document.get("lastModified", ""))[:60]}
        except F.UploadRejected as error:
            raise RegistryError(str(error)) from None
        return {"store_bytes": store_bytes, "frames": frames_to_add, "structural": structural, "extraction_status": extraction_status, "note": note, "metadata": metadata}

    def _write_frames(self, asset_id, a):
        with self.db.connect() as c:
            for frame, png, source in a["frames"]:
                self._save_frame(c, asset_id, frame, png, source)
            if a["structural"]:
                c.execute("UPDATE uiux_design_assets SET status='Analyzed',last_analysis_at=? WHERE id=?", (now_iso(), asset_id))

    def register_upload(self, selection: dict, filename: str, data: bytes, user: str, create_missing: bool = False) -> dict:
        filename = preserve_filename(filename, "design")          # the Source Document keeps the name the user uploaded
        try:
            kind = F.sniff(data, filename)
        except F.UploadRejected as error:
            raise RegistryError(str(error)) from None
        sel = self._selection(selection, create_missing)
        a = self._analyse(kind, filename, data)
        content_hash = sha256_bytes(a["store_bytes"])
        version_id = self._doc_version_preview(sel)
        if version_id:
            try:
                self.sync_with_knowledge_hub(version_id)      # sources already in Knowledge Hub count for duplicate detection too
            except Exception as error:
                log.warning("Knowledge Hub design sync failed: %s", type(error).__name__)
            dup = self._duplicate_in_version(version_id, content_hash, sha256_bytes(data))
            if dup:
                return {"duplicate": True, "asset": self.get(dup["id"]), "message": f"This design is already registered in this version as '{dup['file_name']}'; no duplicate was stored."}
        summary = f"UI/UX design asset ({kind}) registered for AI UI/UX Testing."
        stored = self.hub.store(sel, filename, a["store_bytes"], summary, ["design", "ui-ux", kind])
        asset_id = self._insert_asset(sel, stored, kind, filename, content_hash, user, a["extraction_status"], a["note"], a["structural"], a["metadata"])
        self._write_frames(asset_id, a)
        return {"duplicate": False, "asset": self.get(asset_id), "message": "Design registered in Knowledge Hub."}

    # ------------------------------------------------------------------ Knowledge Hub is the owner: keep both views in step
    @staticmethod
    def _kind_for(item: dict) -> str:
        return {".fig": "fig", ".png": "png", ".jpg": "jpeg", ".jpeg": "jpeg", ".svg": "svg", ".json": "figma_json"}.get(str(item.get("extension") or Path(item.get("file_name", "")).suffix).lower(), "")

    def adopt_item(self, item: dict, user: str = "Knowledge Hub") -> str | None:
        """Register testing-side metadata for a Figma source that Knowledge Hub already stores (uploaded there). Never writes to Knowledge Hub."""
        sel = {"domain": item["domain"], "module": item["module"], "knowledge_name": item["knowledge_name"], "document_type": item["document_type"], "version": item["version"]}
        stored = {"knowledge_id": item["id"], "document_version_id": item.get("document_version_id"), "file_name": item["file_name"]}
        kind, status, a, failure = self._kind_for(item), "Registered", None, ""
        try:
            data = self.hub.read_bytes(item)
            if not kind:
                raise RegistryError("This file type is not a supported design format (.fig, Figma JSON, PNG, JPEG, SVG).")
            declared = F.sniff(data, item["file_name"])
            kind = declared
            a = self._analyse(declared, item["file_name"], data)
        except (F.UploadRejected, RegistryError, ValueError, OSError) as error:
            failure = str(error)[:300]
        if a is None:
            a = {"store_bytes": b"", "frames": [], "structural": False, "metadata": {}, "extraction_status": "Not analysable", "note": failure}
            status = "Needs attention"
            kind = kind or "unsupported"
        try:
            asset_id = self._insert_asset(sel, stored, kind, item["file_name"], item.get("sha256") or sha256_bytes(a["store_bytes"]), user, a["extraction_status"], a["note"],
                                          a["structural"], {**a["metadata"], "adopted_from_knowledge_hub": True}, owned=False, status=status)
        except sqlite3.IntegrityError:
            return None  # another request registered it first
        self._write_frames(asset_id, a)
        return asset_id

    def sync_with_knowledge_hub(self, document_version_id=None) -> dict:
        """Designs uploaded through Knowledge Hub become selectable here; hierarchy changes made in Knowledge Hub (move, rename) are followed."""
        adopted, refreshed = 0, 0
        items = self.hub.figma_items()
        with self.db.connect() as c:
            known = {r["knowledge_id"]: r for r in c.execute("SELECT id,knowledge_id,document_version_id,domain,module,knowledge_name,document_type,version,source_file FROM uiux_design_assets WHERE knowledge_id IS NOT NULL")}
        for item in items:
            row = known.get(item["id"])
            if row is None:
                if document_version_id is not None and item.get("document_version_id") != int(document_version_id):
                    continue            # only the version being looked at is analysed now; the rest follows when it is opened
                if self.adopt_item(item):
                    adopted += 1
                continue
            current = (item.get("document_version_id"), item["domain"], item["module"], item["knowledge_name"], item["document_type"], item["version"], item["file_name"])
            if current != (row["document_version_id"], row["domain"], row["module"], row["knowledge_name"], row["document_type"], row["version"], row["source_file"]):
                with self.db.connect() as c:
                    c.execute("UPDATE uiux_design_assets SET document_version_id=?,domain=?,module=?,knowledge_name=?,document_type=?,version=?,source_file=? WHERE id=?", (*current, row["id"]))
                refreshed += 1
        return {"adopted": adopted, "refreshed": refreshed}

    def _doc_version_preview(self, sel):
        try:
            return self.hierarchy.resolve(sel)["document_version_id"]
        except HierarchyError:
            return None

    # ------------------------------------------------------------------ Figma
    def register_figma_link(self, selection: dict, url_or_key: str, user: str, create_missing: bool = False) -> dict:
        try:
            ref = parse_figma_url(url_or_key)
        except FigmaError as error:
            raise RegistryError(str(error)) from None
        sel = self._selection(selection, create_missing)
        token, _ = self.vault.figma_token()
        client = self.figma_factory(token)
        try:
            doc = client.get_file(ref["file_key"], depth=2)
        except FigmaError as error:
            raise RegistryError(str(error)) from None
        name = str(doc.get("name") or ref["file_key"])[:150]
        frames = extract_frames(doc, ref["file_key"])
        descriptor = {"type": "figma-link", "file_key": ref["file_key"], "url": ref["url"], "name": name,
                      "figma_version": str(doc.get("version", "")), "last_modified": str(doc.get("lastModified", ""))}
        blob = json.dumps(descriptor, indent=2).encode("utf-8")
        content_hash = sha256_bytes(blob)
        ver = self._doc_version_preview(sel)
        if ver:
            with self.db.connect() as c:
                dup = c.execute("SELECT id,file_name FROM uiux_design_assets WHERE document_version_id=? AND figma_file_key=?", (int(ver), ref["file_key"])).fetchone()
            if dup:
                return {"duplicate": True, "asset": self.get(dup["id"]), "message": f"This Figma file is already connected in this version as '{dup['file_name']}'."}
        filename = safe_filename(name, "figma") + ".figma-link.json"
        stored = self.hub.store(sel, filename, blob, f"Figma design link '{name}' for AI UI/UX Testing (no credentials stored).", ["design", "figma", "link"])
        asset_id = self._insert_asset(sel, stored, "figma_link", filename, content_hash, user, "Connected - frames listed",
                                      "Frame structure and images are fetched when you run Analyze Design.", False,
                                      {"figma": {"name": name, "version": descriptor["figma_version"], "lastModified": descriptor["last_modified"], "initial_node": ref["node_id"]}},
                                      ref["file_key"], ref["url"])
        with self.db.connect() as c:
            for fr in frames[:200]:
                fr["nodes"] = []  # structure comes from Analyze Design (depth-limited listing carries no children)
                self._save_frame(c, asset_id, fr, None, "", selected=1 if (not ref["node_id"] or fr["node_id"] == ref["node_id"]) else 0)
        return {"duplicate": False, "asset": self.get(asset_id), "message": "Figma file connected and registered in Knowledge Hub."}

    def select_frames(self, asset_id: str, node_ids: list) -> dict:
        with self.db.connect() as c:
            self._asset_row(c, asset_id)
            c.execute("UPDATE uiux_frames SET selected=0 WHERE asset_id=?", (asset_id,))
            for nid in node_ids:
                c.execute("UPDATE uiux_frames SET selected=1 WHERE asset_id=? AND node_id=?", (asset_id, nid))
        return self.get(asset_id)

    def analyze(self, asset_id: str, user: str, scale: float = 1.0) -> dict:
        """Fetch (or re-fetch) structure and images for the selected frames. Reports every limitation."""
        asset = self.get(asset_id)
        notes, counts = [], {"frames": 0, "images": 0, "nodes": 0}
        if asset["asset_kind"] != "figma_link":
            msg = {"figma_json": "Structure came from the uploaded Figma JSON; use a frame's image upload to add visual references.",
                   "fig": "Native .fig files cannot be analysed structurally. Connect the design by Figma URL or upload a Figma JSON / PNG export.",
                   }.get(asset["asset_kind"], "Image designs need no analysis; visual comparison uses the image as the expected design.")
            with self.db.connect() as c:
                c.execute("UPDATE uiux_design_assets SET last_analysis_at=?,status=? WHERE id=?",
                          (now_iso(), "Analyzed" if asset["asset_kind"] != "fig" else "Structure unavailable", asset_id))
            return {"asset": self.get(asset_id), "notes": [msg], "counts": counts}
        token, source = self.vault.figma_token()
        client = self.figma_factory(token)
        selected = [f for f in asset["frames"] if f["selected"]][:MAX_FRAMES_PER_ANALYSIS]
        if not selected:
            raise RegistryError("Select at least one frame to analyse.")
        ids = [f["node_id"] for f in selected]
        key = asset["figma_file_key"]
        try:
            nodes = client.get_nodes(key, ids).get("nodes") or {}
            variables = client.get_variables(key)
            urls = client.get_image_urls(key, ids, scale=scale)
        except FigmaError as error:
            with self.db.connect() as c:
                c.execute("UPDATE uiux_design_assets SET status='Needs attention',extraction_status=?,extraction_note=? WHERE id=?", ("Figma request failed", str(error)[:300], asset_id))
            raise RegistryError(str(error)) from None
        if variables.get("unavailable"):
            notes.append(variables["reason"])
        with self.db.connect() as c:
            for f in selected:
                entry = (nodes.get(f["node_id"]) or {}).get("document")
                parsed = parse_frame_node(entry, key, f["page"]) if entry else None
                if not parsed:
                    notes.append(f"Frame '{f['name']}' was not returned by Figma (deleted or not accessible).")
                    continue
                resolve_variable_names([parsed], variables)
                png = None
                url = urls.get(f["node_id"])
                if url:
                    try:
                        raw = client.download_image(url)
                        F.validate_raster(raw)
                        png, _, _ = self._to_png(raw)
                        counts["images"] += 1
                    except (FigmaError, F.UploadRejected) as error:
                        notes.append(f"Frame '{f['name']}': image not retrieved ({error}).")
                else:
                    notes.append(f"Frame '{f['name']}': Figma did not render an image.")
                if parsed["truncated"]:
                    notes.append(f"Frame '{f['name']}' has more than the supported number of layers; extra layers were not analysed.")
                self._save_frame(c, asset_id, parsed, png, "figma-render", selected=1)
                counts["frames"] += 1
                counts["nodes"] += len(parsed["nodes"])
            c.execute("UPDATE uiux_design_assets SET status='Analyzed',structural_available=1,last_analysis_at=?,last_refresh_at=?,extraction_status=?,extraction_note=? WHERE id=?",
                      (now_iso(), now_iso(), f"Structure and images retrieved from the Figma API ({counts['frames']} frame(s))", "; ".join(notes)[:500], asset_id))
        return {"asset": self.get(asset_id), "notes": notes, "counts": counts, "token_source": source}

    def attach_frame_image(self, frame_id: str, data: bytes, user: str) -> dict:
        try:
            kind = F.sniff(data, "x.png" if data.startswith(b"\x89PNG") else "x.jpg")
            F.validate_raster(data)
            png, w, h = self._to_png(data)
        except F.UploadRejected as error:
            raise RegistryError(str(error)) from None
        f = self.frame(frame_id)
        if f["width"] and abs(w - f["width"]) > 2:
            raise RegistryError(f"The image is {w}px wide but the frame is {int(f['width'])}px wide. Export the frame at 1x so pixels match design units.")
        rel = self.storage.relative("designs", f["asset_id"], f["id"].split(":", 1)[1] + ".png")
        self.storage.write(rel, png)
        with self.db.connect() as c:
            c.execute("UPDATE uiux_frames SET image_path=?,image_hash=?,image_source='attached',updated_at=? WHERE id=?", (rel, sha256_bytes(png), now_iso(), frame_id))
        return {"frame_id": frame_id, "width": w, "height": h}
