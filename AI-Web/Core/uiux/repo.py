"""Persistence and lifecycle rules for AI UI/UX Testing (everything except design registration and the run engine)."""
from __future__ import annotations

import json
import re

from . import ENGINE_VERSION
from .findings import snapshot
from .hierarchy import Hierarchy
from .schema import UiuxDatabase
from .security import ENVIRONMENTS, TargetError, authorize, classify_ip, normalize_url, origin_of, resolve_host
from .storage import Storage
from .util import dumps, loads, new_id, new_run_id, now_iso, sha256_bytes

REVIEW_DECISIONS = ("Confirmed", "Not Applicable", "Needs Investigation")
MAPPING_STATUSES = ("Suggested", "Approved", "Rejected")
RUN_ACTIVE = ("Queued", "Running", "Paused")
RUN_FINAL = ("Completed", "Partial", "Failed", "Blocked", "Cancelled", "Interrupted")
BASELINE_FLOW = {"Draft": ("In Review", "Rejected"), "In Review": ("Approved", "Rejected", "Draft"), "Approved": ("Active", "Rejected"), "Active": ("Superseded",), "Superseded": (), "Rejected": ()}
VIEWPORT_PRESETS = {"desktop": (1440, 900), "tablet": (768, 1024), "mobile": (375, 812)}


class RepoError(ValueError):
    """A rule was violated; the message is safe to show to the user."""


def _row(row):
    return dict(row) if row is not None else None


class UiuxRepo:
    def __init__(self, db: UiuxDatabase, hierarchy: Hierarchy, storage: Storage):
        self.db, self.hierarchy, self.storage = db, hierarchy, storage

    # ================================================================= authorized targets
    def list_targets(self, include_inactive=False) -> list:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM uiux_targets" + ("" if include_inactive else " WHERE active=1") + " ORDER BY name").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["extra_origins"] = loads(d.pop("extra_origins_json"), [])
            d["internal_ok"] = bool(d["internal_ok"])
            d["active"] = bool(d["active"])
            out.append(d)
        return out

    def target_records(self) -> list:
        with self.db.connect() as c:
            return [dict(r) for r in c.execute("SELECT * FROM uiux_targets WHERE active=1").fetchall()]

    def get_target(self, target_id: str) -> dict:
        for t in self.list_targets(include_inactive=True):
            if t["id"] == target_id:
                return t
        raise RepoError("The application target does not exist.")

    def add_target(self, name: str, url: str, environment: str, internal_ok: bool, extra_origins: list, user: str, note: str = "", resolver=None) -> dict:
        name = (name or "").strip()[:120]
        if not name:
            raise RepoError("Give the application target a name.")
        if environment not in ENVIRONMENTS:
            raise RepoError("Select an environment: " + ", ".join(ENVIRONMENTS) + ".")
        try:
            origin = origin_of(normalize_url(url))
            extras = []
            for item in (extra_origins or [])[:6]:
                if str(item).strip():
                    extras.append(origin_of(normalize_url(item)))
            # validate now so an unsafe target is refused when it is authorized, not only when it runs
            probe = {"id": "probe", "origin": origin, "environment": environment, "internal_ok": 1 if internal_ok else 0, "extra_origins_json": dumps(extras), "active": 1}
            try:
                authorize(origin + "/", environment, [probe], resolver)
            except TargetError as error:
                if "could not be resolved" not in str(error):
                    raise
        except TargetError as error:
            raise RepoError(str(error)) from None
        tid = new_id("TGT")
        try:
            with self.db.connect() as c:
                c.execute("INSERT INTO uiux_targets(id,name,origin,environment,internal_ok,extra_origins_json,authorized_by,authorized_at,note,active) VALUES(?,?,?,?,?,?,?,?,?,1)",
                          (tid, name, origin, environment, 1 if internal_ok else 0, dumps(extras), user, now_iso(), (note or "")[:300]))
        except Exception as error:
            if "UNIQUE" in str(error):
                raise RepoError(f"{origin} is already authorized for {environment}.") from None
            raise
        return self.get_target(tid)

    def deactivate_target(self, target_id: str):
        self.get_target(target_id)
        with self.db.connect() as c:
            c.execute("UPDATE uiux_targets SET active=0 WHERE id=?", (target_id,))

    # ================================================================= mappings
    def _mapping_public(self, row):
        d = dict(row)
        for k in ("evidence", "element_map", "masks", "states"):
            d[k] = loads(d.pop(k + "_json"), {} if k in ("evidence", "element_map") else [])
        return d

    def create_mapping(self, asset_id: str, frame_id: str, target_id: str, route: str, viewport: tuple | None, user: str, method="manual", confidence=1.0, evidence=None) -> dict:
        with self.db.connect() as c:
            asset = c.execute("SELECT id,document_version_id FROM uiux_design_assets WHERE id=?", (asset_id,)).fetchone()
            frame = c.execute("SELECT id,asset_id,width,height,viewport_class FROM uiux_frames WHERE id=?", (frame_id,)).fetchone()
        if not asset or not frame or frame["asset_id"] != asset["id"]:
            raise RepoError("The frame does not belong to the selected design.")
        if target_id:
            self.get_target(target_id)
        route = self.clean_route(route)
        if viewport is None:
            viewport = (int(round(frame["width"])), min(int(round(frame["height"])), 1200))
        vw, vh = int(viewport[0]), int(viewport[1])
        if not (200 <= vw <= 3840 and 200 <= vh <= 5000):
            raise RepoError("Choose a viewport between 200×200 and 3840×5000.")
        mid = new_id("MAP")
        now = now_iso()
        status = "Approved" if method == "manual" else "Suggested"
        with self.db.connect() as c:
            dup = c.execute("SELECT id FROM uiux_mappings WHERE frame_id=? AND target_id=? AND route=? AND viewport_w=? AND status!='Rejected'", (frame_id, target_id, route, vw)).fetchone()
            if dup:
                raise RepoError("This frame is already mapped to that page and viewport.")
            c.execute("""INSERT INTO uiux_mappings(id,document_version_id,asset_id,frame_id,target_id,route,viewport_w,viewport_h,viewport_name,confidence,status,method,evidence_json,
                created_by,created_at,approved_by,approved_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (mid, asset["document_version_id"], asset_id, frame_id, target_id or "", route, vw, vh, frame["viewport_class"], float(confidence), status, method, dumps(evidence or {}),
                       user, now, user if status == "Approved" else "", now if status == "Approved" else None, now))
        return self.get_mapping(mid)

    @staticmethod
    def clean_route(route: str) -> str:
        route = (route or "/").strip()
        if re.match(r"^[a-z][a-z0-9+.-]*://", route, re.I) or route.startswith("//"):
            raise RepoError("Enter a path on the authorized application (for example /crf), not a full URL.")
        if re.search(r"[\s\x00-\x1f\\]", route) or len(route) > 300:
            raise RepoError("The page path contains invalid characters.")
        return route if route.startswith("/") else "/" + route

    def get_mapping(self, mapping_id: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM uiux_mappings WHERE id=?", (mapping_id,)).fetchone()
            if not row:
                raise RepoError("The mapping does not exist.")
            d = self._mapping_public(row)
            f = c.execute("SELECT name,width,height,viewport_class FROM uiux_frames WHERE id=?", (d["frame_id"],)).fetchone()
            d["frame_name"], d["frame_width"], d["frame_height"] = (f["name"], f["width"], f["height"]) if f else ("", 0, 0)
        return d

    def list_mappings(self, document_version_id: int, include_rejected=False) -> list:
        with self.db.connect() as c:
            rows = c.execute("SELECT id FROM uiux_mappings WHERE document_version_id=?" + ("" if include_rejected else " AND status!='Rejected'") + " ORDER BY created_at", (int(document_version_id),)).fetchall()
        return [self.get_mapping(r["id"]) for r in rows]

    def update_mapping(self, mapping_id: str, user: str, route=None, viewport=None, masks=None, states=None, target_id=None) -> dict:
        m = self.get_mapping(mapping_id)
        fields, values = [], []
        if route is not None:
            fields.append("route=?"); values.append(self.clean_route(route))
        if viewport is not None:
            vw, vh = int(viewport[0]), int(viewport[1])
            if not (200 <= vw <= 3840 and 200 <= vh <= 5000):
                raise RepoError("Choose a viewport between 200×200 and 3840×5000.")
            fields += ["viewport_w=?", "viewport_h=?"]; values += [vw, vh]
        if masks is not None:
            clean = []
            for item in masks[:30]:
                if isinstance(item, dict) and "selector" in item:
                    clean.append({"selector": str(item["selector"])[:200], "reason": str(item.get("reason", ""))[:120]})
                elif isinstance(item, dict) and all(k in item for k in ("x", "y", "w", "h")):
                    clean.append({k: float(item[k]) for k in ("x", "y", "w", "h")} | {"reason": str(item.get("reason", ""))[:120]})
            fields.append("masks_json=?"); values.append(dumps(clean))
        if states is not None:
            fields.append("states_json=?"); values.append(dumps(self._clean_states(states)))
        if target_id is not None:
            if target_id:
                self.get_target(target_id)
            fields.append("target_id=?"); values.append(target_id)
        if not fields:
            return m
        # a change to what is compared invalidates a previous approval: it must be re-approved
        status = "Suggested" if m["status"] == "Approved" and (route is not None or viewport is not None or target_id is not None) else m["status"]
        fields += ["status=?", "updated_at=?"]; values += [status, now_iso()]
        with self.db.connect() as c:
            c.execute(f"UPDATE uiux_mappings SET {','.join(fields)} WHERE id=?", values + [mapping_id])
        return self.get_mapping(mapping_id)

    @staticmethod
    def _clean_states(states: list) -> list:
        out = []
        for s in (states or [])[:10]:
            if not isinstance(s, dict) or s.get("name") not in ("default", "hover", "focus", "disabled", "loading", "error", "success", "modal", "dropdown"):
                continue
            out.append({"name": s["name"], "selector": str(s.get("selector", ""))[:200], "frame_id": str(s.get("frame_id", ""))[:120],
                        "actions": [a for a in (s.get("actions") or [])[:6] if isinstance(a, dict) and a.get("action") in ("hover", "focus", "click", "press", "wait")]})
        return out

    def set_status(self, mapping_id: str, status: str, user: str) -> dict:
        if status not in MAPPING_STATUSES:
            raise RepoError("Unknown mapping status.")
        self.get_mapping(mapping_id)
        with self.db.connect() as c:
            c.execute("UPDATE uiux_mappings SET status=?,approved_by=?,approved_at=?,updated_at=? WHERE id=?",
                      (status, user if status == "Approved" else "", now_iso() if status == "Approved" else None, now_iso(), mapping_id))
        return self.get_mapping(mapping_id)

    def set_element_map(self, mapping_id: str, design_node_id: str, selector: str | None, user: str) -> dict:
        m = self.get_mapping(mapping_id)
        emap = dict(m["element_map"])
        if selector:
            if len(selector) > 300:
                raise RepoError("The selector is too long.")
            emap[design_node_id] = selector
        else:
            emap.pop(design_node_id, None)
        with self.db.connect() as c:
            c.execute("UPDATE uiux_mappings SET element_map_json=?,updated_at=? WHERE id=?", (dumps(emap), now_iso(), mapping_id))
        return self.get_mapping(mapping_id)

    def delete_mapping(self, mapping_id: str):
        self.get_mapping(mapping_id)
        with self.db.connect() as c:
            used = c.execute("SELECT COUNT(*) FROM uiux_runs WHERE config_json LIKE ?", (f'%"{mapping_id}"%',)).fetchone()[0]
            if used:
                c.execute("UPDATE uiux_mappings SET status='Rejected',updated_at=? WHERE id=?", (now_iso(), mapping_id))   # keep history of runs that used it
            else:
                c.execute("DELETE FROM uiux_mappings WHERE id=?", (mapping_id,))

    # ================================================================= configurations
    def save_config(self, document_version_id: int, asset_id: str, name: str, config: dict, user: str, config_id: str = "") -> dict:
        name = (name or "").strip()[:120]
        if not name:
            raise RepoError("Give the configuration a name.")
        clean = self.clean_config(config)
        now = now_iso()
        with self.db.connect() as c:
            if config_id:
                row = c.execute("SELECT id FROM uiux_configs WHERE id=? AND document_version_id=?", (config_id, int(document_version_id))).fetchone()
                if not row:
                    raise RepoError("The configuration does not exist for this version.")
                c.execute("UPDATE uiux_configs SET name=?,config_json=?,updated_at=? WHERE id=?", (name, dumps(clean), now, config_id))
            else:
                config_id = new_id("CFG")
                c.execute("INSERT INTO uiux_configs(id,document_version_id,asset_id,name,config_json,created_by,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?)",
                          (config_id, int(document_version_id), asset_id or "", name, dumps(clean), user, now, now))
        return self.get_config(config_id)

    def get_config(self, config_id: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM uiux_configs WHERE id=?", (config_id,)).fetchone()
        if not row:
            raise RepoError("The configuration does not exist.")
        d = dict(row)
        d["config"] = loads(d.pop("config_json"), {})
        return d

    def list_configs(self, document_version_id: int) -> list:
        with self.db.connect() as c:
            rows = c.execute("SELECT id FROM uiux_configs WHERE document_version_id=? ORDER BY updated_at DESC", (int(document_version_id),)).fetchall()
        return [self.get_config(r["id"]) for r in rows]

    def delete_config(self, config_id: str):
        self.get_config(config_id)
        with self.db.connect() as c:
            c.execute("DELETE FROM uiux_configs WHERE id=?", (config_id,))

    @staticmethod
    def clean_config(config: dict) -> dict:
        from . import categories as UC
        from .structure import CATEGORIES, Tolerances
        config = config or {}
        ui = bool(config.get("ui_categories"))
        if ui:
            # one-click workflow: the user picks the 14 user categories; the engine categories that must run are derived from them
            chosen = [k for k in (config.get("user_categories") or config.get("categories") or []) if k in UC.KEYS]
            chosen = [k for k in UC.KEYS if k in set(chosen)]
            if not chosen:
                raise RepoError("Select at least one test category.")
            wanted = UC.engine_categories(chosen)
            cats = [x for x in CATEGORIES if x in wanted]
        else:
            chosen = []
            cats = [x for x in (config.get("categories") or CATEGORIES) if x in CATEGORIES]
            if not cats:
                raise RepoError("Select at least one test category.")
        bad = Tolerances.problems(config.get("tolerances")) if config.get("strict_tolerances") else []
        if bad:
            raise RepoError("Check the tolerances: " + " ".join(bad[:3]))
        env = config.get("environment", "QA")
        if env not in ENVIRONMENTS:
            raise RepoError("Select a valid environment.")
        browser = config.get("browser", "chromium")
        if browser != "chromium":
            raise RepoError("Only Chromium is supported in this release.")
        auth = config.get("auth") or {"type": "none"}
        if auth.get("type") not in ("none", "stored"):
            raise RepoError("Credentials are never saved in a configuration. Store them with 'Application credentials' and select 'Use stored credentials'.")
        tol = Tolerances.from_dict(config.get("tolerances"))
        return {
            "target_id": str(config.get("target_id", ""))[:60], "environment": env, "build_label": str(config.get("build_label", ""))[:80],
            "browser": browser, "mapping_ids": [str(x)[:60] for x in (config.get("mapping_ids") or [])][:40],
            "categories": cats, "user_categories": chosen, "ui_categories": ui, "tolerances": {k: getattr(tol, k) for k in tol.__dataclass_fields__},
            "visual": {"color_threshold": float((config.get("visual") or {}).get("color_threshold", 28)), "shift": int((config.get("visual") or {}).get("shift", 1))},
            "auth": {"type": auth.get("type", "none")}, "ai": {"enabled": bool((config.get("ai") or {}).get("enabled", False))},
            "scope": str(config.get("scope", "selected"))[:30], "colour_scheme": "dark" if config.get("colour_scheme") == "dark" else "light",
            "baseline_id": str(config.get("baseline_id", ""))[:60], "timeout_s": max(30, min(int(config.get("timeout_s", 300)), 1800)),
        }

    # ================================================================= runs
    def create_run(self, document_version_id: int, asset_id: str, config: dict, user: str, kind="full", parent_run_id="", config_id="") -> dict:
        ident = self.hierarchy.identity_of_version(document_version_id)
        clean = self.clean_config(config) if kind != "retry" else config
        if not clean["mapping_ids"]:
            raise RepoError("Select at least one approved screen mapping to test.")
        if kind != "retry":
            clean["target_id"] = self.resolve_target_id(clean, document_version_id)
        with self.db.connect() as c:
            asset = c.execute("SELECT * FROM uiux_design_assets WHERE id=?", (asset_id,)).fetchone()
            if not asset or asset["document_version_id"] != int(document_version_id):
                raise RepoError("The design does not belong to the selected version.")
            for mid in clean["mapping_ids"]:
                m = c.execute("SELECT document_version_id,status,asset_id FROM uiux_mappings WHERE id=?", (mid,)).fetchone()
                if not m or m["document_version_id"] != int(document_version_id) or m["asset_id"] != asset_id:
                    raise RepoError("A selected mapping belongs to a different design or version.")
                if m["status"] == "Rejected":
                    raise RepoError("A selected mapping was rejected.")
            running = c.execute("SELECT COUNT(*) FROM uiux_runs WHERE status IN ('Queued','Running','Paused') AND document_version_id=?", (int(document_version_id),)).fetchone()[0]
            if running:
                raise RepoError("A run for this version is already in progress. Wait for it to finish or stop it first.")
            active = c.execute("SELECT id FROM uiux_baselines WHERE document_version_id=? AND asset_id=? AND status='Active'", (int(document_version_id), asset_id)).fetchone()
            run_id = new_run_id()
            clean["baseline_id"] = clean.get("baseline_id") or (active["id"] if active else "")
            now = now_iso()
            c.execute("""INSERT INTO uiux_runs(run_id,document_version_id,domain,module,knowledge_name,document_type,version,source_type,source_file,asset_id,baseline_id,config_id,
                app_url,environment,build_label,browser,viewports_json,kind,parent_run_id,status,created_at,created_by,config_json,engine_version)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (run_id, int(document_version_id), ident["domain"], ident["module"], ident["knowledge_name"], ident["document_type"], ident["version"], asset["source_type"], asset["source_file"],
                       asset_id, clean["baseline_id"], config_id, "", clean["environment"], clean["build_label"], clean["browser"], "[]", kind, parent_run_id, "Queued", now, user,
                       dumps(clean), ENGINE_VERSION))
        return self.get_run(run_id)

    def resolve_target_id(self, clean: dict, document_version_id: int) -> str:
        """The application target a run will use. Resolved when the run is created so a missing target is a clear error now,
        not a 'Blocked' run later. An explicit target_id wins; otherwise the one target the selected screens point at; otherwise the
        only active target for the environment. Never guesses between several."""
        tid = clean.get("target_id") or ""
        if tid:
            self.get_target(tid)     # an unknown id is a clear error now; a removed one still creates a run that ends 'Blocked' with an explanation
            return tid
        with self.db.connect() as c:
            marks = ",".join("?" * len(clean["mapping_ids"]))
            rows = c.execute(f"SELECT DISTINCT target_id FROM uiux_mappings WHERE id IN ({marks}) AND target_id!=''", clean["mapping_ids"]).fetchall()
        mapped = {r["target_id"] for r in rows}
        active = {t["id"] for t in self.list_targets()}
        mapped &= active
        if len(mapped) == 1:
            return mapped.pop()
        env_targets = [t["id"] for t in self.list_targets() if t["environment"] == clean["environment"]]
        if not mapped and len(env_targets) == 1:
            return env_targets[0]
        raise RepoError("Enter the target website URL (for example http://localhost:8000) and confirm you are authorized to test it." if not mapped
                        else "The selected screens point at different application targets. Choose one target.")

    # ------------------------------------------------------------------ run steps (the observable trail behind live view and replay)
    def add_step(self, run_id: str, kind: str, message: str, unit="", category="", status="info", page_url="", evidence_id="", meta=None) -> int:
        with self.db.connect() as c:
            seq = (c.execute("SELECT COALESCE(MAX(seq),0) FROM uiux_run_steps WHERE run_id=?", (run_id,)).fetchone()[0]) + 1
            c.execute("INSERT INTO uiux_run_steps(run_id,seq,at,unit,category,kind,status,message,page_url,evidence_id,meta_json) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                      (run_id, seq, now_iso(), unit[:200], category[:60], kind[:30], status[:20], message[:400], re.sub(r"[?#].*$", "", page_url or "")[:300], evidence_id or "", dumps(meta or {})))
        return seq

    def list_steps(self, run_id: str, after=0, limit=500) -> list:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM uiux_run_steps WHERE run_id=? AND seq>? ORDER BY seq LIMIT ?", (run_id, int(after), int(limit))).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["meta"] = loads(d.pop("meta_json"), {})
            out.append(d)
        return out

    def run_dir(self, run_id: str) -> tuple:
        """Where a run's evidence lives: runs/<domain>/<module>/<knowledge>/<version>/<run id>. Evidence rows keep their own stored path,
        so runs recorded before this layout (runs/<run id>) stay readable."""
        cache = self.__dict__.setdefault("_run_dirs", {})
        if run_id not in cache:
            run = self.get_run(run_id)
            cache[run_id] = ("runs", run["domain"] or "-", run["module"] or "-", run["knowledge_name"] or "-", run["version"] or "-", run_id)
        return cache[run_id]

    def count_findings(self, run_id: str) -> int:
        with self.db.connect() as c:
            return c.execute("SELECT COUNT(*) FROM uiux_findings WHERE run_id=? AND verifiable=1", (run_id,)).fetchone()[0]

    def get_run(self, run_id: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM uiux_runs WHERE run_id=?", (run_id,)).fetchone()
        if not row:
            raise RepoError("The run does not exist.")
        d = dict(row)
        for k in ("config", "summary", "progress"):
            d[k] = loads(d.pop(k + "_json"), {})
        d["viewports"] = loads(d.pop("viewports_json"), [])
        d["cancel_requested"] = bool(d["cancel_requested"])
        return d

    def update_run(self, run_id: str, **fields):
        json_fields = {"config": "config_json", "summary": "summary_json", "progress": "progress_json", "viewports": "viewports_json"}
        sets, values = [], []
        for k, v in fields.items():
            col = json_fields.get(k, k)
            sets.append(f"{col}=?")
            values.append(dumps(v) if k in json_fields else v)
        with self.db.connect() as c:
            c.execute(f"UPDATE uiux_runs SET {','.join(sets)} WHERE run_id=?", values + [run_id])

    def list_runs(self, document_version_id: int, limit=50) -> list:
        with self.db.connect() as c:
            rows = c.execute("SELECT run_id FROM uiux_runs WHERE document_version_id=? ORDER BY created_at DESC LIMIT ?", (int(document_version_id), int(limit))).fetchall()
        out = []
        for r in rows:
            run = self.get_run(r["run_id"])
            run.pop("config", None)
            out.append(run)
        return out

    def request_cancel(self, run_id: str):
        run = self.get_run(run_id)
        if run["status"] not in RUN_ACTIVE:
            raise RepoError("Only a queued or running run can be stopped.")
        self.update_run(run_id, cancel_requested=1)

    def reconcile_interrupted(self) -> int:
        """At startup: a run left Queued/Running by a restart cannot continue; mark it Interrupted (its saved findings stay)."""
        with self.db.connect() as c:
            cur = c.execute("UPDATE uiux_runs SET status='Interrupted',status_detail='The server restarted while this run was in progress.',ended_at=? WHERE status IN ('Queued','Running','Paused')", (now_iso(),))
            return cur.rowcount

    # ================================================================= findings and evidence
    def store_findings(self, run_id: str, items: list, regression: dict | None = None, offset=0) -> int:
        run = self.get_run(run_id)
        n = offset
        with self.db.connect() as c:
            for f in items:
                n += 1
                defect_id = f"{run_id}-D{n:03d}"
                state = (regression or {}).get(f["fingerprint"], "")
                cur = c.execute("""INSERT INTO uiux_findings(defect_id,run_id,document_version_id,fingerprint,mapping_id,frame_id,screen,component,category,title,description,expected,actual,difference,
                    classification,severity,confidence,design_evidence_json,browser_evidence_json,screenshot_evidence_id,suggestion,suggestion_source,ai_note,verifiable,regression_state,
                    application_build,design_version,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                (defect_id, run_id, run["document_version_id"], f["fingerprint"], f.get("mapping_id", ""), f.get("frame_id", ""), f["screen"], f["component"][:200], f["category"],
                                 f["title"], f["description"], f["expected"], f["actual"], f["difference"], f["classification"], f["severity"], f["confidence"],
                                 dumps({**(f.get("design_evidence") or {}), "reason": f.get("classification_reason", ""), "severity_rationale": f.get("severity_rationale", "")}),
                                 dumps({**(f.get("browser_evidence") or {}), "viewport": f.get("viewport"), "region": f.get("region"), "component_type": f.get("component_type"), "prop": f.get("prop")}),
                                 f.get("screenshot_evidence_id", ""), f.get("suggestion", ""), f.get("suggestion_source", ""), f.get("ai_note", ""), f.get("verifiable", 1), state,
                                 run["build_label"], run["version"], now_iso()))
                f["id"], f["defect_id"] = cur.lastrowid, defect_id
        self._apply_prior_reviews(run_id)
        return n

    def _apply_prior_reviews(self, run_id: str):
        with self.db.connect() as c:
            rows = c.execute("SELECT id,fingerprint,document_version_id,expected,actual FROM uiux_findings WHERE run_id=? AND verifiable=1", (run_id,)).fetchall()
            for r in rows:
                rev = c.execute("SELECT decision,actual_snapshot FROM uiux_reviews WHERE document_version_id=? AND fingerprint=? ORDER BY id DESC LIMIT 1", (r["document_version_id"], r["fingerprint"])).fetchone()
                if rev:
                    changed = rev["actual_snapshot"] and rev["actual_snapshot"] != snapshot({"expected": r["expected"], "actual": r["actual"]})
                    c.execute("UPDATE uiux_findings SET review_status=?,needs_reassessment=? WHERE id=?", (rev["decision"], 1 if changed else 0, r["id"]))

    def _finding_public(self, row):
        d = dict(row)
        d["design_evidence"] = loads(d.pop("design_evidence_json"), {})
        d["browser_evidence"] = loads(d.pop("browser_evidence_json"), {})
        d["verifiable"] = bool(d["verifiable"])
        d["needs_reassessment"] = bool(d["needs_reassessment"])
        return d

    def list_findings(self, run_id: str, classification="", severity="", category="", review_status="", screen="", regression_state="", text="", limit=500, offset=0) -> dict:
        where, args = ["run_id=?"], [run_id]
        for col, val in (("classification", classification), ("severity", severity), ("category", category), ("review_status", review_status), ("screen", screen), ("regression_state", regression_state)):
            if val:
                where.append(f"{col}=?"); args.append(val)
        if text:
            where.append("(title LIKE ? OR component LIKE ? OR description LIKE ?)"); args += [f"%{text}%"] * 3
        sql = " AND ".join(where)
        with self.db.connect() as c:
            total = c.execute(f"SELECT COUNT(*) FROM uiux_findings WHERE {sql}", args).fetchone()[0]
            rows = c.execute(f"SELECT * FROM uiux_findings WHERE {sql} ORDER BY CASE severity WHEN 'Critical' THEN 0 WHEN 'High' THEN 1 WHEN 'Medium' THEN 2 ELSE 3 END, verifiable DESC, id LIMIT ? OFFSET ?",
                            args + [int(limit), int(offset)]).fetchall()
            ev = c.execute("SELECT id,finding_id,kind,label FROM uiux_evidence WHERE run_id=? AND finding_id IS NOT NULL", (run_id,)).fetchall()
        by_f = {}
        for e in ev:
            by_f.setdefault(e["finding_id"], []).append({"id": e["id"], "kind": e["kind"], "label": e["label"]})
        items = [self._finding_public(r) for r in rows]
        for it in items:
            it["evidence"] = by_f.get(it["id"], [])
        return {"total": total, "items": items}

    def get_finding(self, finding_id: int) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM uiux_findings WHERE id=?", (int(finding_id),)).fetchone()
            if not row:
                raise RepoError("The finding does not exist.")
            ev = c.execute("SELECT id,kind,label FROM uiux_evidence WHERE finding_id=?", (int(finding_id),)).fetchall()
        d = self._finding_public(row)
        d["evidence"] = [dict(e) for e in ev]
        d["review_history"] = self.review_history(d["document_version_id"], d["fingerprint"])
        return d

    def set_ai_note(self, finding_id: int, note: str, suggestion: str = ""):
        with self.db.connect() as c:
            row = c.execute("SELECT suggestion FROM uiux_findings WHERE id=?", (int(finding_id),)).fetchone()
            if not row:
                return
            if suggestion and not row["suggestion"]:
                c.execute("UPDATE uiux_findings SET ai_note=?,suggestion=?,suggestion_source='ai' WHERE id=?", (note[:1500], suggestion[:1500], int(finding_id)))
            else:
                c.execute("UPDATE uiux_findings SET ai_note=? WHERE id=?", (note[:1500], int(finding_id)))

    def add_evidence(self, run_id: str, kind: str, name: str, data: bytes, label="", finding_id=None, meta=None, width=0, height=0, mime="image/png") -> str:
        evid = new_id("EV")
        ext = {"image/png": "png", "image/jpeg": "jpg", "video/webm": "webm"}.get(mime, "bin")
        rel = self.storage.relative(*self.run_dir(run_id), f"{evid}_{re.sub(r'[^A-Za-z0-9]', '', kind)[:20]}.{ext}")
        self.storage.write(rel, data)
        with self.db.connect() as c:
            c.execute("INSERT INTO uiux_evidence(id,run_id,finding_id,kind,path,mime,sha256,width,height,label,meta_json,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                      (evid, run_id, finding_id, kind, rel, mime, sha256_bytes(data), width, height, label[:200], dumps(meta or {}), now_iso()))
        return evid

    def link_evidence(self, evidence_id: str, finding_id: int):
        with self.db.connect() as c:
            c.execute("UPDATE uiux_evidence SET finding_id=? WHERE id=?", (int(finding_id), evidence_id))

    def evidence_meta(self, evidence_id: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM uiux_evidence WHERE id=?", (evidence_id,)).fetchone()
        if not row:
            raise RepoError("The evidence does not exist.")
        d = dict(row)
        d["meta"] = loads(d.pop("meta_json"), {})
        return d

    def evidence_bytes(self, evidence_id: str) -> tuple[bytes, str]:
        meta = self.evidence_meta(evidence_id)
        return self.storage.read(meta["path"]), meta["mime"]

    def list_evidence(self, run_id: str, kind="") -> list:
        with self.db.connect() as c:
            rows = c.execute("SELECT id,finding_id,kind,label,width,height,meta_json FROM uiux_evidence WHERE run_id=?" + (" AND kind=?" if kind else "") + " ORDER BY created_at", [run_id] + ([kind] if kind else [])).fetchall()
        return [{**{k: r[k] for k in ("id", "finding_id", "kind", "label", "width", "height")}, "meta": loads(r["meta_json"], {})} for r in rows]

    # ================================================================= reviews (append-only)
    def review(self, finding_id: int, decision: str, comment: str, reviewer: str) -> dict:
        if decision not in REVIEW_DECISIONS:
            raise RepoError("Choose one of: " + ", ".join(REVIEW_DECISIONS) + ".")
        comment = (comment or "").strip()[:1000]
        f = self.get_finding(finding_id)
        if not f["verifiable"]:
            raise RepoError("Not Verifiable items are informational and cannot be reviewed as defects.")
        if decision in ("Not Applicable", "Needs Investigation") and not comment:
            raise RepoError("Add a comment explaining this decision.")
        snap = snapshot({"expected": f["expected"], "actual": f["actual"]})
        with self.db.connect() as c:
            c.execute("INSERT INTO uiux_reviews(fingerprint,document_version_id,finding_id,run_id,decision,comment,reviewer,reviewed_at,actual_snapshot) VALUES(?,?,?,?,?,?,?,?,?)",
                      (f["fingerprint"], f["document_version_id"], f["id"], f["run_id"], decision, comment, reviewer, now_iso(), snap))
            c.execute("UPDATE uiux_findings SET review_status=?,needs_reassessment=0 WHERE id=?", (decision, f["id"]))
        return self.get_finding(finding_id)

    def review_history(self, document_version_id: int, fingerprint: str) -> list:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM uiux_reviews WHERE document_version_id=? AND fingerprint=? ORDER BY id", (int(document_version_id), fingerprint)).fetchall()
        return [dict(r) for r in rows]

    # ================================================================= baselines
    def _baseline_public(self, row):
        d = dict(row)
        d["snapshot"] = loads(d.pop("snapshot_json"), {})
        return d

    def create_baseline(self, document_version_id: int, asset_id: str, source_run_id: str, label: str, notes: str, user: str) -> dict:
        run = self.get_run(source_run_id)
        if run["document_version_id"] != int(document_version_id) or run["asset_id"] != asset_id:
            raise RepoError("The run belongs to a different design or version.")
        if run["status"] not in ("Completed", "Partial"):
            raise RepoError("A baseline can only be created from a run that completed.")
        label = (label or "").strip()[:120] or f"Baseline {run['build_label'] or run['run_id']}"
        findings = self.list_findings(source_run_id, limit=5000)["items"]
        snap = {
            "run_id": source_run_id, "build_label": run["build_label"], "asset_id": asset_id,
            "findings": [{"fingerprint": f["fingerprint"], "title": f["title"], "classification": f["classification"], "severity": f["severity"], "review_status": f["review_status"],
                          "expected": f["expected"], "actual": f["actual"], "screen": f["screen"]} for f in findings if f["verifiable"]],
            "screens": [e for e in self.list_evidence(source_run_id, "screenshot")],
            "mappings": run["config"].get("mapping_ids", []), "summary": run["summary"].get("totals", {}),
        }
        with self.db.connect() as c:
            asset = c.execute("SELECT content_hash FROM uiux_design_assets WHERE id=?", (asset_id,)).fetchone()
            snap["design_hash"] = asset["content_hash"] if asset else ""
            bid = new_id("BSL")
            c.execute("""INSERT INTO uiux_baselines(id,document_version_id,asset_id,label,design_version,build_label,status,source_run_id,snapshot_json,notes,created_by,created_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""", (bid, int(document_version_id), asset_id, label, run["version"], run["build_label"], "Draft", source_run_id, dumps(snap), (notes or "")[:500], user, now_iso()))
        return self.get_baseline(bid)

    def get_baseline(self, baseline_id: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM uiux_baselines WHERE id=?", (baseline_id,)).fetchone()
        if not row:
            raise RepoError("The baseline does not exist.")
        return self._baseline_public(row)

    def list_baselines(self, document_version_id: int) -> list:
        with self.db.connect() as c:
            rows = c.execute("SELECT * FROM uiux_baselines WHERE document_version_id=? ORDER BY created_at DESC", (int(document_version_id),)).fetchall()
        out = []
        for r in rows:
            d = self._baseline_public(r)
            d["snapshot"] = {"finding_count": len(d["snapshot"].get("findings", [])), "build_label": d["snapshot"].get("build_label", ""), "run_id": d["snapshot"].get("run_id", ""), "design_hash": d["snapshot"].get("design_hash", "")}
            out.append(d)
        return out

    def transition_baseline(self, baseline_id: str, to: str, user: str, note="") -> dict:
        b = self.get_baseline(baseline_id)
        if to not in BASELINE_FLOW.get(b["status"], ()):
            raise RepoError(f"A baseline in '{b['status']}' cannot move to '{to}'.")
        now = now_iso()
        with self.db.connect() as c:
            if to == "In Review":
                c.execute("UPDATE uiux_baselines SET status=?,reviewed_by=?,reviewed_at=? WHERE id=?", (to, user, now, baseline_id))
            elif to == "Approved":
                if user and b["created_by"] == user and b["reviewed_by"] == user:
                    pass  # single-user installations are allowed; the audit trail records who did what
                c.execute("UPDATE uiux_baselines SET status=?,approved_by=?,approved_at=? WHERE id=?", (to, user, now, baseline_id))
            elif to == "Active":
                prev = c.execute("SELECT id FROM uiux_baselines WHERE document_version_id=? AND asset_id=? AND status='Active'", (b["document_version_id"], b["asset_id"])).fetchall()
                for p in prev:
                    c.execute("UPDATE uiux_baselines SET status='Superseded',superseded_at=?,superseded_by=? WHERE id=?", (now, baseline_id, p["id"]))
                c.execute("UPDATE uiux_baselines SET status='Active',activated_at=? WHERE id=?", (now, baseline_id))
            else:
                c.execute("UPDATE uiux_baselines SET status=? WHERE id=?", (to, baseline_id))
            if note:
                c.execute("UPDATE uiux_baselines SET notes=? WHERE id=?", (((b["notes"] + "\n") if b["notes"] else "") + f"[{now} {user}] {note}"[:500], baseline_id))
        return self.get_baseline(baseline_id)

    def active_baseline(self, document_version_id: int, asset_id: str):
        with self.db.connect() as c:
            row = c.execute("SELECT id FROM uiux_baselines WHERE document_version_id=? AND asset_id=? AND status='Active'", (int(document_version_id), asset_id)).fetchone()
        return self.get_baseline(row["id"]) if row else None

    # ================================================================= regression checks
    def save_check(self, finding_id: int, name: str, user: str) -> dict:
        f = self.get_finding(finding_id)
        if not f["verifiable"] or f["classification"] == "Not Verifiable":
            raise RepoError("Only verified findings can be saved as regression checks.")
        if f["review_status"] not in ("Confirmed",) and f["classification"] != "Confirmed Difference":
            raise RepoError("Review and confirm this finding before saving it as a regression check.")
        spec = {"prop": f["browser_evidence"].get("prop"), "component": f["component"], "screen": f["screen"], "expected": f["expected"], "category": f["category"],
                "component_type": f["browser_evidence"].get("component_type"), "viewport": f["browser_evidence"].get("viewport")}
        cid = new_id("CHK")
        with self.db.connect() as c:
            dup = c.execute("SELECT id FROM uiux_checks WHERE document_version_id=? AND source_finding_fingerprint=? AND active=1", (f["document_version_id"], f["fingerprint"])).fetchone()
            if dup:
                raise RepoError("This finding is already saved as a regression check.")
            c.execute("INSERT INTO uiux_checks(id,document_version_id,mapping_id,name,category,spec_json,source_finding_fingerprint,active,created_by,created_at) VALUES(?,?,?,?,?,?,?,1,?,?)",
                      (cid, f["document_version_id"], f["mapping_id"], (name or f["title"])[:150], f["category"], dumps(spec), f["fingerprint"], user, now_iso()))
        return self.get_check(cid)

    def get_check(self, check_id: str) -> dict:
        with self.db.connect() as c:
            row = c.execute("SELECT * FROM uiux_checks WHERE id=?", (check_id,)).fetchone()
        if not row:
            raise RepoError("The regression check does not exist.")
        d = dict(row)
        d["spec"] = loads(d.pop("spec_json"), {})
        d["active"] = bool(d["active"])
        return d

    def list_checks(self, document_version_id: int) -> list:
        with self.db.connect() as c:
            rows = c.execute("SELECT id FROM uiux_checks WHERE document_version_id=? ORDER BY created_at DESC", (int(document_version_id),)).fetchall()
        return [self.get_check(r["id"]) for r in rows]

    def deactivate_check(self, check_id: str):
        self.get_check(check_id)
        with self.db.connect() as c:
            c.execute("UPDATE uiux_checks SET active=0 WHERE id=?", (check_id,))

    def record_check_result(self, check_id: str, result: str, run_id: str):
        with self.db.connect() as c:
            c.execute("UPDATE uiux_checks SET last_result=?,last_run_id=? WHERE id=?", (result, run_id, check_id))

    # ================================================================= comparisons and analytics
    def compare_runs(self, run_a: str, run_b: str) -> dict:
        """a = earlier/base, b = later/current."""
        from .findings import regression_diff
        a, b = self.get_run(run_a), self.get_run(run_b)
        if a["document_version_id"] != b["document_version_id"]:
            raise RepoError("Runs from different Knowledge Hub versions cannot be compared.")
        fa = self.list_findings(run_a, limit=5000)["items"]
        fb = self.list_findings(run_b, limit=5000)["items"]
        diff = regression_diff(fb, fa)
        slim = lambda f: {k: f[k] for k in ("id", "fingerprint", "title", "severity", "classification", "category", "screen", "expected", "actual", "review_status")}
        return {"base": {k: a[k] for k in ("run_id", "build_label", "version", "status", "created_at")}, "current": {k: b[k] for k in ("run_id", "build_label", "version", "status", "created_at")},
                "counts": diff["counts"], "new": [slim(f) for f in fb if diff["states"].get(f["fingerprint"]) == "New"],
                "changed": [slim(f) for f in fb if diff["states"].get(f["fingerprint"]) == "Changed"],
                "persisting": [slim(f) for f in fb if diff["states"].get(f["fingerprint"]) == "Persisting"], "fixed": [slim(f) for f in diff["fixed"]],
                "design_changed": a["asset_id"] != b["asset_id"]}

    def compare_with_baseline(self, baseline_id: str, run_id: str) -> dict:
        b = self.get_baseline(baseline_id)
        run = self.get_run(run_id)
        if b["document_version_id"] != run["document_version_id"]:
            raise RepoError("The baseline belongs to a different Knowledge Hub version.")
        from .findings import regression_diff
        cur = self.list_findings(run_id, limit=5000)["items"]
        diff = regression_diff(cur, b["snapshot"].get("findings", []))
        return {"baseline": {"id": b["id"], "label": b["label"], "status": b["status"], "build_label": b["build_label"]}, "run_id": run_id, "counts": diff["counts"],
                "new": [f["title"] for f in cur if diff["states"].get(f["fingerprint"]) == "New"], "fixed": [f["title"] for f in diff["fixed"]]}

    def analytics(self, document_version_id: int | None = None, limit=30) -> dict:
        where, args = ("WHERE document_version_id=?", [int(document_version_id)]) if document_version_id else ("", [])
        with self.db.connect() as c:
            runs = c.execute(f"SELECT run_id,status,build_label,version,created_at,summary_json,domain,module,knowledge_name,document_type FROM uiux_runs {where} ORDER BY created_at DESC LIMIT ?", args + [limit]).fetchall()
            cats = c.execute(f"SELECT f.category,f.classification,COUNT(*) n FROM uiux_findings f JOIN uiux_runs r ON r.run_id=f.run_id {where.replace('document_version_id', 'r.document_version_id')} "
                             "AND f.verifiable=1 GROUP BY f.category,f.classification" if where else "SELECT f.category,f.classification,COUNT(*) n FROM uiux_findings f WHERE f.verifiable=1 GROUP BY f.category,f.classification", args).fetchall()
            reviewed = c.execute("SELECT review_status,COUNT(*) n FROM uiux_findings GROUP BY review_status").fetchall()
        trend = []
        for r in reversed(runs):
            totals = (loads(r["summary_json"], {}) or {}).get("totals", {})
            trend.append({"run_id": r["run_id"], "created_at": r["created_at"], "status": r["status"], "build": r["build_label"], "version": r["version"],
                          "confirmed": totals.get("confirmed", 0), "potential": totals.get("potential", 0), "needs_investigation": totals.get("needs_investigation", 0),
                          "not_verifiable": totals.get("not_verifiable", 0), "critical": totals.get("critical", 0), "high": totals.get("high", 0)})
        return {"runs": len(runs), "trend": trend, "by_category": [dict(r) for r in cats], "review_status": [dict(r) for r in reviewed]}
