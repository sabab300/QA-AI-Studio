# Create: AI/Core/api_collection_repository.py

"""
QA AI Studio
API Collection Repository

Version: 2.0

Persists an imported Postman Collection (.json export) so QA
Automation's "API" automation type can generate scripts from real,
uploaded endpoint/method/header/body/auth data instead of the AI
guessing all of that from a test case's plain-English text — the
same "ground it in something real" fix URL Knowledge applied to
Playwright automation, applied here to API automation.

Postman Collection format (v2.0/v2.1, the two schemas actually seen
in the wild): a JSON document with an "info" block, an "item" array
(each entry is EITHER a request OR a folder containing more items —
arbitrarily nested), and an optional collection-level "variable"
array (e.g. baseUrl, auth tokens) referenced elsewhere as
"{{variableName}}". This module flattens that nested structure into
one row per actual request (api_endpoints), keeping each request's
folder path as a breadcrumb for context, and resolves "{{variable}}"
references in URLs on a best-effort basis so generated scripts show
a real, usable URL rather than a literal "{{baseUrl}}" placeholder.

QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4 Rule 5:
an API Collection is an AUTOMATION ASSET bound to a Test Cases (TC-*)
folder, never a Knowledge Hub source type/folder. Two storage paths
exist here side by side, on purpose:

  - NEW path (`test_case_version_id` given): the collection is filed
    under the selected TC-* folder's own "API-TC-*" automation
    subfolder, using the ORIGINAL uploaded .json byte-for-byte (no
    rewriting). This is the corrected model and the one any new
    upload UI should use.
  - LEGACY path (`test_case_version_id` omitted, `domain`/`module`/
    `knowledge_name` given instead): preserved ONLY for the existing
    call sites that predate this fix (`Web/routers/knowledge_router.py`'s
    `/api-collections/import` and `Core/automation_web_repository.py`'s
    `ApiCollectionsWeb.import_collection()`), so they keep working
    exactly as before while those endpoints are relocated to API
    Automation under Rule 5's UI plan. This path deliberately does
    NOT create any physical folder under Knowledge Hub/Central
    Repository (an earlier pass, QA-AI-STUDIO-LIVE-CORRECTION-PASS-V2
    item 10, added a "ST-API Collection" managed folder here — that
    direction is explicitly wrong per Rule 5 and was reverted before
    ever reaching a live backend restart). It still records a
    knowledge_items bridge row (same as before this project's V2
    pass) purely so legacy callers/existing Manage Knowledge rendering
    keep working; it is not filed as a real Source Type in the
    Domain -> ... -> Source Document physical hierarchy.

`migrate_unresolved_legacy_collections()` is the safe startup audit
that gives every existing api_collections row an honest
`association_status`: 'linked' only when it can be bound to a TC-*
folder with zero ambiguity, 'unresolved' otherwise. It never guesses,
never physically migrates an ambiguous row, and never creates a
Knowledge Hub folder for any of this.
"""

import hashlib
import json
from datetime import datetime
from pathlib import Path

from Database.db_manager import DatabaseManager
from Core.logger import Logger


# Mirrors discovery_repository.py's CAPTURED_FLOW_SOURCE_TYPE /
# CAPTURED_FLOW_DOCUMENT_TYPE pattern — marks a knowledge_items row
# that exists only to bridge a LEGACY imported API collection under
# the Domain -> Module -> Knowledge Name -> Version tree for display
# purposes. New imports (with a test_case_version_id) never create
# one of these; see the module docstring.
API_COLLECTION_SOURCE_TYPE = "API_COLLECTION"

API_COLLECTION_DOCUMENT_TYPE = "API Collection"

DEFAULT_API_COLLECTION_VERSION = "1.0"


class ApiCollectionRepository:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.db = DatabaseManager()

    # ================================================================
    # Public entry point
    # ================================================================

    def import_postman_collection(
        self,
        file_path,
        domain=None,
        module=None,
        knowledge_name=None,
        version=None,
        document_type=None,
        test_case_version_id=None,
        test_case_file_id=None,
    ):
        """
        Parses a Postman Collection .json export at `file_path` and
        stores every request it contains.

        `test_case_version_id`, when given, files the collection under
        that Test Cases (TC-*) folder's "API-TC-*" automation subfolder
        (Rule 5's corrected model) and takes priority over
        domain/module/knowledge_name/version, which are still accepted
        for display/grounding metadata but no longer used to place a
        physical folder. Omit it to use the legacy Knowledge Hub bridge
        behavior kept for existing call sites — see the module
        docstring.

        Returns:
            {
                "success": bool,
                "collection_id": int | None,
                "knowledge_item_id": int | None,
                "test_case_version_id": int | None,
                "association_status": str | None,
                "collection_name": str | None,
                "folders_found": int,
                "endpoints_saved": int,
                "endpoints_skipped": int,
                "skipped_endpoints": [ {reason, name}, ... ],
                "error": str | None,
            }
        """

        summary = {
            "success": False,
            "collection_id": None,
            "knowledge_item_id": None,
            "test_case_version_id": None,
            "association_status": None,
            "collection_name": None,
            "folders_found": 0,
            "endpoints_saved": 0,
            "endpoints_skipped": 0,
            "skipped_endpoints": [],
            "error": None,
        }

        try:

            raw_text = Path(file_path).read_text(encoding="utf-8")

        except Exception as ex:

            summary["error"] = f"Could not read file: {ex}"

            return summary

        try:

            data = json.loads(raw_text)

        except json.JSONDecodeError as ex:

            summary["error"] = (
                f"This doesn't look like a valid JSON file "
                f"(problem at line {ex.lineno}: {ex.msg}). Export "
                f"the collection from Postman as 'Collection v2.1' "
                f"and try again."
            )

            return summary

        if not isinstance(data, dict) or "item" not in data:

            summary["error"] = (
                "This JSON file doesn't look like a Postman "
                "Collection export — expected an 'info' block and "
                "an 'item' array. In Postman: right-click the "
                "collection -> Export -> Collection v2.1."
            )

            return summary

        collection_name = (
            (data.get("info") or {}).get("name")
            or Path(file_path).stem
        )

        summary["collection_name"] = collection_name

        variables = self._extract_variables(data.get("variable") or [])

        endpoints, folders_found = self._flatten_items(
            data.get("item") or [], variables
        )

        if not endpoints:

            summary["error"] = (
                "No requests were found inside this collection — "
                "nothing to import."
            )

            return summary

        try:

            source_bytes = Path(file_path).read_bytes()

            source_name = Path(file_path).name

            knowledge_item_id = None

            repository_path = None

            association_status = None

            if test_case_version_id:

                # Rule 5 corrected model: file under the selected TC-*
                # folder's own automation subfolder, original bytes,
                # never a Knowledge Hub folder.
                version_row, source_row = self._test_case_version_context(
                    test_case_version_id
                )

                # Validate the authoritative workbook relationship
                # before creating a managed file or changing database
                # state. A stale/cross-folder selection must be a clean,
                # atomic rejection with no partial collection artifact.
                connection = self.db.get_connection()
                try:
                    file_row = connection.execute(
                        "SELECT id FROM test_case_files WHERE id=? AND test_case_version_id=?",
                        (int(test_case_file_id or 0), int(test_case_version_id)),
                    ).fetchone()
                finally:
                    connection.close()
                if not file_row:
                    raise ValueError("The selected Test Cases XLSX does not belong to the selected TC folder.")

                domain = (source_row or {}).get("domain") or domain

                module = (source_row or {}).get("module") or module

                knowledge_name = (source_row or {}).get("knowledge_name") or knowledge_name

                version = (source_row or {}).get("version") or version

                managed = self._write_managed_collection_file_in_tc_folder(
                    version_row["folder_path"], source_bytes, source_name,
                )

                repository_path = managed["repository_path"]

                association_status = "linked"

            else:

                # Legacy bridge, kept only for existing call sites —
                # see the module docstring. No physical folder is
                # created under Knowledge Hub/Central Repository.
                knowledge_item_id = self._get_or_create_linked_knowledge_item(
                    domain, module, knowledge_name, version, document_type,
                )

            summary["knowledge_item_id"] = knowledge_item_id

            summary["test_case_version_id"] = (
                int(test_case_version_id) if test_case_version_id else None
            )

            collection_id, collection_already_existed = self._save_collection(
                name=collection_name,
                source_file_name=source_name,
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=version,
                linked_knowledge_item_id=knowledge_item_id,
                test_case_version_id=(
                    int(test_case_version_id) if test_case_version_id else None
                ),
                test_case_file_id=(int(test_case_file_id) if test_case_file_id else None),
                repository_path=repository_path,
                association_status=association_status,
                variables=variables,
            )

            summary["collection_id"] = collection_id

            summary["association_status"] = association_status

            summary["collection_already_existed"] = collection_already_existed

            saved, skipped, skipped_detail = self._save_endpoints(
                collection_id, endpoints
            )

            summary["endpoints_saved"] = saved

            summary["endpoints_skipped"] = skipped

            summary["skipped_endpoints"] = skipped_detail

            summary["folders_found"] = folders_found

            summary["indexed_chunks"] = self._index_collection_for_search(
                collection_id=collection_id,
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=version,
                collection_name=collection_name,
                source_file_name=source_name,
                endpoints=endpoints,
                document_type=document_type,
                variables=variables,
            )

            summary["success"] = True

        except Exception as ex:

            self.logger.exception(
                "API collection import failed."
            )

            summary["error"] = str(ex)

        return summary

    # ================================================================
    # Postman JSON flattening
    # ================================================================

    def _extract_variables(self, variable_list):

        variables = {}

        for entry in variable_list:

            key = entry.get("key")

            if key:

                variables[key] = entry.get("value", "")

        return variables

    def _flatten_items(self, items, variables, folder_path=""):
        """
        Postman's `item` array is a tree — an entry is a REQUEST if
        it has a "request" key, otherwise it's a FOLDER with its own
        nested "item" array. Recurses depth-first, building a
        breadcrumb folder_path ("Users > Auth > Login") for context
        on every flattened endpoint.

        Returns (endpoints_list, folders_found_count).
        """

        endpoints = []

        folders_found = 0

        for entry in items:

            name = entry.get("name") or "(unnamed)"

            if "request" in entry:

                endpoints.append(
                    self._parse_request_item(entry, folder_path)
                )

            elif "item" in entry:

                folders_found += 1

                child_path = (
                    f"{folder_path} > {name}" if folder_path else name
                )

                child_endpoints, child_folders = self._flatten_items(
                    entry["item"], variables, child_path
                )

                endpoints.extend(child_endpoints)

                folders_found += child_folders

            # Entries with neither "request" nor "item" aren't a
            # shape this format defines — skip rather than guess.

        # Resolve {{variable}} placeholders now that the full
        # variable set is known, on every endpoint gathered so far
        # at THIS level (safe to do repeatedly — resolution is
        # idempotent and cheap).
        for endpoint in endpoints:

            endpoint["url_resolved"] = self._resolve_variables(
                endpoint["url_raw"], variables
            )

        return endpoints, folders_found

    def _parse_request_item(self, entry, folder_path):

        request = entry.get("request") or {}

        method = (request.get("method") or "GET").upper()

        url_field = request.get("url")

        if isinstance(url_field, dict):

            url_raw = url_field.get("raw", "")

        else:

            url_raw = url_field or ""

        headers = [
            {"key": h.get("key", ""), "value": h.get("value", "")}
            for h in (request.get("header") or [])
            if not h.get("disabled")
        ]

        query_params = []

        path_params = []

        if isinstance(url_field, dict):

            query_params = [
                {"key": q.get("key", ""), "value": q.get("value", "")}
                for q in (url_field.get("query") or [])
                if not q.get("disabled")
            ]

            path_params = [
                {"key": v.get("key", ""), "value": v.get("value", "")}
                for v in (url_field.get("variable") or [])
            ]

        raw_description = request.get("description")

        if raw_description is None:

            raw_description = entry.get("description")

        if isinstance(raw_description, dict):

            description = raw_description.get("content", "")

        else:

            description = raw_description or ""

        tests_script = ""

        for event in (entry.get("event") or []):

            if event.get("listen") == "test":

                exec_lines = ((event.get("script") or {}).get("exec")) or []

                if isinstance(exec_lines, str):

                    exec_lines = [exec_lines]

                tests_script = "\n".join(str(line) for line in exec_lines).strip()

                if tests_script:

                    break

        body = request.get("body") or {}

        body_mode = body.get("mode", "")

        if body_mode == "raw":

            body_raw = body.get("raw", "")

        elif body_mode == "urlencoded":

            body_raw = json.dumps(
                {p.get("key", ""): p.get("value", "")
                 for p in body.get("urlencoded", [])}
            )

        elif body_mode == "formdata":

            body_raw = json.dumps(
                {p.get("key", ""): p.get("value", "")
                 for p in body.get("formdata", [])}
            )

        else:

            body_raw = ""

        auth = request.get("auth")

        auth_type = auth.get("type", "") if auth else ""

        auth_details = auth if auth else {}

        example_response_status = None

        example_response_body = None

        responses = entry.get("response") or []

        if responses:

            first_response = responses[0]

            example_response_status = first_response.get("code")

            example_response_body = first_response.get("body")

        return {
            "folder_path": folder_path,
            "name": entry.get("name") or "(unnamed)",
            "method": method,
            "url_raw": url_raw,
            "headers": headers,
            "query_params": query_params,
            "path_params": path_params,
            "body_mode": body_mode,
            "body_raw": body_raw,
            "auth_type": auth_type,
            "auth_details": auth_details,
            "example_response_status": example_response_status,
            "example_response_body": example_response_body,
            "description": description,
            "tests_script": tests_script,
        }

    def _resolve_variables(self, text, variables):
        """
        Best-effort {{variableName}} substitution — a real, usable
        URL in generated scripts beats a literal "{{baseUrl}}" every
        time, but this is NOT meant to be a full templating engine:
        a variable Postman itself only resolves via an active
        environment file (not exported with the collection) is left
        as-is rather than guessed at.
        """

        if not text:

            return text

        resolved = text

        for key, value in variables.items():

            resolved = resolved.replace(f"{{{{{key}}}}}", str(value))

        return resolved

    # ================================================================
    # Persistence
    # ================================================================

    def _save_collection(
        self,
        name,
        source_file_name,
        domain,
        module,
        knowledge_name,
        version,
        linked_knowledge_item_id,
        variables,
        test_case_version_id=None,
        test_case_file_id=None,
        repository_path=None,
        association_status=None,
    ):
        """
        Get-or-create, keyed by whichever real relationship this
        import actually has: `test_case_version_id` for the Rule 5
        corrected model, else the legacy `linked_knowledge_item_id`
        bridge. Previously this always INSERTed a brand-new
        `api_collections` row on every import, even a byte-identical
        re-import of the same collection under the same scope —
        leaving duplicate collection rows (and, via _save_endpoints()
        below, duplicate endpoint rows that silently orphaned any Test
        Case already bound to the earlier import's endpoint id).
        Re-importing under the same TC folder / scope now UPDATES the
        existing collection row in place instead, so existing endpoint
        ids/bindings survive re-import.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        existing_id = None

        if test_case_file_id is not None:

            cursor.execute(
                "SELECT id FROM api_collections WHERE test_case_file_id = ?",
                (test_case_file_id,),
            )

            row = cursor.fetchone()

            if row:

                existing_id = row[0]

        elif linked_knowledge_item_id is not None:

            cursor.execute(
                "SELECT id FROM api_collections "
                "WHERE linked_knowledge_item_id = ? AND test_case_version_id IS NULL",
                (linked_knowledge_item_id,),
            )

            row = cursor.fetchone()

            if row:

                existing_id = row[0]

        if existing_id is not None:

            cursor.execute(
                """
                UPDATE api_collections
                SET name = ?, source_file_name = ?, domain = ?,
                    module = ?, knowledge_name = ?, version = ?,
                    repository_path = COALESCE(?, repository_path),
                    association_status = COALESCE(?, association_status),
                    test_case_version_id = COALESCE(?, test_case_version_id),
                    test_case_file_id = COALESCE(?, test_case_file_id),
                    variables_json = ?, status = 'Active',
                    modified_date = ?
                WHERE id = ?
                """,
                (
                    name,
                    source_file_name,
                    domain or "",
                    module or "",
                    knowledge_name or "",
                    version or "",
                    repository_path,
                    association_status,
                    test_case_version_id,
                    test_case_file_id,
                    json.dumps(variables),
                    now,
                    existing_id,
                ),
            )

            collection_id = existing_id

        else:

            cursor.execute(
                """
                INSERT INTO api_collections
                (name, source_file_name, domain, module, knowledge_name,
                 version, linked_knowledge_item_id, test_case_version_id,
                 test_case_file_id,
                 repository_path, association_status, variables_json,
                 status, created_date, modified_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?)
                """,
                (
                    name,
                    source_file_name,
                    domain or "",
                    module or "",
                    knowledge_name or "",
                    version or "",
                    linked_knowledge_item_id,
                    test_case_version_id,
                    test_case_file_id,
                    repository_path,
                    association_status or "unresolved",
                    json.dumps(variables),
                    now,
                    now,
                ),
            )

            collection_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return collection_id, existing_id is not None

    def _save_endpoints(self, collection_id, endpoints):
        """
        API-Automation-Enhancement Phase 7-upload: get-or-create per
        endpoint, keyed by (method, url_raw) WITHIN this collection_id
        — previously always INSERTed every endpoint fresh, so
        re-importing the identical collection (e.g. re-running the
        same upload, or a CI step that always re-uploads) doubled,
        tripled, etc. the endpoint rows every time, and any Test Case
        already bound to an earlier import's endpoint id silently kept
        pointing at a now-stale duplicate while a fresh, identical-
        looking duplicate sat unbound next to it. A matched endpoint is
        now UPDATED in place (imported fields refreshed, id unchanged
        — existing bindings/history keep pointing at the same row); a
        genuinely new (method, url) pair is still inserted as before.
        """

        saved = 0

        skipped = 0

        skipped_detail = []

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            "SELECT id, method, url_raw FROM api_endpoints "
            "WHERE collection_id = ?",
            (collection_id,),
        )

        existing_by_key = {
            (row[1] or "", row[2] or ""): row[0] for row in cursor.fetchall()
        }

        for endpoint in endpoints:

            if not endpoint.get("url_raw"):

                skipped += 1

                skipped_detail.append(
                    {
                        "reason": "no URL on this request",
                        "name": endpoint.get("name", ""),
                    }
                )

                continue

            key = (endpoint.get("method", ""), endpoint.get("url_raw", ""))

            existing_endpoint_id = existing_by_key.get(key)

            if existing_endpoint_id is not None:

                cursor.execute(
                    """
                    UPDATE api_endpoints
                    SET folder_path = ?, name = ?, url_resolved = ?,
                        headers_json = ?, query_params_json = ?,
                        body_mode = ?, body_raw = ?, auth_type = ?,
                        auth_details_json = ?, example_response_status = ?,
                        example_response_body = ?, status = 'Active',
                        modified_date = ?
                    WHERE id = ?
                    """,
                    (
                        endpoint.get("folder_path", ""),
                        endpoint.get("name", ""),
                        endpoint.get("url_resolved", ""),
                        json.dumps(endpoint.get("headers", [])),
                        json.dumps(endpoint.get("query_params", [])),
                        endpoint.get("body_mode", ""),
                        endpoint.get("body_raw", ""),
                        endpoint.get("auth_type", ""),
                        json.dumps(endpoint.get("auth_details", {})),
                        endpoint.get("example_response_status"),
                        endpoint.get("example_response_body"),
                        now,
                        existing_endpoint_id,
                    ),
                )

                skipped += 1

                skipped_detail.append(
                    {
                        "reason": (
                            "already imported (same method + URL) — "
                            "existing binding preserved, imported "
                            "fields refreshed"
                        ),
                        "name": endpoint.get("name", ""),
                    }
                )

                continue

            cursor.execute(
                """
                INSERT INTO api_endpoints
                (collection_id, folder_path, name, method, url_raw,
                 url_resolved, headers_json, query_params_json,
                 body_mode, body_raw, auth_type, auth_details_json,
                 example_response_status, example_response_body,
                 status, created_date, modified_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        'Active', ?, ?)
                """,
                (
                    collection_id,
                    endpoint.get("folder_path", ""),
                    endpoint.get("name", ""),
                    endpoint.get("method", ""),
                    endpoint.get("url_raw", ""),
                    endpoint.get("url_resolved", ""),
                    json.dumps(endpoint.get("headers", [])),
                    json.dumps(endpoint.get("query_params", [])),
                    endpoint.get("body_mode", ""),
                    endpoint.get("body_raw", ""),
                    endpoint.get("auth_type", ""),
                    json.dumps(endpoint.get("auth_details", {})),
                    endpoint.get("example_response_status"),
                    endpoint.get("example_response_body"),
                    now,
                    now,
                ),
            )

            existing_by_key[key] = cursor.lastrowid

            saved += 1

        conn.commit()

        conn.close()

        return saved, skipped, skipped_detail

    def _index_collection_for_search(
        self,
        collection_id,
        domain,
        module,
        knowledge_name,
        version,
        collection_name,
        source_file_name,
        endpoints,
        document_type=None,
        variables=None,
    ):
        try:

            from Core.document_chunker import DocumentChunker
            from Core.embedding_engine import EmbeddingEngine
            from Core.vector_store import VectorStore

            resolved_document_type = (
                str(document_type or "").strip() or API_COLLECTION_DOCUMENT_TYPE
            )

            text = self._build_searchable_text(
                collection_name, domain, module, knowledge_name, endpoints,
                variables=variables,
            )

            chunks = DocumentChunker().split(text)

            if not chunks:

                return 0

            embeddings = EmbeddingEngine().generate_embeddings(chunks)

            if embeddings is None:

                embeddings = [None] * len(chunks)

            batch_items = []

            for index, chunk in enumerate(chunks, start=1):

                embedding = embeddings[index - 1]

                if embedding is None:

                    continue

                batch_items.append({
                    "doc_id": f"apicollection_{collection_id}_{index}",
                    "text": chunk,
                    "embedding": embedding,
                    "metadata": {
                        "domain": domain or "",
                        "module": module or "",
                        "knowledge_name": knowledge_name or "",
                        "version": version or "",
                        "file_name": source_file_name,
                        "file_type": ".json",
                        "category": "API",
                        "document_type": resolved_document_type,
                        "summary": (
                            f"Imported API collection '{collection_name}' "
                            f"({len(endpoints)} endpoint(s))."
                        ),
                        "tags": "api,endpoints,postman",
                        "confidence": 1.0,
                        "chunk_number": index,
                        "total_chunks": len(chunks),
                    },
                })

            if not batch_items:

                return 0

            return VectorStore().save_documents_batch(batch_items)

        except Exception:

            self.logger.exception(
                "Indexing API collection for search failed — the "
                "import itself still succeeded, but AI test-case "
                "generation may not find this collection's scope "
                "until it's re-imported or a document is added."
            )

            return 0

    def _build_searchable_text(
        self, collection_name, domain, module, knowledge_name, endpoints,
        variables=None,
    ):
        """
        Flattens the collection into readable text an embedding
        model and an LLM can both make sense of — same intent as
        Postman's own request list, just linearized.

        QA-AI-STUDIO-API-COLLECTION-KNOWLEDGE-QA-ENGINEERING-API-AUTOMATION-FINAL-FIX
        items 3/4/11: this is the ONLY text QA Engineering's grounded
        retrieval ever sees for an imported API Collection, so it must
        carry enough structure (headers, query/path params, auth type,
        body, expected status, description, tests/scripts, variable
        NAMES) for the AI to write real positive/negative/boundary/
        integration cases against — but every header/auth value that
        looks like a credential is masked first (Core.secret_masking),
        never embedded as a live secret. Variable NAMES (e.g.
        "X-Token") are listed so the AI can reference "{{X-Token}}";
        their resolved VALUES are never included here.
        """

        from Core.secret_masking import mask_value

        lines = [
            f"API Collection: {collection_name}",
            (
                f"Scope: {domain or '(no domain)'} / "
                f"{module or '(no module)'} / "
                f"{knowledge_name or '(no knowledge name)'}"
            ),
            f"Total endpoints: {len(endpoints)}",
        ]

        variable_names = sorted((variables or {}).keys())

        if variable_names:

            lines.append(
                "Collection variables (reference as {{name}} — values "
                "are never exposed here): " + ", ".join(variable_names)
            )

        lines.append("")
        lines.append("This collection defines the following API endpoints:")
        lines.append("")

        for endpoint in endpoints:

            url = endpoint.get("url_resolved") or endpoint.get("url_raw", "")

            lines.append(
                f"- {endpoint.get('method', '')} {url} "
                f"({endpoint.get('name', '')})"
            )

            if endpoint.get("folder_path"):

                lines.append(f"  Folder: {endpoint['folder_path']}")

            if endpoint.get("description"):

                lines.append(f"  Description: {endpoint['description']}")

            headers = endpoint.get("headers") or []

            if headers:

                header_text = ", ".join(
                    f"{h.get('key', '')}: {mask_value(h.get('key', ''), h.get('value', ''))}"
                    for h in headers
                    if h.get("key")
                )

                if header_text:

                    lines.append(f"  Headers (required): {header_text}")

            query_params = endpoint.get("query_params") or []

            if query_params:

                lines.append(
                    "  Query params: " + ", ".join(
                        f"{p.get('key', '')}={mask_value(p.get('key', ''), p.get('value', ''))}"
                        for p in query_params if p.get("key")
                    )
                )

            path_params = endpoint.get("path_params") or []

            if path_params:

                lines.append(
                    "  Path params: " + ", ".join(
                        f"{p.get('key', '')}={mask_value(p.get('key', ''), p.get('value', ''))}"
                        for p in path_params if p.get("key")
                    )
                )

            if endpoint.get("auth_type"):

                lines.append(f"  Auth type: {endpoint['auth_type']}")

            if endpoint.get("body_raw"):

                body_preview = endpoint["body_raw"][:500]

                lines.append(f"  Body ({endpoint.get('body_mode', '')}): "
                             f"{body_preview}")

            if endpoint.get("example_response_status"):

                lines.append(
                    f"  Expected status (example): "
                    f"{endpoint['example_response_status']}"
                )

            if endpoint.get("example_response_body"):

                response_preview = str(endpoint["example_response_body"])[:500]

                lines.append(f"  Example response: {response_preview}")

            if endpoint.get("tests_script"):

                lines.append(
                    f"  Tests/scripts (from the collection): "
                    f"{endpoint['tests_script'][:500]}"
                )

            lines.append("")

        return "\n".join(lines)

    # ================================================================
    # Legacy Knowledge Hub bridge (kept only for existing call sites —
    # see the module docstring; never creates a physical folder)
    # ================================================================

    def _get_or_create_linked_knowledge_item(
        self, domain, module, knowledge_name, version, document_type=None,
    ):
        """
        Same bridge pattern as
        DiscoveryRepository._get_or_create_captured_knowledge_item()
        — files this import under the Domain -> Module -> Knowledge
        Name -> Version tree by creating (or reusing) a placeholder
        knowledge_items row, so it appears in Manage Knowledge
        alongside any document describing the same API. Returns None
        (no linkage) unless domain, module AND knowledge_name are
        all given.

        Deliberately does NOT create any physical folder under
        Central Repository — an earlier pass added that and it was
        reverted (see the module docstring) because Rule 5 forbids
        an API Collection from ever being filed as a Knowledge Hub
        source type/folder. This bridge row exists purely so legacy
        callers/UI keep rendering the same way they did before.

        Document Type is a user-selected classification (e.g.
        "Integration Document") completely independent of Source Type
        ("API Collection") — it must never be derived from/overwritten
        by the source type. `document_type`, when given a real value,
        both seeds a NEW placeholder row and corrects an EXISTING one.
        """

        domain = (domain or "").strip()

        module = (module or "").strip()

        knowledge_name = (knowledge_name or "").strip()

        version = (
            (version or "").strip() or DEFAULT_API_COLLECTION_VERSION
        )

        document_type = (document_type or "").strip()

        if not domain or not module or not knowledge_name:

            return None

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id, document_type FROM knowledge_items
            WHERE domain = ? AND module = ? AND knowledge_name = ?
              AND version = ? AND source_type = ?
            """,
            (domain, module, knowledge_name, version,
             API_COLLECTION_SOURCE_TYPE),
        )

        row = cursor.fetchone()

        if row:

            existing_id, existing_document_type = row[0], row[1]

            if document_type and document_type != existing_document_type:

                cursor.execute(
                    "UPDATE knowledge_items SET document_type = ?, "
                    "modified_date = ? WHERE id = ?",
                    (document_type, datetime.now().isoformat(), existing_id),
                )

                conn.commit()

            conn.close()

            return existing_id

        conn.close()

        from Core.metadata_manager import MetadataManager

        metadata_manager = MetadataManager()

        domain_id = metadata_manager.get_or_create_domain(domain)

        module_id = metadata_manager.get_or_create_module(domain, module)

        knowledge_path = f"{domain}/{module}/{knowledge_name}/{version}"

        sentinel_sha256 = hashlib.sha256(
            f"api-collection::{knowledge_path}".encode("utf-8")
        ).hexdigest()

        now = datetime.now().isoformat()

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO knowledge_items
            (domain, module, knowledge_name, version, knowledge_path,
             file_name, original_path, repository_path, sha256,
             file_size, extension, knowledge_type, source_type,
             document_type, status, created_date, modified_date,
             domain_id, module_id)
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, '.json',
                    'API_COLLECTION', ?, ?, 'ACTIVE', ?, ?, ?, ?)
            """,
            (
                domain,
                module,
                knowledge_name,
                version,
                knowledge_path,
                "(imported API collection)",
                knowledge_path,
                sentinel_sha256,
                API_COLLECTION_SOURCE_TYPE,
                document_type or API_COLLECTION_DOCUMENT_TYPE,
                now,
                now,
                domain_id,
                module_id,
            ),
        )

        knowledge_item_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return knowledge_item_id

    # ================================================================
    # Test Cases (TC-*) folder storage — Rule 5 corrected model
    # ================================================================

    def _test_case_version_context(self, test_case_version_id):
        """
        Returns (version_row, source_knowledge_item_row) for a real
        TC-* folder — `version_row["folder_path"]` is the actual
        physical TC folder to file the "API-TC-*" automation subfolder
        under. Raises ValueError if the id doesn't resolve, so a bad
        selection fails loudly instead of silently filing under a
        placeholder path.
        """

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM test_case_versions WHERE id = ?",
            (int(test_case_version_id),),
        )

        version_row = cursor.fetchone()

        if not version_row:

            conn.close()

            raise ValueError(
                f"Test Cases folder (id={test_case_version_id}) was not found."
            )

        source_row = None

        if version_row.get("source_knowledge_id"):

            cursor.execute(
                "SELECT * FROM knowledge_items WHERE id = ?",
                (version_row["source_knowledge_id"],),
            )

            source_row = cursor.fetchone()

        conn.close()

        if not Path(version_row["folder_path"] or "").is_dir():

            raise ValueError(
                "The selected Test Cases folder is unavailable on disk."
            )

        return version_row, source_row

    def _write_managed_collection_file_in_tc_folder(
        self, tc_folder_path, source_bytes, source_name,
    ):
        """
        Creates (if missing) the "API-TC-*" automation subfolder
        directly inside the given TC-* folder and writes `source_bytes`
        into it as `source_name`, UNCHANGED — Rule 5: "Do not
        unnecessarily rewrite the original collection."

        Collision-safe: if a file of that name already exists there
        with DIFFERENT content (e.g. two distinct collections that
        happen to share a filename, the exact root cause behind the
        misleading duplicate-looking "(2)"/"(5)" entries reported
        live), the new file is saved under a disambiguated name
        instead of silently overwriting or colliding — re-uploading
        the SAME bytes just refreshes the existing file in place.
        """

        tc_folder = Path(tc_folder_path)

        automation_folder = tc_folder / f"API-{tc_folder.name}"

        automation_folder.mkdir(parents=True, exist_ok=True)

        base_name = source_name or "api-collection.json"

        stem = Path(base_name).stem

        suffix = Path(base_name).suffix or ".json"

        destination = automation_folder / base_name

        counter = 2

        while destination.exists() and destination.read_bytes() != source_bytes:

            destination = automation_folder / f"{stem} ({counter}){suffix}"

            counter += 1

        destination.write_bytes(source_bytes)

        return {
            "repository_path": str(destination),
            "file_name": destination.name,
            "sha256": hashlib.sha256(source_bytes).hexdigest(),
            "file_size": len(source_bytes),
        }

    def _reconstruct_collection_json(self, collection, endpoints):
        """
        Rebuilds a Postman Collection v2.1-shaped JSON document from
        what was actually persisted (the collection row + its
        api_endpoints rows) for a LEGACY import whose original .json
        upload was never retained. This is a faithful reconstruction of
        the imported data, not a byte-identical copy of the original
        file — disclosed via the `_qa_ai_studio_migration_note` field
        so this is never mistaken for the genuine original export.
        """

        items = []

        for endpoint in endpoints:

            try:
                headers = json.loads(endpoint.get("headers_json") or "[]")
            except (TypeError, ValueError):
                headers = []

            try:
                query_params = json.loads(endpoint.get("query_params_json") or "[]")
            except (TypeError, ValueError):
                query_params = []

            url = {"raw": endpoint.get("url_raw") or endpoint.get("url_resolved") or ""}

            if query_params:

                url["query"] = query_params

            request = {
                "method": endpoint.get("method") or "GET",
                "header": headers,
                "url": url,
            }

            if endpoint.get("body_raw"):

                request["body"] = {
                    "mode": endpoint.get("body_mode") or "raw",
                    "raw": endpoint.get("body_raw"),
                }

            try:
                auth_details = json.loads(endpoint.get("auth_details_json") or "null")
            except (TypeError, ValueError):
                auth_details = None

            if auth_details:

                request["auth"] = auth_details

            items.append({
                "name": endpoint.get("name") or "(unnamed)",
                "request": request,
            })

        payload = {
            "info": {
                "name": collection.get("name") or "Imported API Collection",
                "_postman_id": f"qa-ai-studio-migrated-{collection.get('id')}",
                "schema": "https://schema.getpostman.com/json/collection/v2.1.0/collection.json",
                "_qa_ai_studio_migration_note": (
                    "Reconstructed by QA AI Studio from persisted endpoint "
                    "data during the Rule-5 TC-folder association audit "
                    "(QA-AI-STUDIO-FINAL-ARCHITECTURE-UI-AUTOMATION-COMPLETION-V4) "
                    "— the original uploaded .json was not retained by the "
                    "legacy import path, so this is a faithful reconstruction "
                    "of what was imported, not a byte-identical copy of the "
                    "original upload."
                ),
            },
            "item": items,
        }

        return json.dumps(payload, indent=2).encode("utf-8")

    def migrate_unresolved_legacy_collections(self):
        """
        Startup audit — NEVER a physical Knowledge-Hub migration (see
        Web/main.py's startup hook for why the earlier
        migrate_legacy_managed_paths() approach was wrong and removed
        before ever running against a live backend).

        For every ACTIVE api_collections row that does not yet have a
        `test_case_version_id`:

          - If more than one unresolved collection shares the exact
            same Domain/Module/Knowledge Name/Version, which one is
            "the" canonical collection for that scope is NOT
            deterministic — ALL of them are left `association_status
            = 'unresolved'` rather than guessing.
          - Otherwise, if that scope resolves to exactly ONE Test
            Cases (TC-*) folder across every knowledge_items row at
            that scope, that is an unambiguous, deterministic binding:
            the collection is linked to it now (its JSON is
            reconstructed from persisted endpoint data — see
            `_reconstruct_collection_json` — and written into that TC
            folder's "API-TC-*" subfolder).
          - Otherwise (zero or multiple TC-folder candidates, or no
            Domain/Module/Knowledge Name at all), the row is left
            completely untouched except `association_status =
            'unresolved'`.

        Idempotent and safe to call on every startup. Returns a
        summary dict for the caller to log.
        """

        summary = {
            "checked": 0,
            "already_linked": 0,
            "auto_linked": 0,
            "unresolved": 0,
            "errors": [],
        }

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute("SELECT * FROM api_collections WHERE status = 'Active'")

        collections = cursor.fetchall()

        conn.close()

        def _scope_key(row):

            return (
                (row.get("domain") or "").strip(),
                (row.get("module") or "").strip(),
                (row.get("knowledge_name") or "").strip(),
                (row.get("version") or "").strip(),
            )

        unresolved_rows = [row for row in collections if not row.get("test_case_version_id")]

        scope_groups = {}

        for row in unresolved_rows:

            scope_groups.setdefault(_scope_key(row), []).append(row)

        for collection in collections:

            summary["checked"] += 1

            try:

                if collection.get("test_case_version_id"):

                    summary["already_linked"] += 1

                    continue

                key = _scope_key(collection)

                domain, module, knowledge_name, version = key

                if not domain or not module or not knowledge_name:

                    self._mark_association_status(collection["id"], "unresolved")

                    summary["unresolved"] += 1

                    continue

                if len(scope_groups.get(key, [])) != 1:

                    # More than one unresolved collection shares this
                    # exact scope -- which one is canonical is not
                    # deterministic. Leave all of them unresolved.
                    self._mark_association_status(collection["id"], "unresolved")

                    summary["unresolved"] += 1

                    continue

                conn = self.db.get_connection()

                conn.row_factory = self._dict_factory

                cursor = conn.cursor()

                query = (
                    "SELECT id FROM knowledge_items "
                    "WHERE domain = ? AND module = ? AND knowledge_name = ?"
                )

                params = [domain, module, knowledge_name]

                if version:

                    query += " AND version = ?"

                    params.append(version)

                cursor.execute(query, params)

                knowledge_item_ids = [row["id"] for row in cursor.fetchall()]

                candidates = []

                if knowledge_item_ids:

                    placeholders = ",".join("?" for _ in knowledge_item_ids)

                    cursor.execute(
                        f"SELECT * FROM test_case_versions "
                        f"WHERE source_knowledge_id IN ({placeholders})",
                        knowledge_item_ids,
                    )

                    candidates = cursor.fetchall()

                conn.close()

                if len(candidates) != 1:

                    self._mark_association_status(collection["id"], "unresolved")

                    summary["unresolved"] += 1

                    continue

                version_row = candidates[0]

                if not Path(version_row.get("folder_path") or "").is_dir():

                    self._mark_association_status(collection["id"], "unresolved")

                    summary["unresolved"] += 1

                    continue

                endpoints = self.list_endpoints(collection["id"])

                source_bytes = self._reconstruct_collection_json(collection, endpoints)

                source_name = (
                    str(collection.get("source_file_name") or "").strip()
                    or f"{str(collection.get('name') or 'api-collection').strip()}.json"
                )

                managed = self._write_managed_collection_file_in_tc_folder(
                    version_row["folder_path"], source_bytes, source_name,
                )

                conn = self.db.get_connection()

                cursor = conn.cursor()

                cursor.execute(
                    """
                    UPDATE api_collections
                    SET test_case_version_id = ?, repository_path = ?,
                        association_status = 'linked', modified_date = ?
                    WHERE id = ?
                    """,
                    (
                        version_row["id"],
                        managed["repository_path"],
                        datetime.now().isoformat(),
                        collection["id"],
                    ),
                )

                conn.commit()

                conn.close()

                summary["auto_linked"] += 1

            except Exception as ex:

                self.logger.exception(
                    "Legacy API Collection association audit failed for "
                    "collection id=%s", collection.get("id"),
                )

                summary["errors"].append({
                    "collection_id": collection.get("id"),
                    "error": str(ex),
                })

        self.logger.info("API Collection legacy association audit: %s", summary)

        return summary

    def _mark_association_status(self, collection_id, status):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "UPDATE api_collections SET association_status = ? WHERE id = ?",
            (status, collection_id),
        )

        conn.commit()

        conn.close()

    # ================================================================
    # Read APIs
    # ================================================================

    def list_collections(self):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM api_collections WHERE status = 'Active' "
            "ORDER BY id DESC"
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    def get_collection(self, collection_id):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM api_collections WHERE id = ?",
            (collection_id,),
        )

        row = cursor.fetchone()

        conn.close()

        return row

    def get_endpoints_by_ids(self, endpoint_ids):
        """
        Batch lookup for a list of real endpoint ids -- section D
        (QA-AI-STUDIO-API-AUTOMATION-END-TO-END-DEVICE-FINALIZATION):
        powers the API Automation grid's "TEST CASE / API TITLE"
        column, which needs each bound Test Case's real endpoint
        name/method without issuing one query per row. Silently
        drops any id that doesn't correspond to a real endpoint
        rather than erroring, since a bound_api_endpoint_id can point
        at an endpoint that was later removed. Returns {id: row}.
        """

        endpoint_ids = [int(value) for value in (endpoint_ids or []) if value is not None]

        if not endpoint_ids:

            return {}

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        placeholders = ",".join("?" for _ in endpoint_ids)

        cursor.execute(
            f"SELECT * FROM api_endpoints WHERE id IN ({placeholders})",
            endpoint_ids,
        )

        rows = cursor.fetchall()

        conn.close()

        return {row["id"]: row for row in rows}

    def backfill_linked_knowledge_item(self, collection_id):
        """
        Self-heals a legacy imported API Collection whose
        linked_knowledge_item_id is NULL -- a state that predates
        this linking logic and was never backfilled. Idempotent: a
        collection that is already linked is returned unchanged,
        untouched. Reuses the SAME _get_or_create_linked_knowledge_item()
        a legacy import already calls, per AGENTS.md's "Reuse before
        rewrite" -- never a second, parallel linking implementation.

        Kept for the existing call site in
        Core/test_execution_manager.py. Does not create any physical
        Knowledge Hub folder (see that method's own docstring); does
        not affect `test_case_version_id`/`association_status`, which
        `migrate_unresolved_legacy_collections()` owns.
        """

        collection = self.get_collection(collection_id)

        if not collection:

            raise ValueError(f"API Collection {collection_id} not found.")

        if collection.get("linked_knowledge_item_id"):

            return collection["linked_knowledge_item_id"]

        knowledge_item_id = self._get_or_create_linked_knowledge_item(
            collection.get("domain"), collection.get("module"),
            collection.get("knowledge_name"), collection.get("document_type"),
        )

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "UPDATE api_collections SET linked_knowledge_item_id = ? WHERE id = ?",
            (knowledge_item_id, collection_id),
        )

        conn.commit()

        conn.close()

        return knowledge_item_id

    def list_endpoints(self, collection_id):

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT * FROM api_endpoints
            WHERE collection_id = ? AND status = 'Active'
            ORDER BY id ASC
            """,
            (collection_id,),
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    def get_endpoint(self, endpoint_id):
        """
        Single-endpoint read backing the Postman-style endpoint
        editor (view + edit method/URL/headers/body of one imported
        request) — used by "View Request".
        """

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            "SELECT * FROM api_endpoints WHERE id = ?",
            (endpoint_id,),
        )

        row = cursor.fetchone()

        conn.close()

        return row

    def update_endpoint(
        self,
        endpoint_id,
        method=None,
        name=None,
        url_raw=None,
        url_resolved=None,
        headers_json=None,
        body_mode=None,
        body_raw=None,
    ):
        """
        Persists edits made in the Postman-style endpoint editor.
        Only columns passed as something other than None are
        updated, so a caller can save just what changed without
        needing to re-supply every field.
        """

        columns_and_values = [
            ("method", method),
            ("name", name),
            ("url_raw", url_raw),
            ("url_resolved", url_resolved),
            ("headers_json", headers_json),
            ("body_mode", body_mode),
            ("body_raw", body_raw),
        ]

        set_clauses = []

        values = []

        for column, value in columns_and_values:

            if value is not None:

                set_clauses.append(f"{column} = ?")

                values.append(value)

        if not set_clauses:

            return False

        set_clauses.append("modified_date = ?")

        values.append(datetime.now().isoformat())

        values.append(endpoint_id)

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            f"UPDATE api_endpoints SET {', '.join(set_clauses)} "
            f"WHERE id = ?",
            values,
        )

        conn.commit()

        conn.close()

        return True

    def get_endpoints_for_scope(self, domain, module, knowledge_name):
        """
        Returns every endpoint belonging to any active collection
        imported under this Domain/Module/Knowledge Name — this is
        what grounds API script generation: a test case filed under
        the same Domain/Module/Knowledge Name as an imported
        collection can pull its REAL endpoints instead of the AI
        guessing.

        QA-AI-STUDIO-API-SQL-AUTOMATION-LIFECYCLE-ROOT-FIX: the exact
        match below is intentionally tried first and preferred (never
        changes existing, correctly-scoped behavior), but it used to
        be the ONLY attempt — an operator who imported a collection
        under "Trade facilitation" (or with a trailing space) while
        the test case's own scope says "Trade Facilitation" got a
        silent, unexplained zero-endpoints result with no way to
        tell why. Falls back to a case-insensitive, whitespace-
        trimmed match on the same three fields whenever the exact
        match finds nothing, so a harmless capitalization/whitespace
        difference at import time no longer blocks every downstream
        check (endpoint_available, AI generation grounding, Execute
        Against Real Server) that depends on this lookup.
        """

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT e.*
            FROM api_endpoints e
            JOIN api_collections c ON c.id = e.collection_id
            WHERE c.status = 'Active' AND e.status = 'Active'
              AND c.test_case_file_id IS NULL
              AND c.domain = ? AND c.module = ?
              AND c.knowledge_name = ?
            ORDER BY e.id ASC
            """,
            (domain or "", module or "", knowledge_name or ""),
        )

        rows = cursor.fetchall()

        if not rows:

            cursor.execute(
                """
                SELECT e.*
                FROM api_endpoints e
                JOIN api_collections c ON c.id = e.collection_id
                WHERE c.status = 'Active' AND e.status = 'Active'
                  AND c.test_case_file_id IS NULL
                  AND TRIM(LOWER(c.domain)) = TRIM(LOWER(?))
                  AND TRIM(LOWER(c.module)) = TRIM(LOWER(?))
                  AND TRIM(LOWER(c.knowledge_name)) = TRIM(LOWER(?))
                ORDER BY e.id ASC
                """,
                (domain or "", module or "", knowledge_name or ""),
            )

            rows = cursor.fetchall()

        conn.close()

        return rows

    def get_endpoints_for_knowledge_item(self, knowledge_item_id):
        """
        The other direction of the LEGACY link — given a Knowledge
        Hub node, returns the endpoints of whichever collection(s) are
        still bridged to it via `linked_knowledge_item_id`. Kept for
        the desktop app's manage_knowledge_page.py; unused by this web
        app (new collections use `get_endpoints_for_test_case_version`
        instead).
        """

        if not knowledge_item_id:

            return []

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT e.*
            FROM api_endpoints e
            JOIN api_collections c ON c.id = e.collection_id
            WHERE c.linked_knowledge_item_id = ? AND e.status = 'Active'
            ORDER BY e.id ASC
            """,
            (knowledge_item_id,),
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    def get_endpoints_for_test_case_version(self, test_case_version_id):
        """
        Rule 5 corrected model's read side: given a TC-* folder
        (test_case_versions.id), returns the endpoints of whichever
        API Collection is bound to it via `test_case_version_id`. This
        is the real, stable relationship — never filenames — that the
        API Automation panel and "View Request" should read from for
        any collection imported through the new TC-folder upload flow.
        """

        if not test_case_version_id:

            return []

        conn = self.db.get_connection()

        conn.row_factory = self._dict_factory

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT e.*
            FROM api_endpoints e
            JOIN api_collections c ON c.id = e.collection_id
            WHERE c.test_case_version_id = ? AND c.status = 'Active'
              AND e.status = 'Active'
            ORDER BY e.id ASC
            """,
            (int(test_case_version_id),),
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    def get_endpoints_for_test_case_file(self, test_case_file_id):
        if not test_case_file_id:
            return []
        conn = self.db.get_connection()
        conn.row_factory = self._dict_factory
        rows = conn.execute(
            """SELECT e.* FROM api_endpoints e
               JOIN api_collections c ON c.id=e.collection_id
               WHERE c.test_case_file_id=? AND c.status='Active' AND e.status='Active'
               ORDER BY e.id""",
            (int(test_case_file_id),),
        ).fetchall()
        conn.close()
        return rows

    @staticmethod
    def _dict_factory(cursor, row):

        columns = [col[0] for col in cursor.description]

        return {
            columns[i]: row[i]
            for i in range(len(columns))
        }
