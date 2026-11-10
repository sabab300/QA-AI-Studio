"""Additive discovery persistence. Construct only on an authorized request.

Explicit db_path is mandatory for isolated tests; no application imports then.
"""
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .evidence import DiscoveryError


class DiscoveryRepository:
    def __init__(self, db_path=None):
        if db_path is None:
            from Database.db_manager import DatabaseManager
            db_path = DatabaseManager().db_path
        self.db_path = db_path
        with self.connection() as c:
            c.executescript('''
                CREATE TABLE IF NOT EXISTS api_discovery_sessions (
                    id TEXT PRIMARY KEY, owner_id INTEGER NOT NULL, name TEXT NOT NULL,
                    website TEXT NOT NULL, mode TEXT NOT NULL, status TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    error TEXT NOT NULL DEFAULT '', analysis_json TEXT NOT NULL DEFAULT '{}'
                );
                CREATE TABLE IF NOT EXISTS api_discovery_evidence (
                    id INTEGER PRIMARY KEY, session_id TEXT NOT NULL
                        REFERENCES api_discovery_sessions(id) ON DELETE CASCADE,
                    endpoint_key TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_api_discovery_owner ON api_discovery_sessions(owner_id,created_at);
                CREATE INDEX IF NOT EXISTS idx_api_discovery_evidence ON api_discovery_evidence(session_id,endpoint_key);
                CREATE TABLE IF NOT EXISTS api_discovery_actions (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL
                      REFERENCES api_discovery_sessions(id) ON DELETE CASCADE,
                    status TEXT NOT NULL, payload_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS api_discovery_outputs (
                    id TEXT PRIMARY KEY, session_id TEXT NOT NULL
                      REFERENCES api_discovery_sessions(id) ON DELETE CASCADE,
                    kind TEXT NOT NULL, state TEXT NOT NULL, result_json TEXT NOT NULL DEFAULT '{}'
                );
            ''')
            # Older standalone evidence stores may not contain canonical
            # sources. Add the nullable FK only in the application database;
            # historical sessions remain readable and are never reassigned.
            c.execute('BEGIN IMMEDIATE')
            tables={row[0] for row in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            columns={row[1] for row in c.execute('PRAGMA table_info(api_discovery_sessions)')}
            if 'knowledge_items' in tables and 'source_id' not in columns:
                c.execute('ALTER TABLE api_discovery_sessions ADD COLUMN source_id INTEGER REFERENCES knowledge_items(id) ON DELETE RESTRICT')

    @contextmanager
    def connection(self):
        c = sqlite3.connect(self.db_path, timeout=10)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA foreign_keys=ON")
        try:
            with c:
                yield c
        finally:
            c.close()

    def create(self, owner, name, website, mode, evidence=(),source_id=None):
        identity = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        with self.connection() as c:
            c.execute("INSERT INTO api_discovery_sessions(id,owner_id,name,website,mode,status,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)",
                      (identity, owner, name, website, mode, "completed" if mode == "HAR" else "starting", now, now))
            c.executemany("INSERT INTO api_discovery_evidence(session_id,endpoint_key,payload_json) VALUES (?,?,?)",
                          [(identity, e["key"], json.dumps(e)) for e in evidence])
            if source_id is not None:
                if 'source_id' not in {row[1] for row in c.execute('PRAGMA table_info(api_discovery_sessions)')}:
                    raise DiscoveryError('Canonical source storage is unavailable.')
                c.execute('UPDATE api_discovery_sessions SET source_id=? WHERE id=?',(source_id,identity))
        return identity

    def get(self, identity, owner):
        with self.connection() as c:
            row = c.execute("SELECT * FROM api_discovery_sessions WHERE id=? AND owner_id=?", (identity, owner)).fetchone()
            if not row:
                raise KeyError("Discovery session does not exist or belongs to another user.")
            result = dict(row)
            result["analysis"] = json.loads(result.pop("analysis_json"))
            records = c.execute("SELECT id,payload_json FROM api_discovery_evidence WHERE session_id=? ORDER BY id LIMIT 2000", (identity,)).fetchall()
        result["captured_requests"] = len(records)
        grouped = {}
        for record in records:
            e = json.loads(record["payload_json"])
            if e["key"] not in grouped:
                grouped[e["key"]] = dict(e, evidence_ids=[], observed_statuses=[], occurrences=0)
            endpoint = grouped[e["key"]]
            endpoint["evidence_ids"].append(record["id"])
            endpoint["occurrences"] += 1
            endpoint["observed_statuses"] = sorted(set(endpoint["observed_statuses"] + [e["status"]]))
        result["endpoints"] = list(grouped.values())
        with self.connection() as c:
            result["actions"] = [dict(id=r["id"], status=r["status"], **json.loads(r["payload_json"]))
                                 for r in c.execute("SELECT * FROM api_discovery_actions WHERE session_id=? ORDER BY rowid LIMIT 200", (identity,))]
            result["outputs"] = [dict(id=r["id"], kind=r["kind"], state=r["state"], result=json.loads(r["result_json"]))
                                 for r in c.execute("SELECT * FROM api_discovery_outputs WHERE session_id=?", (identity,))]
        return result

    def action(self, identity, owner, payload, status="captured", action_id=None):
        self.get(identity, owner)
        action_id = action_id or uuid.uuid4().hex
        with self.connection() as c:
            if c.execute("SELECT count(*) FROM api_discovery_actions WHERE session_id=?", (identity,)).fetchone()[0] >= 200:
                raise DiscoveryError("Capture reached its 200-action resource limit.")
            c.execute("INSERT INTO api_discovery_actions(id,session_id,status,payload_json) VALUES (?,?,?,?)",
                      (action_id, identity, status, json.dumps(payload)))
        return action_id

    def action_status(self, identity, owner, action_id, status):
        self.get(identity, owner)
        with self.connection() as c:
            c.execute("UPDATE api_discovery_actions SET status=? WHERE id=? AND session_id=?", (status, action_id, identity))

    def review_action(self,identity,owner,action_id,control):
        self.get(identity,owner)
        with self.connection() as c:
            row=c.execute('SELECT payload_json FROM api_discovery_actions WHERE id=? AND session_id=? AND status=\'awaiting_confirmation\'',(action_id,identity)).fetchone()
            if not row:raise DiscoveryError('The proposed action is no longer awaiting review.')
            payload=json.loads(row[0]);payload['reviewed_control']=control
            c.execute('UPDATE api_discovery_actions SET payload_json=? WHERE id=?',(json.dumps(payload),action_id))

    def list(self, owner):
        with self.connection() as c:
            return [dict(r) for r in c.execute("SELECT id,name,website,mode,status,created_at,updated_at,error FROM api_discovery_sessions WHERE owner_id=? ORDER BY created_at DESC LIMIT 100", (owner,))]

    def page(self,owner,source_id,offset=0,limit=25,search='',status='',legacy=False):
        with self.connection() as c:
            where='s.owner_id=? AND (s.source_id=? OR (? AND s.source_id IS NULL)) AND s.name LIKE ? AND (?=\'\' OR s.status=?)'
            args=(owner,source_id,int(legacy),'%'+search[:150]+'%',status,status)
            total=c.execute('SELECT COUNT(*) FROM api_discovery_sessions s WHERE '+where,args).fetchone()[0]
            rows=c.execute('SELECT s.id,s.name,s.source_id,s.website,s.mode,s.status,s.created_at,s.updated_at,s.error,(SELECT COUNT(*) FROM api_discovery_evidence e WHERE e.session_id=s.id) AS captured_requests,(SELECT COUNT(DISTINCT endpoint_key) FROM api_discovery_evidence e WHERE e.session_id=s.id) AS unique_apis FROM api_discovery_sessions s WHERE '+where+' ORDER BY s.created_at DESC LIMIT ? OFFSET ?',args+(min(100,max(1,limit)),max(0,offset))).fetchall()
            return {'sessions':[dict(row) for row in rows],'total':total}

    def evidence(self,identity,owner,evidence_id):
        with self.connection() as c:
            row=c.execute('SELECT e.payload_json FROM api_discovery_evidence e JOIN api_discovery_sessions s ON s.id=e.session_id WHERE e.session_id=? AND s.owner_id=? AND e.id=?',(identity,owner,evidence_id)).fetchone()
            if not row:raise KeyError('Evidence not found for current owner.')
            return json.loads(row[0])

    def append(self, identity, owner, evidence):
        with self.connection() as c:
            row = c.execute("SELECT status FROM api_discovery_sessions WHERE id=? AND owner_id=?", (identity, owner)).fetchone()
            if not row or row[0] != "recording":
                return
            count = c.execute("SELECT count(*) FROM api_discovery_evidence WHERE session_id=?", (identity,)).fetchone()[0]
            if count >= 2000:
                raise DiscoveryError("Capture reached the 2,000 request limit.")
            c.execute("INSERT INTO api_discovery_evidence(session_id,endpoint_key,payload_json) VALUES (?,?,?)", (identity, evidence["key"], json.dumps(evidence)))

    def update(self, identity, owner, status, error="", analysis=None):
        with self.connection() as c:
            args = [status, error, datetime.now(timezone.utc).isoformat()]
            fields = "status=?,error=?,updated_at=?"
            if analysis is not None:
                fields += ",analysis_json=?"
                args.append(json.dumps(analysis))
            c.execute("UPDATE api_discovery_sessions SET " + fields + " WHERE id=? AND owner_id=?", args + [identity, owner])

    def delete(self, identity, owner):
        self.get(identity, owner)
        with self.connection() as c:
            if c.execute("SELECT 1 FROM api_discovery_outputs WHERE session_id=? LIMIT 1", (identity,)).fetchone():
                raise DiscoveryError("This discovery has canonical output references. Preserve its traceability; deletion is blocked.")
            c.execute("DELETE FROM api_discovery_sessions WHERE id=? AND owner_id=?", (identity, owner))

    def claim_output(self, identity, owner, key, kind):
        self.get(identity, owner)
        with self.connection() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute("SELECT * FROM api_discovery_outputs WHERE id=? AND session_id=?", (key, identity)).fetchone()
            if row:
                if row["state"] == "completed":
                    return json.loads(row["result_json"])
                raise DiscoveryError("This operation is already pending or needs reconciliation. No duplicate output was created; inspect the existing canonical records before retrying.")
            c.execute("INSERT INTO api_discovery_outputs(id,session_id,kind,state) VALUES (?,?,?,'pending')", (key, identity, kind))
        return None

    def finish_output(self, key, state, result):
        with self.connection() as c:
            c.execute("UPDATE api_discovery_outputs SET state=?,result_json=? WHERE id=?", (state,json.dumps(result),key))

    def reconcile_interrupted(self):
        # Call once on application startup, never per request: a live worker
        # must not be marked interrupted by another authenticated request.
        with self.connection() as c:
            c.execute("UPDATE api_discovery_sessions SET status='interrupted', error=? WHERE status IN ('starting','recording','paused','stopping')",
                      ("Collection was interrupted by server shutdown. Committed evidence remains available; start a new browser to collect more.",))
