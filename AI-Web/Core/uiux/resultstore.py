"""Persistence of per-check results and of the ClickUp tickets created from them (tables ``uiux_results`` and ``uiux_clickup_links``).

Results are written once, when a screen finishes, and never regenerated: the report, the evidence viewer, the Excel export
and History all read these saved rows, so they stay identical after a restart.
"""
from __future__ import annotations

import sqlite3

from . import results as R
from .util import dumps, loads, new_id, now_iso

PUBLIC_KEYS = ("id", "run_id", "seq", "test_id", "user_category", "category_label", "name", "expected", "actual", "result", "reason", "severity", "confidence", "source", "screen", "viewport",
               "member_count", "finding_fingerprint", "design_name", "design_version", "target_url", "executed_at")


class ResultStore:
    def __init__(self, db):
        self.db = db

    # ------------------------------------------------------------------ results
    def store(self, run: dict, rows: list, screen: str = "", viewport: str = "") -> int:
        """Append rows to the run. ``run`` supplies design/target context; returns the number stored."""
        if not rows:
            return 0
        from . import categories as UC
        with self.db.connect() as c:
            seq = (c.execute("SELECT COALESCE(MAX(seq),0) FROM uiux_results WHERE run_id=?", (run["run_id"],)).fetchone()[0])
            short = run["run_id"].split("-")[-1]
            for row in rows:
                seq += 1
                c.execute("""INSERT INTO uiux_results(id,run_id,seq,test_id,user_category,category_label,name,expected,actual,result,reason,severity,confidence,source,screen,viewport,
                             member_count,members_json,evidence_json,finding_fingerprint,design_name,design_version,target_url,executed_at)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (new_id("RS"), run["run_id"], seq, f"UX-{short}-{seq:04d}", row["user_category"], UC.LABELS.get(row["user_category"], row["user_category"]), row["name"][:300],
                           str(row["expected"])[:600], str(row["actual"])[:600], row["result"], row["reason"][:1000], row.get("severity", ""), float(row.get("confidence") or 0), row.get("source", ""),
                           screen or row.get("screen", ""), viewport or row.get("viewport", ""), int(row.get("member_count", 1)), dumps(row.get("members", [])), dumps(row.get("evidence", {})),
                           row.get("finding_fingerprint", ""), run.get("source_file") or run.get("knowledge_name", ""), run.get("version", ""), run.get("app_url", ""), now_iso()))
        return len(rows)

    @staticmethod
    def _public(row, detail=False):
        d = {k: row[k] for k in PUBLIC_KEYS}
        d["evidence"] = loads(row["evidence_json"], {})
        if detail:
            d["members"] = loads(row["members_json"], [])
        return d

    def _ticket_join(self, c, run_id: str) -> dict:
        out = {}
        for r in c.execute("SELECT * FROM uiux_clickup_links WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall():
            slot = out.setdefault(r["result_id"], [])
            slot.append(self._link_public(r))
        return out

    @staticmethod
    def _link_public(r):
        d = {k: r[k] for k in ("id", "run_id", "result_id", "config_id", "list_id", "task_type", "task_id", "task_url", "task_title", "task_status", "outcome", "error", "created_by", "created_at")}
        d["attachments"] = loads(r["attachments_json"], [])
        return d

    @staticmethod
    def ticket_state(links: list) -> dict:
        created = [x for x in links if x["outcome"] == "Created"]
        if created:
            last = created[-1]
            return {"ticket_status": "Created", "ticket": last, "tickets": created}
        if links:
            return {"ticket_status": "Failed", "ticket": links[-1], "tickets": []}
        return {"ticket_status": "Not Created", "ticket": None, "tickets": []}

    def list(self, run_id: str, result: str = "", category: str = "", severity: str = "", ticket_status: str = "", text: str = "", limit: int = 100, offset: int = 0) -> dict:
        """Default order: FAIL, Needs Review, Not Verifiable, PASS, Not Applicable, Not Executed (then test order)."""
        order = "CASE result " + " ".join(f"WHEN '{k}' THEN {v}" for k, v in R.SORT_ORDER.items()) + " ELSE 9 END"
        with self.db.connect() as c:
            where, args = ["run_id=?"], [run_id]
            if result:
                where.append("result=?")
                args.append(result)
            if category:
                where.append("user_category=?")
                args.append(category)
            if severity:
                where.append("severity=?")
                args.append(severity)
            if text:
                like = "%" + text.replace("%", "").replace("_", "")[:80] + "%"
                where.append("(name LIKE ? OR expected LIKE ? OR actual LIKE ? OR reason LIKE ? OR test_id LIKE ?)")
                args += [like] * 5
            rows = c.execute(f"SELECT * FROM uiux_results WHERE {' AND '.join(where)} ORDER BY {order}, seq", args).fetchall()
            tickets = self._ticket_join(c, run_id)
        items = []
        for r in rows:
            d = self._public(r)
            d.update(self.ticket_state(tickets.get(r["id"], [])))
            if ticket_status and d["ticket_status"] != ticket_status:
                continue
            items.append(d)
        total = len(items)
        return {"items": items[offset:offset + limit], "total": total, "offset": offset, "limit": limit}

    def all(self, run_id: str) -> list:
        """Every result of a run in test order with its members and tickets (used by exports)."""
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM uiux_results WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
            tickets = self._ticket_join(c, run_id)
        out = []
        for r in rows:
            d = self._public(r, detail=True)
            d.update(self.ticket_state(tickets.get(r["id"], [])))
            out.append(d)
        return out

    def get(self, result_id: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM uiux_results WHERE id=?", (result_id,)).fetchone()
            if not row:
                raise LookupError("The result does not exist.")
            links = [self._link_public(r) for r in c.execute("SELECT * FROM uiux_clickup_links WHERE result_id=? ORDER BY created_at", (result_id,)).fetchall()]
        d = self._public(row, detail=True)
        d.update(self.ticket_state(links))
        return d

    def counts(self, run_id: str) -> dict:
        with self.db.connect() as c:
            by_status = {s: 0 for s in R.STATUSES}
            for r in c.execute("SELECT result,COUNT(*) n FROM uiux_results WHERE run_id=? GROUP BY result", (run_id,)):
                by_status[r["result"]] = r["n"]
            cats = {}
            for r in c.execute("SELECT user_category,category_label,result,COUNT(*) n FROM uiux_results WHERE run_id=? GROUP BY user_category,result", (run_id,)):
                row = cats.setdefault(r["user_category"], {"label": r["category_label"], **{s: 0 for s in R.STATUSES}})
                row[r["result"]] = r["n"]
            elements = c.execute("SELECT COALESCE(SUM(member_count),0) FROM uiux_results WHERE run_id=? AND result IN ('PASS','FAIL','Needs Review')", (run_id,)).fetchone()[0]
        executed = by_status[R.PASS] + by_status[R.FAIL] + by_status[R.REVIEW]
        return {"by_status": by_status, "by_category": cats, "total_rows": sum(by_status.values()), "checks_executed": executed, "element_comparisons": int(elements)}

    def history_counts(self, run_ids: list) -> dict:
        if not run_ids:
            return {}
        with self.db.connect() as c:
            out = {rid: {s: 0 for s in R.STATUSES} for rid in run_ids}
            marks = ",".join("?" * len(run_ids))
            for r in c.execute(f"SELECT run_id,result,COUNT(*) n FROM uiux_results WHERE run_id IN ({marks}) GROUP BY run_id,result", run_ids):
                out[r["run_id"]][r["result"]] = r["n"]
            tickets = {rid: 0 for rid in run_ids}
            for r in c.execute(f"SELECT run_id,COUNT(*) n FROM uiux_clickup_links WHERE run_id IN ({marks}) AND outcome='Created' GROUP BY run_id", run_ids):
                tickets[r["run_id"]] = r["n"]
        return {rid: {"counts": out[rid], "tickets": tickets[rid]} for rid in run_ids}

    # ------------------------------------------------------------------ ClickUp links
    def find_by_key(self, key: str):
        with self.db.connect() as c:
            r = c.execute("SELECT * FROM uiux_clickup_links WHERE idempotency_key=?", (key,)).fetchone()
        return self._link_public(r) if r else None

    def add_link(self, run_id: str, result_id: str, key: str, config_id: int, list_id: str, task_type: str, outcome: str, user: str, task_id="", task_url="", title="", status="", error="",
                 attachments=None) -> dict:
        link_id = new_id("CU")
        try:
            with self.db.connect() as c:
                c.execute("""INSERT INTO uiux_clickup_links(id,run_id,result_id,idempotency_key,config_id,list_id,task_type,task_id,task_url,task_title,task_status,outcome,error,attachments_json,created_by,created_at)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                          (link_id, run_id, result_id, key, int(config_id or 0), list_id, task_type, task_id, task_url, title[:300], status, outcome, error[:600], dumps(attachments or []), user, now_iso()))
        except sqlite3.IntegrityError:
            existing = self.find_by_key(key)
            if existing:
                return existing
            raise
        return self.find_by_key(key)

    def update_link(self, key: str, **fields):
        cols = {"outcome": "outcome", "error": "error", "task_id": "task_id", "task_url": "task_url", "task_status": "task_status", "attachments": "attachments_json", "task_title": "task_title"}
        sets, vals = [], []
        for k, v in fields.items():
            sets.append(f"{cols[k]}=?")
            vals.append(dumps(v) if k == "attachments" else v)
        with self.db.connect() as c:
            c.execute(f"UPDATE uiux_clickup_links SET {','.join(sets)} WHERE idempotency_key=?", vals + [key])
        return self.find_by_key(key)

    def links_for_run(self, run_id: str) -> list:
        with self.db.connect() as c:
            return [self._link_public(r) for r in c.execute("SELECT * FROM uiux_clickup_links WHERE run_id=? ORDER BY created_at", (run_id,)).fetchall()]

    # ------------------------------------------------------------------ history (all runs, newest first)
    def history(self, limit: int = 100, text: str = "") -> list:
        with self.db.connect() as c:
            sql = ("SELECT run_id,created_at,started_at,ended_at,source_file,knowledge_name,version,app_url,status,status_detail,document_version_id,asset_id,kind,engine_version,config_json,summary_json "
                   "FROM uiux_runs")
            args = []
            if text:
                like = "%" + text.replace("%", "").replace("_", "")[:80] + "%"
                sql += " WHERE source_file LIKE ? OR app_url LIKE ? OR knowledge_name LIKE ? OR run_id LIKE ?"
                args += [like] * 4
            rows = c.execute(sql + " ORDER BY created_at DESC LIMIT ?", args + [int(limit)]).fetchall()
        ids = [r["run_id"] for r in rows]
        extra = self.history_counts(ids)
        out = []
        for r in rows:
            cfg, summ = loads(r["config_json"], {}), loads(r["summary_json"], {})
            path = next((u.get("page", {}).get("path", "") for u in (summ.get("units") or []) if u.get("status") == "Tested"), "")
            d = {k: r[k] for k in ("run_id", "created_at", "started_at", "ended_at", "source_file", "knowledge_name", "version", "app_url", "status", "status_detail", "document_version_id", "asset_id", "kind", "engine_version")}
            d["website"] = (r["app_url"] or "") + (path if path and path != "/" else "")
            d["counts"] = extra[r["run_id"]]["counts"]
            d["tickets"] = extra[r["run_id"]]["tickets"]
            d["has_results"] = sum(d["counts"].values()) > 0
            d["categories"] = len(cfg.get("user_categories") or [])
            out.append(d)
        return out
