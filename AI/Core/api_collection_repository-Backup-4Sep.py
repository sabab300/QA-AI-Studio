# Create: AI/Core/api_collection_repository.py

"""
QA AI Studio
API Collection Repository

Version: 1.0

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

Naming, for now: an imported collection can optionally be filed
under a Domain / Module / Knowledge Name / Version, via the exact
same placeholder-knowledge_items-row bridge pattern
DiscoveryRepository._get_or_create_captured_knowledge_item() uses
for captured URL flows — so an API collection and, say, an SRS
document describing that same API can be filed under one Knowledge
Name and show up together in Manage Knowledge's tree. Leaving those
fields blank imports the collection unlinked, same idea as leaving
Domain/Module/Knowledge Name blank on a URL Knowledge capture.
"""

import json
from datetime import datetime
from pathlib import Path

from Database.db_manager import DatabaseManager
from Core.logger import Logger


# Mirrors discovery_repository.py's CAPTURED_FLOW_SOURCE_TYPE /
# CAPTURED_FLOW_DOCUMENT_TYPE pattern — marks a knowledge_items row
# that exists only to file an imported API collection under the
# Domain -> Module -> Knowledge Name -> Version tree, not an actual
# uploaded file. manage_knowledge_page.py checks this to render its
# children (endpoints) instead of a file preview.
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
    ):
        """
        Parses a Postman Collection .json export at `file_path` and
        stores every request it contains.

        Returns:
            {
                "success": bool,
                "collection_id": int | None,
                "knowledge_item_id": int | None,
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

            knowledge_item_id = self._get_or_create_linked_knowledge_item(
                domain, module, knowledge_name, version
            )

            summary["knowledge_item_id"] = knowledge_item_id

            collection_id = self._save_collection(
                name=collection_name,
                source_file_name=Path(file_path).name,
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=version,
                linked_knowledge_item_id=knowledge_item_id,
                variables=variables,
            )

            summary["collection_id"] = collection_id

            saved, skipped, skipped_detail = self._save_endpoints(
                collection_id, endpoints
            )

            summary["endpoints_saved"] = saved

            summary["endpoints_skipped"] = skipped

            summary["skipped_endpoints"] = skipped_detail

            summary["folders_found"] = folders_found

            summary["indexed_chunks"] = 0

            if knowledge_item_id:

                summary["indexed_chunks"] = self._index_collection_for_search(
                    collection_id=collection_id,
                    domain=domain,
                    module=module,
                    knowledge_name=knowledge_name,
                    version=version,
                    collection_name=collection_name,
                    source_file_name=Path(file_path).name,
                    endpoints=endpoints,
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

        if isinstance(url_field, dict):

            query_params = [
                {"key": q.get("key", ""), "value": q.get("value", "")}
                for q in (url_field.get("query") or [])
                if not q.get("disabled")
            ]

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
            "body_mode": body_mode,
            "body_raw": body_raw,
            "auth_type": auth_type,
            "auth_details": auth_details,
            "example_response_status": example_response_status,
            "example_response_body": example_response_body,
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
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            INSERT INTO api_collections
            (name, source_file_name, domain, module, knowledge_name,
             version, linked_knowledge_item_id, variables_json,
             status, created_date, modified_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?)
            """,
            (
                name,
                source_file_name,
                domain or "",
                module or "",
                knowledge_name or "",
                version or "",
                linked_knowledge_item_id,
                json.dumps(variables),
                now,
                now,
            ),
        )

        collection_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return collection_id

    def _save_endpoints(self, collection_id, endpoints):

        saved = 0

        skipped = 0

        skipped_detail = []

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

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
    ):
        try:

            from Core.document_chunker import DocumentChunker
            from Core.embedding_engine import EmbeddingEngine
            from Core.vector_store import VectorStore

            text = self._build_searchable_text(
                collection_name, domain, module, knowledge_name, endpoints
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
                        "document_type": API_COLLECTION_DOCUMENT_TYPE,
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
        self, collection_name, domain, module, knowledge_name, endpoints
    ):
        """
        Flattens the collection into readable text an embedding
        model and an LLM can both make sense of — same intent as
        Postman's own request list, just linearized.
        """

        lines = [
            f"API Collection: {collection_name}",
            (
                f"Scope: {domain or '(no domain)'} / "
                f"{module or '(no module)'} / "
                f"{knowledge_name or '(no knowledge name)'}"
            ),
            f"Total endpoints: {len(endpoints)}",
            "",
            "This collection defines the following API endpoints:",
            "",
        ]

        for endpoint in endpoints:

            url = endpoint.get("url_resolved") or endpoint.get("url_raw", "")

            lines.append(
                f"- {endpoint.get('method', '')} {url} "
                f"({endpoint.get('name', '')})"
            )

            if endpoint.get("folder_path"):

                lines.append(f"  Folder: {endpoint['folder_path']}")

            headers = endpoint.get("headers") or []

            if headers:

                header_text = ", ".join(
                    f"{h.get('key', '')}: {h.get('value', '')}"
                    for h in headers
                    if h.get("key")
                )

                if header_text:

                    lines.append(f"  Headers: {header_text}")

            if endpoint.get("body_raw"):

                body_preview = endpoint["body_raw"][:500]

                lines.append(f"  Body ({endpoint.get('body_mode', '')}): "
                             f"{body_preview}")

            lines.append("")

        return "\n".join(lines)

    def _get_or_create_linked_knowledge_item(
        self, domain, module, knowledge_name, version
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
        """

        domain = (domain or "").strip()

        module = (module or "").strip()

        knowledge_name = (knowledge_name or "").strip()

        version = (
            (version or "").strip() or DEFAULT_API_COLLECTION_VERSION
        )

        if not domain or not module or not knowledge_name:

            return None

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id FROM knowledge_items
            WHERE domain = ? AND module = ? AND knowledge_name = ?
              AND version = ? AND source_type = ?
            """,
            (domain, module, knowledge_name, version,
             API_COLLECTION_SOURCE_TYPE),
        )

        row = cursor.fetchone()

        if row:

            conn.close()

            return row[0]

        conn.close()

        from Core.metadata_manager import MetadataManager

        metadata_manager = MetadataManager()

        domain_id = metadata_manager.get_or_create_domain(domain)

        module_id = metadata_manager.get_or_create_module(domain, module)

        knowledge_path = f"{domain}/{module}/{knowledge_name}/{version}"

        import hashlib

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
                API_COLLECTION_DOCUMENT_TYPE,
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
        editor in Manage Knowledge (view + edit method/URL/headers/
        body of one imported request).
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
              AND c.domain = ? AND c.module = ?
              AND c.knowledge_name = ?
            ORDER BY e.id ASC
            """,
            (domain or "", module or "", knowledge_name or ""),
        )

        rows = cursor.fetchall()

        conn.close()

        return rows

    def get_endpoints_for_knowledge_item(self, knowledge_item_id):
        """
        The other direction of the link — given a Knowledge Hub
        node, returns the endpoints of whichever collection(s) are
        linked to it. Used by manage_knowledge_page.py to nest
        endpoints under the matching tree row.
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

    @staticmethod
    def _dict_factory(cursor, row):

        columns = [col[0] for col in cursor.description]

        return {
            columns[i]: row[i]
            for i in range(len(columns))
        }