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
from datetime import datetime
from pathlib import Path

from Core.metadata_manager import MetadataManager
from Core.repository_manager import RepositoryManager
from Core.knowledge_merge import KnowledgeMerge
from Core.version_compare import VersionCompare
from Core.smart_upload import SmartUpload
from Shared.hierarchy_values import canonical_source_type


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

    def _hierarchy_administration(self):
        from Shared.hierarchy_administration import HierarchyAdministration
        from Core.vector_store import VectorStore
        from Core.retrieval_cache import RetrievalCache
        return HierarchyAdministration(self.metadata.db,self.repository.central,VectorStore(),self._vector_ids_for_item,RetrievalCache().clear)

    # --------------------------------------------------
    # Browse
    # --------------------------------------------------

    def get_tree(self):

        return self.metadata.get_tree()

    def hierarchy_catalog(self, **filters):
        from Shared.document_hierarchy import DocumentHierarchy
        conn=self.metadata.db.get_connection()
        try:
            return DocumentHierarchy(conn).catalog(**filters)
        finally: conn.close()

    def create_hierarchy_node(self, kind, context, name):
        from Shared.document_hierarchy import DocumentHierarchy
        conn=self.metadata.db.get_connection()
        try:
            hierarchy=DocumentHierarchy(conn);hierarchy.ensure_schema()
            result=hierarchy.create_node(kind,context,name)
            conn.commit();return result
        except Exception:
            conn.rollback();raise
        finally: conn.close()

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

    # --------------------------------------------------
    # QA-AI-STUDIO-IMPACT-ANALYSIS-V1
    #
    # Lightweight, join-free reads for Impact Analysis's Source Document
    # cascade (Domain -> Module already covered by list_domains()/
    # list_modules() above; these two cover Knowledge Name -> Version/
    # Document Type/Source Document). Deliberately NOT list_items()
    # below, which additionally joins test-case folders, Playwright
    # artifacts, API collection endpoints, and discovery flows for every
    # item in the whole repository -- far more than a dropdown needs.
    # Same MetadataManager/knowledge_items table every other hierarchy
    # call already uses; no second hierarchy implementation.
    # --------------------------------------------------

    def list_knowledge_names(self, domain, module):

        return self.metadata.get_knowledge_names(domain, module)

    def list_items_for_knowledge(self, domain, module, knowledge_name):

        return self.metadata.list_items_for_knowledge(domain, module, knowledge_name)

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
                    folder["playwright_artifacts"] = []
                    test_cases = connection.execute(
                        "SELECT tc_id FROM test_cases WHERE test_case_version_id=? AND tc_id<>''",
                        (folder["id"],),
                    ).fetchall()
                    from Shared.central_repository import CentralRepository
                    central = CentralRepository()
                    automation_folder = central.build_automation_path(
                        folder.get("folder_path"), "Playwright",
                        folder.get("version_number"), folder.get("source_file_id"),
                    )
                    for test_case in test_cases:
                        tc_id = test_case["tc_id"]
                        asset = connection.execute(
                            "SELECT uploaded_script,modified_date FROM playwright_automation_assets "
                            "WHERE source_knowledge_id=? AND tc_id=?",
                            (item["id"], tc_id),
                        ).fetchone()
                        if not asset or asset.get("uploaded_script") is None:
                            continue
                        artifact_path = automation_folder / (
                            central.sanitize_segment(tc_id, "Test Case ID") + ".uploaded.json"
                        )
                        if not artifact_path.is_file():
                            continue
                        try:
                            artifact_managed_path = central.relative(artifact_path)
                        except ValueError:
                            artifact_managed_path = str(artifact_path)
                        folder["playwright_artifacts"].append({
                            "file_name": artifact_path.name,
                            "artifact_type": "Playwright Uploaded Script",
                            "tc_id": tc_id,
                            "managed_path": artifact_managed_path,
                            "physical_path": str(artifact_path),
                            "available": True,
                            "created_date": datetime.fromtimestamp(
                                artifact_path.stat().st_ctime
                            ).isoformat(),
                            "modified_date": datetime.fromtimestamp(
                                artifact_path.stat().st_mtime
                            ).isoformat(),
                        })
                    folder["automation_folders"] = []

                    def add_automation_artifact(kind, artifact_path, artifact_type, tc_id=None,
                                                record_id=None, record_table=None):
                        path = Path(str(artifact_path or ""))
                        if not path.is_file():
                            return
                        parent = next((entry for entry in folder["automation_folders"]
                                       if entry["folder_path"] == str(path.parent)), None)
                        if parent is None:
                            parent = {
                                "kind": kind, "folder_name": path.parent.name,
                                "folder_path": str(path.parent), "artifacts": [],
                            }
                            folder["automation_folders"].append(parent)
                        if any(entry["physical_path"] == str(path) for entry in parent["artifacts"]):
                            return
                        stat = path.stat()
                        try:
                            managed_path = central.relative(path)
                        except ValueError:
                            managed_path = str(path)
                        parent["artifacts"].append({
                            "file_name": path.name, "artifact_type": artifact_type,
                            "tc_id": tc_id, "managed_path": managed_path,
                            "record_id": record_id, "record_table": record_table,
                            "physical_path": str(path), "available": True,
                            "created_date": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                            "modified_date": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        })

                    for artifact in folder["playwright_artifacts"]:
                        add_automation_artifact(
                            "Playwright", artifact["physical_path"],
                            artifact["artifact_type"], artifact.get("tc_id"),
                        )
                    for api in connection.execute(
                        "SELECT id,repository_path,name FROM api_collections "
                        "WHERE test_case_version_id=? AND repository_path IS NOT NULL",
                        (folder["id"],),
                    ).fetchall():
                        add_automation_artifact("API", api["repository_path"], "API Collection",
                                                record_id=api["id"], record_table="api_collections")
                    for sql_asset in connection.execute(
                        "SELECT id,repository_path,display_name FROM sql_scripts "
                        "WHERE test_case_version_id=? AND repository_path IS NOT NULL",
                        (folder["id"],),
                    ).fetchall():
                        add_automation_artifact("SQL", sql_asset["repository_path"], "Uploaded SQL Script",
                                                record_id=sql_asset["id"], record_table="sql_scripts")
                    for sql_case in connection.execute(
                        "SELECT tc_id,sql_script_path FROM test_cases "
                        "WHERE test_case_version_id=? AND sql_script_path<>''",
                        (folder["id"],),
                    ).fetchall():
                        add_automation_artifact(
                            "SQL", sql_case["sql_script_path"],
                            "Test Case SQL Script", sql_case["tc_id"],
                            record_id=sql_case["tc_id"], record_table="test_cases",
                        )
                    item["test_case_folders"].append(folder)
        except Exception as error:
            # Databases predating QA Engineering versions still remain browsable.
            for item in items:
                item.setdefault("test_case_folders", [])
                item["hierarchy_diagnostic"] = str(error)
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
                and item.get("document_type") == selected.get("document_type")
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
        item["embedding_status"] = item.get("status") or "UNKNOWN"
        if item.get("repository_path"):
            try:
                managed_file = self.repository.central.resolve_managed(
                    item.get("repository_path"), item.get("repository_relative_path")
                )
                item["physical_file_exists"] = managed_file.is_file()
            except (OSError, ValueError):
                item["physical_file_exists"] = False
        else:
            item["physical_file_exists"] = False

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
            if str(metadata.get('knowledge_id') or '')==str(item['id']):
                ids.append(str(doc_id));continue
            if metadata.get('knowledge_id') not in (None,''):continue
            if (
                metadata.get("domain") == item.get("domain")
                and metadata.get("module") == item.get("module")
                and metadata.get("knowledge_name") == item.get("knowledge_name")
                and metadata.get("document_type") == item.get("document_type")
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
        """Use the same atomic hierarchy service as managed-node edits."""
        from Core.vector_store import VectorStore
        from Core.retrieval_cache import RetrievalCache

        existing = self.get_item(knowledge_id)
        if existing is None:
            raise ValueError("Knowledge item not found.")
        hierarchy_fields = ("domain", "module", "knowledge_name", "document_type", "version")
        required = {key: str(fields.get(key, existing.get(key)) or "").strip() for key in hierarchy_fields}
        if not all(required.values()):
            raise ValueError("Complete Domain, Module, Knowledge, Document Type and Version are required.")
        if required["document_type"] in DISALLOWED_NEW_DOCUMENT_TYPES and required["document_type"] != existing.get("document_type"):
            raise ValueError("Automation asset types cannot be newly assigned as Knowledge Document Types.")
        extra = {key: fields[key] for key in ("summary", "tags") if key in fields}
        changed = any(required[key] != str(existing.get(key) or "") for key in hierarchy_fields)
        if changed:
            source_file_id = existing.get("source_file_id")
            if not source_file_id:
                raise ValueError("Legacy source identity is unresolved. Adopt its managed location before changing its hierarchy.")
            context = {key: existing.get(key) for key in hierarchy_fields}
            context.update(source_type=existing.get("source_type") or "Files", source_file_id=source_file_id)
            destination = dict(required, source_type=context["source_type"])
            self._hierarchy_administration().change(
                {"type": "source_file", "name": source_file_id, "context": context},
                destination=destination, item_updates={int(knowledge_id): extra},
            )
        elif extra:
            store = VectorStore()
            ids = self._vector_ids_for_item(existing)
            snapshot = store.snapshot(ids)
            try:
                vector_values = {key: ", ".join(value) if key == "tags" and isinstance(value, list) else value for key, value in extra.items()}
                store.update_metadata(ids, vector_values)
                stored = {key: json.dumps(value) if key == "tags" and isinstance(value, list) else value for key, value in extra.items()}
                if not self.metadata.update_knowledge_item(knowledge_id, **stored):
                    raise ValueError("Knowledge item no longer exists.")
            except Exception:
                store.restore_metadata(snapshot)
                raise
            RetrievalCache().clear()
        result = self.get_item_details(knowledge_id)
        result["storage_strategy"] = "canonical_managed_path"
        return result

    # --------------------------------------------------
    # Delete (one version, not the whole knowledge name)
    # --------------------------------------------------

    def delete_item(self, knowledge_id):
        from Core.vector_store import VectorStore
        from Core.retrieval_cache import RetrievalCache
        from Shared.source_administration import delete_source

        item = self.get_item(knowledge_id)
        if item is None:
            return None
        result = delete_source(self.metadata.db, self.repository.central, VectorStore(), int(knowledge_id))
        RetrievalCache().clear()
        return {"item": item, **result}

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

        data = store.collection.get(include=['metadatas'])

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
    # Manage Knowledge node administration
    # --------------------------------------------------

    _ADMIN_LEVEL_FIELDS = {
        "domain": "domain", "module": "module", "knowledge": "knowledge_name",
        "document_type": "document_type", "version": "version",
        "source_type": "source_type", "source_file": "source_file_id",
    }

    def _admin_items(self, node):
        """Resolve a UI node by stable item identity plus its ancestor scope."""
        items = self.list_items()
        context = node.get("context") or {}
        for key in ("domain", "module", "knowledge_name", "version", "document_type",
                    "source_type", "source_file_id"):
            if context.get(key) not in (None, ""):
                items = [row for row in items if (canonical_source_type(row.get(key))==canonical_source_type(context[key]) if key=='source_type' else str(row.get(key) or "") == str(context[key]))]
        if node.get("knowledge_id") is not None:
            items = [row for row in items if int(row["id"]) == int(node["knowledge_id"])]
        return items

    def _admin_path(self, path):
        candidate = Path(str(path or ""))
        if not path:
            return None
        resolved = candidate.resolve()
        try:
            resolved.relative_to(self.repository.repository_root.resolve())
        except ValueError as error:
            raise ValueError("The managed path is outside the Central Repository.") from error
        return resolved

    def _validated_automation_path(self, node):
        path = self._admin_path(node.get("physical_path"))
        connection = self.metadata.db.get_connection()
        try:
            version = connection.execute(
                "SELECT folder_path,source_knowledge_id FROM test_case_versions WHERE id=?",
                (node.get("test_case_version_id") or node.get("record_id"),),
            ).fetchone()
            if not version:
                raise ValueError("The owning Test Cases folder was not found.")
            owner = Path(version[0]).resolve()
            if owner != path and owner not in path.parents:
                raise ValueError("The automation path does not belong to the selected Test Cases folder.")
            if node.get("type") == "automation_file":
                table, record_id = node.get("record_table"), node.get("record_id")
                stored = None
                if table == "api_collections":
                    stored = connection.execute("SELECT repository_path FROM api_collections WHERE id=? AND test_case_version_id=?", (record_id, node.get("test_case_version_id"))).fetchone()
                elif table == "sql_scripts":
                    stored = connection.execute("SELECT repository_path FROM sql_scripts WHERE id=? AND test_case_version_id=?", (record_id, node.get("test_case_version_id"))).fetchone()
                elif table == "test_cases":
                    stored = connection.execute("SELECT sql_script_path FROM test_cases WHERE tc_id=? AND test_case_version_id=?", (record_id, node.get("test_case_version_id"))).fetchone()
                elif node.get("automation_type") == "Playwright":
                    stored = connection.execute("SELECT 1 FROM playwright_automation_assets WHERE source_knowledge_id=? AND tc_id=?", (node.get("knowledge_id"), node.get("tc_id"))).fetchone()
                    if stored and path.suffix.lower() != ".json": stored = None
                if not stored or (table and Path(stored[0]).resolve() != path):
                    raise ValueError("The automation file identity does not match persisted ownership.")
            return path
        finally:
            connection.close()

    def get_admin_node_details(self, node):
        node_type = str(node.get("type") or "").strip()
        items = self._admin_items(node)
        context = node.get("context") or {}
        details = {
            "node_type": node_type, "display_name": node.get("name") or "",
            "hierarchy": context, "knowledge_source_count": len(items),
            "editable": True,
        }
        if node_type in self._ADMIN_LEVEL_FIELDS:
            field = self._ADMIN_LEVEL_FIELDS[node_type]
            details["display_name"] = context.get(field) or node.get("name") or ""
            next_fields = list(self._ADMIN_LEVEL_FIELDS.values())
            start = next_fields.index(field) + 1
            details["descendant_counts"] = {
                descendant: len({str(row.get(descendant) or "") for row in items})
                for descendant in next_fields[start:]
            }
            if items:
                dates = [row.get("modified_date") or row.get("created_date") for row in items]
                details["created_date"] = min((row.get("created_date") for row in items if row.get("created_date")), default="")
                details["modified_date"] = max((value for value in dates if value), default="")
        elif node_type == "source_document":
            item = self.get_item_details(node.get("knowledge_id"))
            if item is None:
                raise ValueError("Source Document was not found.")
            details.update(item)
            details["display_name"] = item.get("file_name")
        elif node_type == "tc_folder":
            folder = next((folder for row in items for folder in row.get("test_case_folders", [])
                           if int(folder["id"]) == int(node.get("record_id"))), None)
            if not folder:
                raise ValueError("Test Cases folder was not found.")
            details.update({
                "record_id": folder["id"], "display_name": folder.get("name"),
                "physical_path": folder.get("folder_path"),
                "physical_exists": Path(folder.get("folder_path") or "").is_dir(),
                "test_cases_xlsx_count": len(folder.get("files") or []),
                "automation_folder_count": len(folder.get("automation_folders") or []),
                "automation_file_count": sum(len(group.get("artifacts") or []) for group in folder.get("automation_folders") or []),
            })
        elif node_type == "xlsx":
            connection = self.metadata.db.get_connection()
            connection.row_factory = lambda cursor, row: {
                column[0]: row[index] for index, column in enumerate(cursor.description)
            }
            try:
                row = connection.execute(
                    "SELECT f.*,v.folder_path,v.source_knowledge_id,v.version_number "
                    "FROM test_case_files f JOIN test_case_versions v ON v.id=f.test_case_version_id WHERE f.id=?",
                    (node.get("record_id"),),
                ).fetchone()
                if not row:
                    raise ValueError("Test Cases XLSX was not found.")
                row = dict(row)
                count_row = connection.execute(
                    "SELECT COUNT(*) AS count FROM test_cases WHERE test_case_version_id=?", (row["test_case_version_id"],)
                ).fetchone()
                row["test_case_count"] = count_row["count"]
                row["physical_exists"] = Path(row.get("workbook_path") or "").is_file()
                details.update(row); details["display_name"] = row.get("file_name")
            finally:
                connection.close()
        elif node_type in {"automation_folder", "automation_file"}:
            path = self._validated_automation_path(node)
            if path is None:
                raise ValueError("Automation path is unavailable.")
            details.update({
                "display_name": path.name, "automation_type": node.get("automation_type"),
                "physical_path": str(path), "physical_exists": path.exists(),
                "record_id": node.get("record_id"), "record_table": node.get("record_table"),
                "associated_test_case_id": node.get("tc_id"),
            })
            if node_type == "automation_folder":
                details["artifact_count"] = len([child for child in path.iterdir() if child.is_file()]) if path.is_dir() else 0
            elif path.is_file():
                stat = path.stat()
                details.update({"file_size": stat.st_size,
                                "created_date": datetime.fromtimestamp(stat.st_ctime).isoformat(),
                                "modified_date": datetime.fromtimestamp(stat.st_mtime).isoformat()})
        else:
            raise ValueError("Unsupported Manage Knowledge node type.")
        return details

    def update_admin_node(self, node, value):
        node_type = str(node.get("type") or "")
        value = str(value or "").strip()
        if not value:
            raise ValueError("A non-empty value is required.")
        if node_type in self._ADMIN_LEVEL_FIELDS:
            return self._hierarchy_administration().change(node,value=value)
        if node_type == "source_document":
            item = self.get_item(node.get("knowledge_id"))
            path = self._managed_file(item.get("repository_path"), item.get("repository_relative_path")) if item else None
            if path is None or not path.is_file():
                raise ValueError("Managed source location could not be resolved. The source was not renamed.")
            target = path.with_name(value)
            if target.exists() and target != path:
                raise ValueError("The target filename already exists.")
            from Core.vector_store import VectorStore
            store = VectorStore()
            vector_snapshot = store.update_metadata(self._vector_ids_for_item(item), {"file_name": value})
            try:
                path.replace(target)
                result = self.metadata.update_knowledge_item(
                    item["id"], file_name=value, repository_path=str(target),
                    repository_relative_path=self.repository.central.relative(target),
                )
                if not result:
                    raise RuntimeError("Source metadata was not updated.")
            except Exception:
                store.restore_metadata(vector_snapshot)
                if target.exists(): target.replace(path)
                raise
            return {"updated": 1, "physical_path": str(target)}
        if node_type == "xlsx":
            connection = self.metadata.db.get_connection()
            stored = connection.execute("SELECT workbook_path FROM test_case_files WHERE id=? AND test_case_version_id=?", (node.get("record_id"), node.get("test_case_version_id"))).fetchone()
            connection.close()
            path = self._admin_path(stored[0]) if stored else None
            if path is None or not path.is_file():
                raise ValueError("Test Cases XLSX was not found.")
            target = path.with_name(value)
            if target.exists() and target != path: raise ValueError("The target filename already exists.")
            path.replace(target)
            connection = self.metadata.db.get_connection()
            try:
                connection.execute("UPDATE test_case_files SET file_name=?,workbook_path=? WHERE id=?", (target.name, str(target), node.get("record_id")))
                connection.execute("UPDATE test_case_versions SET workbook_path=? WHERE id=? AND workbook_path=?", (str(target), node.get("test_case_version_id"), str(path)))
                connection.commit()
            except Exception:
                connection.rollback(); target.replace(path); raise
            finally: connection.close()
            return {"updated": 1, "physical_path": str(target)}
        if node_type in {"tc_folder", "automation_folder"}:
            if node_type == "automation_folder":
                path = self._validated_automation_path(node)
            else:
                connection = self.metadata.db.get_connection()
                stored = connection.execute("SELECT folder_path FROM test_case_versions WHERE id=?", (node.get("record_id"),)).fetchone()
                connection.close()
                path = self._admin_path(stored[0]) if stored else None
            if path is None or not path.is_dir(): raise ValueError("Managed folder was not found.")
            target = path.with_name(value)
            if target.exists() and target != path: raise ValueError("The target folder already exists.")
            path.replace(target)
            try:
                self._replace_descendant_paths(path, target)
            except Exception:
                target.replace(path); raise
            return {"updated": 1, "physical_path": str(target)}
        if node_type == "automation_file":
            path = self._validated_automation_path(node)
            if path is None or not path.is_file():
                raise ValueError("Automation file was not found.")
            target = path.with_name(value)
            if target.exists() and target != path:
                raise ValueError("The target filename already exists.")
            path.replace(target)
            try:
                self._update_artifact_path(node, target)
            except Exception:
                target.replace(path); raise
            return {"updated": 1, "physical_path": str(target)}
        raise ValueError("This node has no safe editable property.")

    def _prune_unused_domain_module_masters(self, node_type, old_value, domain_context=None):
        """After a rename/move away from `old_value` at the domain or
        module level, remove the now-unused old master row(s) bottom-up.
        update_item() already resolves/creates whatever new master rows
        are needed for the new value; this only cleans up ones nothing
        references any more. Shared by update_admin_node (rename) and
        move_admin_node (V5 — move to an existing target)."""
        if node_type == "domain":
            connection = self.metadata.db.get_connection()
            try:
                old_domain = connection.execute("SELECT id FROM domains WHERE name=?", (old_value,)).fetchone()
                if old_domain:
                    connection.execute("DELETE FROM modules WHERE domain_id=? AND NOT EXISTS (SELECT 1 FROM knowledge_items k WHERE k.module_id=modules.id)", (old_domain[0],))
                    connection.execute("DELETE FROM domains WHERE id=? AND NOT EXISTS (SELECT 1 FROM modules WHERE domain_id=?) AND NOT EXISTS (SELECT 1 FROM knowledge_items WHERE domain_id=?)", (old_domain[0], old_domain[0], old_domain[0]))
                connection.commit()
            except Exception:
                connection.rollback(); raise
            finally: connection.close()
        elif node_type == "module":
            connection = self.metadata.db.get_connection()
            try:
                connection.execute("DELETE FROM modules WHERE name=? AND domain_id=(SELECT id FROM domains WHERE name=?) AND NOT EXISTS (SELECT 1 FROM knowledge_items k WHERE k.module_id=modules.id)", (old_value, domain_context))
                connection.commit()
            except Exception:
                connection.rollback(); raise
            finally: connection.close()

    # ================= V5: Manage Knowledge Hierarchy MOVE =================
    # Section 2/6 of the V5 correction: Edit at a chain level (Domain,
    # Module, Knowledge, Version, Document Type, Source Type, Source File)
    # is a MOVE/REASSIGN of the complete existing subtree onto an existing
    # target at the SAME level — never a free-text rename, never an
    # individual per-descendant edit. This intentionally does NOT touch
    # update_admin_node (still used for the true leaf-level single-field
    # renames — automation_file, and anything else with no children) —
    # Section 21: "Do not mix rename with move."
    #
    # Reuses update_item() (already transactional/rollback-safe per item:
    # vector-metadata snapshot/restore, managed-folder move with its own
    # existing "target already exists" guard, DB update, and
    # _replace_descendant_paths() for every persisted descendant path
    # table) as the per-item primitive — Section 20's reuse principle
    # from V4 still applies. What's new here is: (a) a real, mutation-free
    # collision pre-check across every affected item's physical
    # destination before anything is touched, (b) batch orchestration with
    # compensating rollback if a later item fails after earlier ones
    # already succeeded, and (c) safe cleanup of the now-empty source
    # container only after the full move succeeds.
    #
    # Honesty note (see the V5 report's Section on transaction strategy):
    # this is a best-effort software transaction across N independent
    # DB+filesystem operations, not a single native database transaction
    # — the underlying architecture (per-item managed folders on a real
    # filesystem, one SQLite row per item) has no such primitive to wrap
    # this in. Compensation is real (each succeeded item is reversed with
    # its own original values via the same update_item() call) but is not
    # literally atomic the way a single SQL transaction is.
    #
    # ---- BUG FIX (post-live-test correction): the selected SOURCE ROOT
    # itself must move, not just its descendants. The original V5 release
    # let the caller send a `target_node` at the SAME level as the source
    # and used *its* own-level name for every affected row — that is MERGE
    # semantics (source's descendants get relabeled onto an existing
    # same-level sibling), not MOVE. Move re-parents the selected node
    # while preserving its own identity: the own-level field always comes
    # from `source_node.get("name")`, never from the target, and the
    # target now only needs to describe the PARENT context to move under
    # (Section 4/9 of the correction) — never a same-level sibling to
    # "merge" into. Domain has no parent level in this schema (there is no
    # tenant/company level above Domain), so faking a Domain move by only
    # relocating its Modules would repeat the exact bug this fixes —
    # Domain is therefore deliberately EXCLUDED from Move eligibility this
    # release (Section 8A: "do not fake a move"); its Pencil renders
    # disabled with the same tooltip as the other not-yet-eligible levels.
    _MOVE_ELIGIBLE_TYPES = ("module", "knowledge", "version", "document_type", "source_type", "source_file")

    def move_admin_node(self, source_node, target_node, validate_only=False):
        """Reparent the complete canonical subtree without replacing its IDs."""
        return self._hierarchy_administration().change(source_node,destination=target_node.get('context') or {},validate_only=validate_only)

    def _update_artifact_path(self, node, target):
        table = node.get("record_table")
        record_id = node.get("record_id")
        connection = self.metadata.db.get_connection()
        try:
            if table == "api_collections":
                connection.execute("UPDATE api_collections SET repository_path=?,modified_date=? WHERE id=?",
                                   (str(target), datetime.now().isoformat(), record_id))
            elif table == "sql_scripts":
                connection.execute("UPDATE sql_scripts SET repository_path=?,original_filename=?,modified_date=? WHERE id=?",
                                   (str(target), target.name, datetime.now().isoformat(), record_id))
            elif table == "test_cases":
                connection.execute("UPDATE test_cases SET sql_script_path=? WHERE tc_id=? AND test_case_version_id=?",
                                   (str(target), record_id, node.get("test_case_version_id")))
            connection.commit()
        except Exception:
            connection.rollback(); raise
        finally:
            connection.close()

    _ADMIN_LEVEL_LABELS = {
        "domain": "Domain", "module": "Module", "knowledge": "Knowledge",
        "version": "Version", "document_type": "Document Type",
        "source_type": "Source Type", "source_file": "Source File",
        "source_file_id": "Source File",
    }

    def _admin_delete_blocked_message(self, node, node_type, items):
        """Section 8 (Manage Knowledge Full Hierarchy Administration): a
        blocked delete must name the REAL existing child hierarchy, not a
        generic "descendants still exist". Lists the actual downstream
        values found in `items` (already scoped to this exact node by
        _admin_items), falling back to a real count for the deeper levels
        (Source Document / TC Folder / XLSX / automation) that aren't
        plain string columns on knowledge_items.
        """
        field = self._ADMIN_LEVEL_FIELDS[node_type]
        next_fields = list(self._ADMIN_LEVEL_FIELDS.values())
        start = next_fields.index(field) + 1
        lines = []
        for descendant in next_fields[start:]:
            values = sorted({str(row.get(descendant) or "") for row in items if row.get(descendant)})
            if values:
                label = self._ADMIN_LEVEL_LABELS.get(descendant, descendant.replace("_", " ").title())
                shown = ", ".join(values[:5])
                if len(values) > 5:
                    shown += f" (+{len(values) - 5} more)"
                lines.append(f"{label}: {shown}")
        if not lines:
            # No deeper _ADMIN_LEVEL_FIELDS column differs (this is the
            # Source File level, or every descendant happens to share the
            # same value) — the real blockers are Source Documents / Test
            # Case Folders / XLSX / automation assets, which aren't plain
            # string columns here. Name them by real count instead of
            # inventing field-level detail that doesn't exist.
            source_count = len({str(row.get("id")) for row in items})
            tc_folder_count = len({
                folder["id"] for row in items for folder in (row.get("test_case_folders") or [])
            })
            lines.append(
                f"Source Document(s): {source_count}"
                + (f" · Test Case Folder(s): {tc_folder_count}" if tc_folder_count else "")
            )
        display_name = node.get("name") or (node.get("context") or {}).get(field) or ""
        label = self._ADMIN_LEVEL_LABELS.get(node_type, node_type.replace("_", " ").title())
        return (
            f'Cannot delete {label} "{display_name}". Existing child hierarchy:\n'
            + "\n".join(lines)
            + "\nDelete the child items first."
        )

    def delete_admin_node(self, node):
        node_type = str(node.get("type") or "")
        if node_type in ('domain','module','knowledge','document_type','version'):
            return self._hierarchy_administration().delete_empty(node)
        if node_type == "domain":
            self.delete_domain((node.get("context") or {}).get("domain")); return {"deleted": 1}
        if node_type == "module":
            context = node.get("context") or {}
            self.delete_module(context.get("domain"), context.get("module")); return {"deleted": 1}
        if node_type in self._ADMIN_LEVEL_FIELDS:
            items = self._admin_items(node)
            if items:
                raise ValueError(self._admin_delete_blocked_message(node, node_type, items))
            raise ValueError("Manage Knowledge node was not found.")
        if node_type == "source_document":
            result = self.delete_item(int(node.get("knowledge_id")))
            if result is None: raise ValueError("Source Document was not found.")
            return result
        if node_type == "tc_folder":
            connection = self.metadata.db.get_connection()
            try:
                folder_id = int(node.get("record_id"))
                xlsx = connection.execute("SELECT COUNT(*) FROM test_case_files WHERE test_case_version_id=?", (folder_id,)).fetchone()[0]
                cases = connection.execute("SELECT COUNT(*) FROM test_cases WHERE test_case_version_id=?", (folder_id,)).fetchone()[0]
                api = connection.execute("SELECT COUNT(*) FROM api_collections WHERE test_case_version_id=?", (folder_id,)).fetchone()[0]
                sql = connection.execute("SELECT COUNT(*) FROM sql_scripts WHERE test_case_version_id=?", (folder_id,)).fetchone()[0]
                folder = connection.execute("SELECT folder_path FROM test_case_versions WHERE id=?", (folder_id,)).fetchone()
                path = Path(folder[0]) if folder else None
                physical = len(list(path.iterdir())) if path and path.is_dir() else 0
                if xlsx or cases or api or sql or physical:
                    raise ValueError("Cannot delete TC Folder because Test Cases XLSX and automation assets still exist.")
                connection.execute("DELETE FROM test_case_versions WHERE id=?", (folder_id,)); connection.commit()
                if path and path.is_dir(): path.rmdir()
                return {"deleted": 1}
            except Exception:
                connection.rollback(); raise
            finally: connection.close()
        if node_type == "xlsx":
            connection = self.metadata.db.get_connection(); staged = None
            connection.row_factory = lambda cursor, row: {
                column[0]: row[index] for index, column in enumerate(cursor.description)
            }
            try:
                row = connection.execute("SELECT * FROM test_case_files WHERE id=?", (node.get("record_id"),)).fetchone()
                if not row: raise ValueError("Test Cases XLSX was not found.")
                count_row = connection.execute("SELECT COUNT(*) AS count FROM test_cases WHERE test_case_version_id=?", (row["test_case_version_id"],)).fetchone()
                if count_row["count"]:
                    raise ValueError("Cannot delete Test Cases XLSX because Test Cases still depend on its workbook version.")
                path = Path(row["workbook_path"])
                if path.is_file(): staged = path.with_name(f".{path.name}.deleting-{uuid.uuid4().hex}"); path.replace(staged)
                connection.execute("DELETE FROM test_case_files WHERE id=?", (row["id"],)); connection.commit()
                if staged: staged.unlink()
                return {"deleted": 1}
            except Exception:
                connection.rollback()
                if staged and staged.exists(): staged.replace(Path(row["workbook_path"]))
                raise
            finally: connection.close()
        if node_type == "automation_folder":
            path = self._validated_automation_path(node)
            if path and path.is_dir() and any(path.iterdir()):
                raise ValueError(f"Cannot delete {node.get('automation_type')} folder because automation files still exist.")
            if path and path.is_dir(): path.rmdir()
            return {"deleted": 1}
        if node_type == "automation_file":
            return self._delete_automation_file(node)
        raise ValueError("Unsupported Manage Knowledge node type.")

    def _delete_automation_file(self, node):
        path = self._validated_automation_path(node)
        if path is None or not path.is_file(): raise ValueError("Automation file was not found.")
        table, record_id = node.get("record_table"), node.get("record_id")
        connection = self.metadata.db.get_connection(); staged = path.with_name(f".{path.name}.deleting-{uuid.uuid4().hex}")
        path.replace(staged)
        try:
            if table == "api_collections": connection.execute("DELETE FROM api_collections WHERE id=?", (record_id,))
            elif table == "sql_scripts": connection.execute("DELETE FROM sql_scripts WHERE id=?", (record_id,))
            elif table == "test_cases": connection.execute("UPDATE test_cases SET sql_script_path=NULL WHERE tc_id=? AND test_case_version_id=?", (record_id, node.get("test_case_version_id")))
            elif node.get("automation_type") == "Playwright":
                refs = connection.execute("SELECT COUNT(DISTINCT test_case_version_id) FROM test_cases WHERE tc_id=? AND automation_type='Playwright'", (node.get("tc_id"),)).fetchone()[0]
                if refs > 1: raise ValueError("Cannot delete Playwright file because stable ownership is shared by another workbook revision.")
                connection.execute("DELETE FROM playwright_automation_assets WHERE source_knowledge_id=? AND tc_id=?", (node.get("knowledge_id"), node.get("tc_id")))
            connection.commit(); staged.unlink(); return {"deleted": 1}
        except Exception:
            connection.rollback()
            if staged.exists(): staged.replace(path)
            raise
        finally: connection.close()

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
