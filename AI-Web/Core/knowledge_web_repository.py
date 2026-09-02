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
from pathlib import Path

from Core.metadata_manager import MetadataManager
from Core.repository_manager import RepositoryManager
from Core.upload_pipeline import UploadPipeline
from Core.vector_store import VectorStore
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
        return self.metadata.create_domain(name)

    def list_modules(self, domain):

        return self.metadata.list_modules(domain)

    def create_module(self, domain, name):
        domain = (domain or "").strip()
        name = (name or "").strip()
        if not domain or not name:
            raise ValueError("Domain and module name are required.")
        return self.metadata.get_or_create_module(domain, name)

    def list_items(self, search=None):

        rows = (
            self.metadata.search(search)
            if search
            else self.metadata.list_all()
        )

        return [_row_to_dict(row) for row in rows]

    def get_item(self, knowledge_id):

        return _row_to_dict(self.metadata.get(knowledge_id))

    def get_item_versions(self, knowledge_id):

        rows = self.metadata.get_versions(knowledge_id)

        return [
            {
                "version": row[0],
                "sha256": row[1],
                "repository_path": row[2],
                "created_date": row[3],
            }
            for row in rows
        ]

    # --------------------------------------------------
    # Edit
    # --------------------------------------------------

    def update_item(self, knowledge_id, **fields):

        # Tags arrive from the web as a list; the column stores JSON
        # text, same encoding save_knowledge_item() already uses.
        if "tags" in fields and isinstance(fields["tags"], list):

            fields["tags"] = json.dumps(fields["tags"])

        self.metadata.update_knowledge_item(knowledge_id, **fields)

        return self.get_item(knowledge_id)

    # --------------------------------------------------
    # Delete (one version, not the whole knowledge name)
    # --------------------------------------------------

    def delete_item(self, knowledge_id):

        item = self.get_item(knowledge_id)

        if item is None:

            return None

        version_folder = (
            self.repository.repository_root
            / item["domain"]
            / item["module"]
            / item["knowledge_name"]
            / item["version"]
        )

        files_removed = 0

        if version_folder.exists():

            files_removed = sum(
                1 for f in version_folder.rglob("*") if f.is_file()
            )

            shutil.rmtree(version_folder, ignore_errors=True)

        vectors_removed = self._delete_vectors_for_version(
            item["domain"], item["module"], item["knowledge_name"], item["version"]
        )

        db_summary = self.metadata.delete(knowledge_id)

        return {
            "item": item,
            "files_removed": files_removed,
            "vectors_removed": vectors_removed,
            **db_summary,
        }

    def _delete_vectors_for_version(self, domain, module, knowledge_name, version):

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
    ):

        domain = (domain or "").strip()
        module = (module or "").strip()
        knowledge_name = (knowledge_name or "").strip()
        version = (version or "1.0").strip() or "1.0"

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

        # A plain NamedTemporaryFile gets a random generated basename
        # (e.g. "tmpXXXXXX.txt") — RepositoryManager.save_file() names
        # the file it copies into the repo after whatever path it's
        # given, so that random name would end up as the permanently
        # stored file_name/repository_path. Using a temp DIRECTORY and
        # writing the file under its REAL original name keeps the
        # repo folder and the metadata grid showing the name the user
        # actually uploaded.
        safe_name = Path(original_filename or "upload").name or "upload"

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

        changes = VersionCompare().compare(row_a, row_b)

        return {
            "a": self.get_item(knowledge_id_a),
            "b": self.get_item(knowledge_id_b),
            "changes": changes,
        }
