# Create: AI/Core/knowledge_web_repository.py

"""
QA AI Studio — Web
Knowledge Hub Web Repository

Version: 1.0

Thin web-facing wrapper around the SAME Core classes the desktop
app's Knowledge Hub screens already use (MetadataManager,
RepositoryManager, UploadPipeline, VectorStore) — none of those
files change to support this; this class just adapts their
request/response shape for stateless HTTP handlers, the same
pattern Core/user_repository.py established for Milestone 6.

What's genuinely new here (not just a passthrough):
- upload(): the desktop app hands UploadPipeline a filesystem path
  it already has (a file the user picked). A web upload arrives as
  in-memory bytes from a multipart request instead, so this writes
  them to a short-lived temp file, runs the exact same
  UploadPipeline.upload_file() the desktop app calls, then cleans
  the temp file up — the pipeline itself is untouched.
- delete_item(): MetadataManager.delete() only removes the three DB
  rows. RepositoryManager.delete_knowledge() and
  UploadPipeline.delete_knowledge() both operate at the whole
  knowledge-name level (every version at once), which is coarser
  than deleting the one version a user actually clicked delete on.
  This method deletes precisely the one version's file folder and
  its vector chunks, then the one DB row — safe to call even when
  other versions of the same knowledge item still exist.
"""

import json
import shutil
import tempfile
import uuid
from pathlib import Path

from Core.metadata_manager import MetadataManager
from Core.repository_manager import RepositoryManager
from Core.knowledge_merge import KnowledgeMerge
from Core.version_compare import VersionCompare
from Core.smart_upload import SmartUpload


def _row_to_dict(row):

    if row is None:

        return None

    item = dict(row)

    raw_tags = item.get("tags")

    if raw_tags:

        try:
            item["tags"] = json.loads(raw_tags)
        except (TypeError, ValueError):
            item["tags"] = [t for t in str(raw_tags).split(",") if t]

    else:

        item["tags"] = []

    return item


class KnowledgeRepository:

    def __init__(self):

        self.metadata = MetadataManager()
        self.repository = RepositoryManager()

    # --------------------------------------------------
    # Browse
    # --------------------------------------------------

    def get_tree(self):

        return self.metadata.get_tree()

    def list_domains(self):

        return self.metadata.list_domains()

    def create_domain(self, name):
        name = (name or "").strip()
        if not name:
            raise ValueError("Domain name is required.")
        if any(existing.casefold() == name.casefold() for existing in self.list_domains()):
            raise ValueError(f"Domain '{name}' already exists.")
        return self.metadata.create_domain(name)

    def delete_domain(self, name):
        return self.metadata.delete_domain_by_name(name)

    def list_modules(self, domain):

        return self.metadata.list_modules(domain)

    def create_module(self, domain, name):
        domain = (domain or "").strip()
        name = (name or "").strip()
        if not domain or not name:
            raise ValueError("Domain and module name are required.")
        return self.metadata.get_or_create_module(domain, name)

    def delete_module(self, domain, name):
        return self.metadata.delete_module_by_name(domain, name)

    def list_items(self, search=None):

        rows = (
            self.metadata.search(search)
            if search
            else self.metadata.list_all()
        )

        items = [_row_to_dict(row) for row in rows]
        api_repository = None
        discovery_repository = None
        for item in items:
            if item.get("source_type") == "API_COLLECTION":
                from Core.api_collection_repository import ApiCollectionRepository
                api_repository = api_repository or ApiCollectionRepository()
                item["api_collections"] = []
                for collection in api_repository.list_collections():
                    if collection.get("linked_knowledge_item_id") == item["id"]:
                        item["api_collections"].append({
                            "id": collection["id"],
                            "name": collection.get("name"),
                            "endpoints": [
                                {
                                    "id": endpoint["id"],
                                    "method": endpoint.get("method"),
                                    "name": endpoint.get("name"),
                                    "folder_path": endpoint.get("folder_path"),
                                    "url": endpoint.get("url_resolved") or endpoint.get("url_raw"),
                                }
                                for endpoint in api_repository.list_endpoints(collection["id"])
                            ],
                        })
            elif item.get("source_type") == "URL_CAPTURE":
                from Core.discovery_repository import DiscoveryRepository
                discovery_repository = discovery_repository or DiscoveryRepository()
                item["captured_flows"] = discovery_repository.get_captured_flows_for_knowledge_item(item["id"])
        return items

    def get_item(self, knowledge_id):

        return _row_to_dict(self.metadata.get(knowledge_id))

    def get_item_versions(self, knowledge_id):

        selected = self.get_item(knowledge_id)
        if selected is None:
            return []

        versions = []
        for item in self.list_items():
            if (
                item.get("domain") == selected.get("domain")
                and item.get("module") == selected.get("module")
                and item.get("knowledge_name") == selected.get("knowledge_name")
            ):
                versions.append({
                    "knowledge_id": item["id"],
                    "version": item.get("version"),
                    "sha256": item.get("sha256"),
                    "source": item.get("file_name"),
                    "status": item.get("status"),
                    "document_type": item.get("document_type"),
                    "storage_location": self._managed_storage_location(item.get("repository_path")),
                    "created_date": item.get("created_date"),
                })
        return sorted(versions, key=lambda row: (str(row["version"]), str(row["source"])))

    def get_item_details(self, knowledge_id):

        item = self.get_item(knowledge_id)

        if item is None:
            return None

        vector_ids = self._vector_ids_for_item(item)
        item["storage_location"] = self._managed_storage_location(
            item.get("repository_path")
        )
        item["chunks"] = len(vector_ids)
        item["vectors"] = len(vector_ids)

        return item

    def _managed_storage_location(self, path):

        if not path:
            return ""

        try:
            resolved = Path(path).resolve()
            return str(resolved.relative_to(self.repository.repository_root.resolve()))
        except (OSError, ValueError):
            return "External or unavailable"

    def _vector_ids_for_item(self, item):
        from Core.vector_store import VectorStore

        data = VectorStore().get_all() or {}
        sha256 = str(item.get("sha256") or "")
        ids = []

        if item.get("source_type") == "API_COLLECTION":
            from Core.api_collection_repository import ApiCollectionRepository
            collection_ids = {
                str(collection["id"])
                for collection in ApiCollectionRepository().list_collections()
                if collection.get("linked_knowledge_item_id") == item.get("id")
            }
            return [
                str(doc_id) for doc_id in data.get("ids", [])
                if any(str(doc_id).startswith(f"apicollection_{collection_id}_") for collection_id in collection_ids)
            ]

        for doc_id, metadata in zip(
            data.get("ids", []), data.get("metadatas", [])
        ):
            metadata = metadata or {}
            if (
                metadata.get("domain") == item.get("domain")
                and metadata.get("module") == item.get("module")
                and metadata.get("knowledge_name") == item.get("knowledge_name")
                and metadata.get("version") == item.get("version")
                and metadata.get("file_name") == item.get("file_name")
                and (not sha256 or sha256 in str(doc_id))
            ):
                ids.append(str(doc_id))

        return ids

    # --------------------------------------------------
    # Edit
    # --------------------------------------------------

    def update_item(self, knowledge_id, **fields):
        from Core.vector_store import VectorStore

        existing = self.get_item(knowledge_id)

        if existing is None:
            raise ValueError("Knowledge item not found.")

        for key in ("domain", "module", "knowledge_name", "version", "document_type"):
            if key in fields:
                fields[key] = str(fields[key] or "").strip()

        required = {
            "domain": fields.get("domain", existing.get("domain")),
            "module": fields.get("module", existing.get("module")),
            "knowledge_name": fields.get(
                "knowledge_name", existing.get("knowledge_name")
            ),
            "version": fields.get("version", existing.get("version")),
            "document_type": fields.get(
                "document_type", existing.get("document_type")
            ),
        }

        missing = [name for name, value in required.items() if not value]

        if missing:
            raise ValueError("Required and missing: " + ", ".join(missing) + ".")

        vector_updates = {
            key: fields[key]
            for key in (
                "domain", "module", "knowledge_name", "version",
                "document_type", "summary", "tags"
            )
            if key in fields
        }

        if isinstance(vector_updates.get("tags"), list):
            vector_updates["tags"] = ", ".join(vector_updates["tags"])

        store = VectorStore()
        vector_ids = self._vector_ids_for_item(existing)
        vector_snapshot = store.update_metadata(vector_ids, vector_updates)

        # Tags arrive from the web as a list; the column stores JSON
        # text, same encoding save_knowledge_item() already uses.
        if "tags" in fields and isinstance(fields["tags"], list):

            fields["tags"] = json.dumps(fields["tags"])

        try:
            updated = self.metadata.update_knowledge_item(knowledge_id, **fields)

            if not updated:
                raise ValueError("Knowledge item no longer exists.")

        except Exception:
            store.restore_metadata(vector_snapshot)
            raise

        result = self.get_item_details(knowledge_id)
        result["vectors_updated"] = len(vector_ids)
        result["storage_strategy"] = "immutable_managed_path"

        return result

    # --------------------------------------------------
    # Delete (one version, not the whole knowledge name)
    # --------------------------------------------------

    def delete_item(self, knowledge_id):
        from Core.vector_store import VectorStore

        item = self.get_item(knowledge_id)

        if item is None:

            return None

        managed_file = self._managed_file(item.get("repository_path"))
        other_references = [
            row for row in self.list_items()
            if row["id"] != knowledge_id
            and row.get("repository_path") == item.get("repository_path")
        ]
        staged_file = None

        if managed_file and managed_file.is_file() and not other_references:
            staged_file = managed_file.with_name(
                f".{managed_file.name}.deleting-{uuid.uuid4().hex}"
            )
            managed_file.replace(staged_file)

        store = VectorStore()
        vector_ids = [] if other_references else self._vector_ids_for_item(item)
        vector_snapshot = None

        try:
            vector_snapshot = store.delete_with_snapshot(vector_ids)
            db_summary = self.metadata.delete(knowledge_id)

            if db_summary.get("knowledge") != 1:
                raise RuntimeError("Metadata record was not deleted.")

        except Exception:
            if vector_snapshot:
                store.restore_snapshot(vector_snapshot)
            if staged_file and staged_file.exists():
                staged_file.replace(managed_file)
            raise

        files_removed = 0
        storage_cleanup_error = ""

        if staged_file and staged_file.exists():
            try:
                staged_file.unlink()
                files_removed = 1
                self._prune_empty_managed_folders(managed_file.parent)
            except OSError as error:
                storage_cleanup_error = str(error)

        vectors_removed = len(vector_ids)

        return {
            "item": item,
            "files_removed": files_removed,
            "vectors_removed": vectors_removed,
            "storage_cleanup_error": storage_cleanup_error,
            **db_summary,
        }

    def _managed_file(self, path):

        if not path:
            return None

        try:
            resolved = Path(path).resolve()
            resolved.relative_to(self.repository.repository_root.resolve())
            return resolved
        except (OSError, ValueError):
            return None

    def _prune_empty_managed_folders(self, folder):

        root = self.repository.repository_root.resolve()
        current = folder.resolve()

        while current != root and root in current.parents:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _delete_vectors_for_version(self, domain, module, knowledge_name, version):
        from Core.vector_store import VectorStore

        store = VectorStore()

        data = store.get_all()

        if not data:

            return 0

        ids = data.get("ids", [])
        metadatas = data.get("metadatas", [])

        delete_ids = [
            doc_id
            for doc_id, meta in zip(ids, metadatas)
            if meta.get("domain") == domain
            and meta.get("module") == module
            and meta.get("knowledge_name") == knowledge_name
            and meta.get("version") == version
        ]

        for doc_id in delete_ids:

            store.delete(doc_id)

        return len(delete_ids)

    # --------------------------------------------------
    # Upload
    # --------------------------------------------------

    def upload(
        self,
        file_bytes,
        original_filename,
        domain,
        module,
        knowledge_name,
        version,
        document_type="",
        source_type="FILE",
        reviewed_analysis=None,
    ):

        from Core.upload_pipeline import UploadPipeline

        domain = (domain or "").strip()
        module = (module or "").strip()
        knowledge_name = (knowledge_name or "").strip()
        version = (version or "1.0").strip() or "1.0"
        source_type = (source_type or "FILE").strip()
        allowed_source_types = {
            "FILE", "Files", "Folder", "Release Notes", "Test Cases",
            "SOP Documents", "SMART_UPLOAD",
        }
        if source_type not in allowed_source_types:
            raise ValueError("Unsupported document source type.")

        missing = [
            label
            for label, value in (
                ("domain", domain),
                ("module", module),
                ("knowledge_name", knowledge_name),
            )
            if not value
        ]

        if missing or not file_bytes:

            problem = ", ".join(missing) if missing else "file"

            raise ValueError(f"Required and missing: {problem}.")

        safe_name = Path(original_filename or "upload").name or "upload"
        duplicate = next(
            (
                item for row in self.metadata.list_all()
                if (item := _row_to_dict(row))
                if item.get("domain") == domain
                and item.get("module") == module
                and item.get("knowledge_name") == knowledge_name
                and item.get("version") == version
                and item.get("file_name") == safe_name
            ),
            None,
        )
        if duplicate:
            raise ValueError(
                "This source already exists in the selected Knowledge version. "
                "Use a new version or remove the existing source first."
            )

        # A plain NamedTemporaryFile gets a random generated basename
        # (e.g. "tmpXXXXXX.txt") — RepositoryManager.save_file() names
        # the file it copies into the repo after whatever path it's
        # given, so that random name would end up as the permanently
        # stored file_name/repository_path. Using a temp DIRECTORY and
        # writing the file under its REAL original name keeps the
        # repo folder and the metadata grid showing the name the user
        # actually uploaded.
        temp_dir = tempfile.mkdtemp(prefix="qaais_upload_")
        temp_path = Path(temp_dir) / safe_name

        temp_path.write_bytes(file_bytes)

        try:

            pipeline = UploadPipeline()

            result = pipeline.upload_file(
                source_file=str(temp_path),
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=version,
                document_type=document_type,
                source_type=source_type,
                reviewed_analysis=reviewed_analysis,
            )

        finally:

            shutil.rmtree(temp_dir, ignore_errors=True)

        return result

    # --------------------------------------------------
    # AI Smart Upload — suggest Domain/Module/Knowledge Name/Version/
    # tags from the file's own content, before the user commits to an
    # upload. Read-only: does not touch the repository, DB, or vector
    # store — the operator reviews/edits the suggestion, then calls
    # upload() normally (same as the desktop app's Smart Upload flow).
    # --------------------------------------------------

    def suggest_classification(self, file_bytes, original_filename):

        if not file_bytes:

            raise ValueError("A file is required.")

        safe_name = Path(original_filename or "upload").name or "upload"

        temp_dir = tempfile.mkdtemp(prefix="qaais_smart_")
        temp_path = Path(temp_dir) / safe_name

        temp_path.write_bytes(file_bytes)

        try:

            result = SmartUpload().analyze(str(temp_path))

        finally:

            shutil.rmtree(temp_dir, ignore_errors=True)

        return result

    # --------------------------------------------------
    # Merge Knowledge — combine two or more items' summary/tags into
    # one preview. Read-only: this does NOT write anything back (the
    # desktop backend has no "save merged result" path either — it's
    # a preview a user can copy from). Kept that way here too rather
    # than inventing a new write path the desktop app doesn't have.
    # --------------------------------------------------

    def merge_items(self, knowledge_ids):

        if not knowledge_ids or len(knowledge_ids) < 2:

            raise ValueError("Select at least two knowledge items to merge.")

        result = KnowledgeMerge().merge(knowledge_ids)

        if result["count"] != len(knowledge_ids):

            raise ValueError("One or more of the selected items no longer exist.")

        return {
            "items": [self.get_item(kid) for kid in knowledge_ids],
            **result,
        }

    # --------------------------------------------------
    # Compare Versions — field-by-field diff between two knowledge
    # items (typically two versions of the same knowledge name).
    # --------------------------------------------------

    def compare_versions(self, knowledge_id_a, knowledge_id_b):

        row_a = self.metadata.get(knowledge_id_a)
        row_b = self.metadata.get(knowledge_id_b)

        if row_a is None or row_b is None:

            raise ValueError("One or both knowledge items no longer exist.")

        logical_fields = ("domain", "module", "knowledge_name")
        if any(row_a[field] != row_b[field] for field in logical_fields):
            raise ValueError(
                "Compare Versions requires two versions of the same Domain, Module, and Knowledge Name."
            )

        if knowledge_id_a == knowledge_id_b or row_a["version"] == row_b["version"]:
            raise ValueError("Select two different versions of this Knowledge.")

        changes = VersionCompare().compare(row_a, row_b)

        return {
            "a": self.get_item(knowledge_id_a),
            "b": self.get_item(knowledge_id_b),
            "changes": changes,
        }
