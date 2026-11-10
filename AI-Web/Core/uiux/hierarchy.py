"""Cascading hierarchy selection for AI UI/UX Testing.

Order exposed to the user: Domain -> Module -> Knowledge -> Document Type -> Version -> Source Type -> Source Document.

This is a read-only adapter over the canonical Knowledge Hub ownership tables (domains, modules,
knowledge_entities, knowledge_document_types, document_versions) and knowledge_items. It never creates a second
ownership system; creating missing Knowledge / Document Type / Version nodes is delegated to the canonical
DocumentHierarchy service at registration time.
"""
from __future__ import annotations

import sqlite3

LEVELS = ("domain", "module", "knowledge_name", "document_type", "version", "source_type", "source_file")
LABELS = {"domain": "Domain", "module": "Module", "knowledge_name": "Knowledge", "document_type": "Document Type",
          "version": "Version", "source_type": "Source Type", "source_file": "Source Document"}


class HierarchyError(ValueError):
    """The selection is incomplete, ambiguous or does not exist."""


class Hierarchy:
    def __init__(self, db_path):
        self.db_path = str(db_path)

    def _conn(self):
        conn = sqlite3.connect(self.db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn

    def _catalog(self, conn, **filters):
        columns = {"domain": "d.name", "module": "m.name", "knowledge_name": "k.name", "document_type": "t.name", "version": "v.version"}
        conditions, values = [], []
        for key, column in columns.items():
            if filters.get(key) not in (None, ""):
                conditions.append(column + "=?")
                values.append(filters[key])
        sql = """SELECT d.id AS domain_id,d.name AS domain,m.id AS module_id,m.name AS module,
                 k.id AS knowledge_entity_id,k.name AS knowledge_name,t.id AS document_type_id,t.name AS document_type,
                 v.id AS document_version_id,v.version AS version
                 FROM domains d LEFT JOIN modules m ON m.domain_id=d.id
                 LEFT JOIN knowledge_entities k ON k.module_id=m.id
                 LEFT JOIN knowledge_document_types t ON t.knowledge_id=k.id
                 LEFT JOIN document_versions v ON v.document_type_id=t.id"""
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY d.name,m.name,k.name,t.name,v.id"
        try:
            return [dict(r) for r in conn.execute(sql, values)]
        except sqlite3.OperationalError:
            return []  # canonical hierarchy tables not created yet (fresh install)

    def options(self, level: str, selected: dict) -> list:
        """Options for ``level`` given the selections made at the levels above it (no cross-parent leakage)."""
        if level not in LEVELS:
            raise HierarchyError("Unknown hierarchy level.")
        idx = LEVELS.index(level)
        parents = {k: selected.get(k) for k in LEVELS[:idx]}
        if any(not parents[k] for k in LEVELS[:idx]):
            return []
        conn = self._conn()
        try:
            if level in ("domain", "module", "knowledge_name", "document_type", "version"):
                rows = self._catalog(conn, **{k: v for k, v in parents.items()})
                seen, out = set(), []
                for row in rows:
                    value = row.get(level)
                    if value in (None, "") or value in seen:
                        continue
                    seen.add(value)
                    out.append(value)
                return out
            version_id = self.resolve({k: selected.get(k) for k in LEVELS[:5]})["document_version_id"]
            if level == "source_type":
                rows = conn.execute("SELECT DISTINCT source_type FROM knowledge_items WHERE document_version_id=? AND source_type IS NOT NULL ORDER BY source_type", (version_id,))
                return [r[0] for r in rows]
            rows = conn.execute("SELECT id,file_name,source_file_id,extension FROM knowledge_items WHERE document_version_id=? AND source_type=? ORDER BY id",
                                (version_id, selected["source_type"]))
            return [{"knowledge_id": r["id"], "file_name": r["file_name"], "source_file_id": r["source_file_id"], "extension": r["extension"]} for r in rows]
        finally:
            conn.close()

    def resolve(self, selection: dict) -> dict:
        """Return the canonical identity for Domain..Version. Raises HierarchyError unless it is exactly one node."""
        missing = [LABELS[k] for k in LEVELS[:5] if not str(selection.get(k) or "").strip()]
        if missing:
            raise HierarchyError("Select " + ", ".join(missing) + " before continuing.")
        conn = self._conn()
        try:
            rows = [r for r in self._catalog(conn, **{k: selection[k] for k in LEVELS[:5]}) if r.get("document_version_id")]
        finally:
            conn.close()
        if not rows:
            raise HierarchyError("The selected Domain / Module / Knowledge / Document Type / Version combination does not exist in Knowledge Hub.")
        if len(rows) > 1:
            raise HierarchyError("The selected hierarchy is ambiguous; refresh the hierarchy and select again.")
        return rows[0]

    def identity_of_version(self, document_version_id: int) -> dict:
        conn = self._conn()
        try:
            row = conn.execute("""SELECT d.name AS domain,m.name AS module,k.name AS knowledge_name,t.name AS document_type,v.version AS version,v.id AS document_version_id
                FROM document_versions v JOIN knowledge_document_types t ON t.id=v.document_type_id
                JOIN knowledge_entities k ON k.id=t.knowledge_id JOIN modules m ON m.id=k.module_id JOIN domains d ON d.id=m.domain_id WHERE v.id=?""",
                                (int(document_version_id),)).fetchone()
        finally:
            conn.close()
        if not row:
            raise HierarchyError("The design's hierarchy no longer exists in Knowledge Hub.")
        return dict(row)

    def assert_same_version(self, document_version_id, *others):
        for other in others:
            if int(other) != int(document_version_id):
                raise HierarchyError("The items belong to different Knowledge Hub versions and cannot be combined.")
