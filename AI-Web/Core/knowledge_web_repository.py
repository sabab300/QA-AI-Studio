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


# QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4 Rule 1:
# Knowledge Hub = source knowledge only. These Document Types are
# automation assets (API Collection -> Rule 5, SQL/SQA Script ->
# Rule 6) and must never be newly assigned to a Knowledge Hub item —
# neither on upload() (pure creation) nor via update_item() (which
# would otherwise let someone re-introduce/reassign the value on an
# existing row). A row that already legitimately carries one of these
# values from before this rule existed stays fully readable, and
# update_item() below only blocks CHANGING a row's document_type to
# one of these values, not saving other fields on a row that already
# has it — see the check in update_item() for exactly why.
DISALLOWED_NEW_DOCUMENT_TYPES = {"API Documentation"}


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
        connection = self.metadata.db.get_connection()
        connection.row_factory = lambda cursor, row: {
            column[0]: row[index] for index, column in enumerate(cursor.description)
        }
        try:
            for item in items:
                versions = connection.execute(
                    "SELECT id,version_number,source_file_id,folder_path,workbook_path,created_date "
                    "FROM test_case_versions WHERE source_knowledge_id=? ORDER BY version_number,id",
                    (item["id"],),
                ).fetchall()
                item["test_case_folders"] = []
                for version in versions:
                    folder = dict(version)
                    folder["name"] = Path(folder.get("folder_path") or "").name
                    folder["files"] = [dict(row) for row in connection.execute(
                        "SELECT id,file_name,workbook_path,sha256,created_date FROM test_case_files "
                        "WHERE test_case_version_id=? ORDER BY id",
                        (folder["id"],),
                    ).fetchall()]
                    item["test_case_folders"].append(folder)
        except Exception:
            # Databases predating QA Engineering versions still remain browsable.
            for item in items:
                item.setdefault("test_case_folders", [])
        finally:
            connection.close()
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
                            "source_file_name": collection.get("source_file_name"),
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
                    "storage_location": self._managed_storage_location(
                        item.get("repository_path"), item.get("repository_relative_path")
                    ),
                    "created_date": item.get("created_date"),
                })
        return sorted(versions, key=lambda row: (str(row["version"]), str(row["source"])))

    def get_item_details(self, knowledge_id):

        item = self.get_item(knowledge_id)

        if item is None:
            return None

        vector_ids = self._vector_ids_for_item(item)
        item["storage_location"] = self._managed_storage_location(
            item.get("repository_path"), item.get("repository_relative_path")
        )
        item["chunks"] = len(vector_ids)
        item["vectors"] = len(vector_ids)

        return item

    def _managed_storage_location(self, path, relative_path=""):

        if not path:
            return ""

        try:
            resolved = self.repository.central.resolve_managed(path, relative_path)
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

        # Rule 1: block CHANGING document_type to a disallowed value.
        # A row that already has one of these values (from before this
        # rule existed) can still be saved unchanged — the frontend's
        # edit dropdown injects the row's current value as an option
        # precisely so "save this row with everything else edited"
        # keeps working — this only rejects actually assigning the
        # disallowed value to a row that didn't already have it.
        if (
            "document_type" in fields
            and fields["document_type"] in DISALLOWED_NEW_DOCUMENT_TYPES
            and fields["document_type"] != existing.get("document_type")
        ):
            raise ValueError(
                f"'{fields['document_type']}' is an automation asset type, not "
                "a Knowledge Hub Document Type, and cannot be newly assigned here."
            )

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
        old_source_folder = None
        new_source_folder = None
        managed_file = self._managed_file(
            existing.get("repository_path"), existing.get("repository_relative_path")
        )
        source_file_id = str(existing.get("source_file_id") or "").strip()
        hierarchy_changed = any(
            key in fields for key in
            ("domain", "module", "knowledge_name", "version", "document_type")
        )
        try:
            if managed_file and managed_file.is_file() and source_file_id and hierarchy_changed:
                old_source_folder = managed_file.parent
                new_source_folder = self.repository.central.build_source_path(
                    domain=required["domain"], module=required["module"],
                    knowledge_name=required["knowledge_name"], version=required["version"],
                    document_type=required["document_type"],
                    source_type=existing.get("source_type") or "Files",
                    source_file_id=source_file_id,
                )
                if new_source_folder != old_source_folder:
                    if new_source_folder.exists():
                        raise ValueError("The target managed hierarchy already exists.")
                    new_source_folder.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        old_source_folder.replace(new_source_folder)
                    except FileNotFoundError as error:
                        raise ValueError(
                            "Managed source location could not be resolved. "
                            "The hierarchy was not changed."
                        ) from error
                    moved_file = new_source_folder / managed_file.name
                    fields["repository_path"] = str(moved_file)
                    fields["repository_relative_path"] = self.repository.central.relative(moved_file)
        except Exception:
            store.restore_metadata(vector_snapshot)
            raise

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
            if new_source_folder and new_source_folder.exists() and old_source_folder:
                old_source_folder.parent.mkdir(parents=True, exist_ok=True)
                new_source_folder.replace(old_source_folder)
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

        connection = self.metadata.db.get_connection()
        try:
            dependent_cases = connection.execute(
                "SELECT COUNT(*) FROM test_case_sources WHERE knowledge_source_id=?",
                (knowledge_id,),
            ).fetchone()[0]
            dependent_versions = connection.execute(
                "SELECT COUNT(*) FROM test_case_versions WHERE source_knowledge_id=?",
                (knowledge_id,),
            ).fetchone()[0]
        except Exception:
            dependent_cases = dependent_versions = 0
        finally:
            connection.close()
        if dependent_cases or dependent_versions:
            raise ValueError(
                "This Source File has dependent Test Cases or automation artifacts and cannot be deleted."
            )

        managed_file = self._managed_file(
            item.get("repository_path"), item.get("repository_relative_path")
        )
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

    def _managed_file(self, path, relative_path=""):

        if not path:
            return None

        try:
            resolved = self.repository.central.resolve_managed(path, relative_path)
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
        source_type = {
            "FILE": "Files", "Folder": "Files", "MANUAL_UPLOAD": "Files",
            "SMART_UPLOAD": "Files", "API_COLLECTION": "API Collection",
            "URL_CAPTURE": "URL",
        }.get(source_type, source_type)
        allowed_source_types = {"Files"}
        if source_type not in allowed_source_types:
            raise ValueError("Unsupported document source type.")

        # Rule 1: a brand-new Knowledge Hub item can never be created
        # with a disallowed Document Type — there is no existing row
        # here to be "already legitimate", unlike update_item()'s
        # narrower check above.
        if (document_type or "").strip() in DISALLOWED_NEW_DOCUMENT_TYPES:
            raise ValueError(
                f"'{document_type}' is an automation asset type, not a "
                "Knowledge Hub Document Type, and cannot be used here."
            )

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
