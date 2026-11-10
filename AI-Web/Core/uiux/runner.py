"""Run orchestration: one run = N screen units (mapping x state), each compared in an isolated browser session.

Each unit is committed on its own, so a cancelled, failed or retried run never leaves half-written findings: the units
that finished are complete and consistent, and the run status says exactly what did not happen. A run is only
"Completed" when every selected unit executed; anything skipped or blocked is reported as such, never as success.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
import traceback
from urllib.parse import urlsplit

from . import ENGINE_VERSION, accessibility, compare_visual, findings as F, matching, results as R, structure
from .analysis import frame_dimensions, is_reference_only
from .resultstore import ResultStore
from . import categories as UC
from .browser import BrowserSession, BrowserUnavailable, RunCancelled, check_browser
from .repo import RepoError, UiuxRepo
from .security import TargetError, authorize
from .util import loads, now_iso

log = logging.getLogger("uiux")
MAX_FINDING_IMAGES = 40
MAX_VIDEO_BYTES = 40 * 1024 * 1024
PAUSE_TIMEOUT_S = 30 * 60          # a paused run keeps a browser open; after this it is stopped (and recorded as Cancelled) so resources are released
STRUCTURAL = {"layout", "css", "typography", "forms", "buttons", "navigation", "tables", "icons_images"}


class UnitError(Exception):
    """One screen could not be executed; the message is safe to show."""


class RunManager:
    def __init__(self, repo: UiuxRepo, registry, vault, agent=None, max_parallel=1, resolver=None, headless=True):
        self.repo, self.registry, self.vault, self.agent = repo, registry, vault, agent
        self.resolver, self.headless = resolver, headless
        self.results = ResultStore(repo.db)
        self._events: dict = {}
        self._pause: dict = {}        # run_id -> Event; set = running, cleared = a pause was requested
        self._cursor: dict = {}       # run_id -> (unit index, unit count) for progress
        self._live: dict = {}         # run_id -> {"seq", "at", "caption", "url"} for the live view
        self._timed_out: set = set()
        self.pause_timeout_s = PAUSE_TIMEOUT_S
        self.record_video = True
        self._threads: dict = {}
        self._slots = threading.BoundedSemaphore(max_parallel)
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ control
    def start(self, run_id: str, wait=False) -> threading.Thread:
        with self._lock:
            ev = self._events[run_id] = threading.Event()
            self._pause[run_id] = threading.Event()
            self._pause[run_id].set()
            self._live[run_id] = {"seq": 0, "at": "", "caption": "", "url": ""}
            t = threading.Thread(target=self._thread_main, args=(run_id, ev), daemon=True, name=f"uiux-{run_id}")
            self._threads[run_id] = t
        t.start()
        if wait:
            t.join()
        return t

    def cancel(self, run_id: str):
        self.repo.request_cancel(run_id)
        ev = self._events.get(run_id)
        if ev:
            ev.set()
        pv = self._pause.get(run_id)
        if pv:
            pv.set()      # wake a paused run so it can stop

    def pause(self, run_id: str, wait=10.0) -> str:
        """Ask a running run to pause at its next safe point (between browser operations). Returns 'Paused' once it has, or 'Pausing'."""
        run = self.repo.get_run(run_id)
        if run["status"] == "Paused":
            return "Paused"
        if run["status"] not in ("Queued", "Running") or run_id not in self._pause:
            raise RepoError("Only a running test can be paused.")
        self._pause[run_id].clear()
        self.repo.add_step(run_id, "control", "Pause requested; the test will pause at the next safe point.", status="info")
        end = time.time() + wait
        while time.time() < end:
            st = self.repo.get_run(run_id)["status"]
            if st == "Paused" or st in ("Completed", "Partial", "Failed", "Blocked", "Cancelled", "Interrupted"):
                return st
            time.sleep(0.05)
        return "Pausing"

    def resume(self, run_id: str) -> str:
        run = self.repo.get_run(run_id)
        if run["status"] not in ("Paused", "Running") or run_id not in self._pause:
            raise RepoError("Only a paused test can be resumed.")
        pv = self._pause[run_id]
        if pv.is_set():
            return run["status"]     # nothing was pending
        pv.set()
        end = time.time() + 5
        while time.time() < end:
            st = self.repo.get_run(run_id)["status"]
            if st != "Paused":
                return st
            time.sleep(0.05)
        return "Running"

    def _gate(self, run_id, cancel):
        """The safe point: blocks while the run is paused, raises RunCancelled when it is stopped."""
        pv = self._pause.get(run_id)
        def gate():
            if cancel.is_set():
                raise RunCancelled()
            if pv is None or pv.is_set():
                return
            self.repo.update_run(run_id, status="Paused")
            self.repo.add_step(run_id, "control", "Paused. Completed results are kept; resume continues from this point.", status="paused")
            deadline = time.time() + self.pause_timeout_s
            while not pv.wait(0.2):
                if cancel.is_set():
                    raise RunCancelled()
                if time.time() > deadline:
                    self._timed_out.add(run_id)
                    cancel.set()
                    self.repo.update_run(run_id, cancel_requested=1)
                    raise RunCancelled()
            if cancel.is_set():
                raise RunCancelled()
            self.repo.update_run(run_id, status="Running")
            self.repo.add_step(run_id, "control", "Resumed.", status="info")
        return gate

    def live_state(self, run_id: str) -> dict:
        return dict(self._live.get(run_id) or {})

    def _live_frame(self, run_id, session, caption):
        """Publish what the browser shows right now. This is a refreshed JPEG at every step - not a video stream."""
        frame = session.live_frame()
        if not frame:
            return
        try:
            self.repo.storage.write(self.repo.storage.relative(*self.repo.run_dir(run_id), "live.jpg"), frame)
        except Exception:
            return
        st = self._live.setdefault(run_id, {"seq": 0})
        st.update({"seq": st.get("seq", 0) + 1, "at": now_iso(), "caption": caption[:160], "url": session.current_url})

    @staticmethod
    def _nv_wanted(n, cats):
        if n["category"] in cats:
            return True
        return n["prop"] == "structure_unavailable" and bool(cats & STRUCTURAL)

    @staticmethod
    def _add_special_rows(by_user, sel, visual, extra_nv, state, items):
        """Visual comparison and interactive states are not (category, prop) checks; count them honestly."""
        if "visual" in by_user:
            row = by_user["visual"]
            if visual is not None:
                regions = visual["region_count"]
                row["executed"] += 1
                row["failed" if regions else "passed"] += 1
            else:
                row["not_verifiable"] += sum(1 for n in extra_nv if n["category"] == "visual")
            row["status"] = UC.status_of(row)
        if "states" in by_user:
            row = by_user["states"]
            if state:
                row["executed"] += 1
                row["failed" if any(f["verifiable"] for f in items) else "passed"] += 1
            else:
                row["not_verifiable"] += sum(1 for n in extra_nv if n["category"] == "states")
            row["status"] = UC.status_of(row)

    def _unit_index(self, run, mapping, state):
        return (self._cursor.get(run["run_id"]) or (0, 1))[0]

    def _unit_total(self, run):
        return (self._cursor.get(run["run_id"]) or (0, 1))[1]

    def _keep_video(self, run_id, session, screen, mapping, state, video_dir):
        """Store the screen recording of this unit (also when it was stopped: completed evidence is preserved) and remove the temp files."""
        try:
            path = getattr(session, "video_path", None)
            if path and os.path.exists(path) and os.path.getsize(path) <= MAX_VIDEO_BYTES:
                with open(path, "rb") as fh:
                    data = fh.read()
                self.repo.add_evidence(run_id, "video", "recording", data, f"Recording: {screen}", meta={"mapping_id": mapping["id"], "state": state["name"] if state else "default"}, mime="video/webm")
        except Exception:
            log.warning("could not store the screen recording", exc_info=True)
        finally:
            if video_dir:
                shutil.rmtree(video_dir, ignore_errors=True)

    def _step(self, run_id, kind, message, unit="", category="", status="info", page_url="", evidence_id="", meta=None):
        try:
            return self.repo.add_step(run_id, kind, message, unit, category, status, page_url, evidence_id, meta)
        except Exception:    # the trail must never break a run
            log.warning("could not record a run step", exc_info=True)
            return 0

    def join(self, run_id: str, timeout=None):
        t = self._threads.get(run_id)
        if t:
            t.join(timeout)

    def _thread_main(self, run_id, cancel):
        with self._slots:
            if cancel.is_set() or self.repo.get_run(run_id)["cancel_requested"]:
                self.repo.update_run(run_id, status="Cancelled", status_detail="Stopped before it started.", ended_at=now_iso())
                self._step(run_id, "control", "Stopped before it started.", status="cancelled")
                return
            try:
                self.execute(run_id, cancel)
            except Exception:   # last line of defence: the run must always end in a final state
                log.error("UI/UX run %s crashed:\n%s", run_id, traceback.format_exc())
                self.repo.update_run(run_id, status="Failed", status_detail="The run stopped because of an unexpected error. Check the server log.", ended_at=now_iso())

    # ------------------------------------------------------------------ main flow
    def _progress(self, run_id, phase, i=0, n=1, message="", frac=0.0, **extra):
        try:
            found = self.repo.count_findings(run_id)
        except Exception:
            found = 0
        live = self._live.get(run_id) or {}
        self.repo.update_run(run_id, progress={"phase": phase, "unit": i, "units": n, "percent": min(99, int(100 * (i + frac) / max(n, 1))), "message": message, "at": now_iso(),
                                               "findings": found, "live": {k: live.get(k) for k in ("seq", "at", "caption", "url")}, **extra})

    def execute(self, run_id: str, cancel: threading.Event):
        repo = self.repo
        run = repo.get_run(run_id)
        cfg = run["config"]
        repo.update_run(run_id, status="Running", started_at=now_iso(), engine_version=ENGINE_VERSION)
        units, blockers, completed, cancelled = [], [], 0, False
        gate = self._gate(run_id, cancel)
        self._step(run_id, "run", "Run started.", status="info")
        try:
            target_row = repo.get_target(cfg["target_id"]) if cfg.get("target_id") else None
            if not target_row or not target_row["active"]:
                raise TargetError("No authorized application target is set for this run" + (" (the selected target was removed)" if cfg.get("target_id") else "") +
                                  ". Select an authorized application target or enter the target URL again. Arbitrary websites are never tested.")
            target = authorize(target_row["origin"] + "/", cfg["environment"], repo.target_records(), self.resolver)
            repo.update_run(run_id, app_url=target.origin)
            self._step(run_id, "target", f"Target authorized: {target.origin} ({cfg['environment']}).", status="pass")
            ok, why = check_browser()
            if not ok:
                raise BrowserUnavailable(why)
            auth = self._auth(cfg, target_row)
            mappings = [repo.get_mapping(m) for m in cfg["mapping_ids"]]
            asset = self.registry.get(run["asset_id"])
            plan = self._plan(run, mappings, cfg)
            repo.update_run(run_id, viewports=sorted({f"{m['viewport_w']}x{m['viewport_h']}" for m, _ in plan}))
            previous = self._previous_findings(run)
            baseline = repo.get_baseline(run["baseline_id"]) if run["baseline_id"] else None
            if baseline and baseline["status"] != "Active":
                baseline = None
            offset = 0
            for index, (mapping, state) in enumerate(plan):
                try:
                    gate()
                except RunCancelled:
                    cancelled = True
                    break
                label = f"{mapping['frame_name']} @ {mapping['viewport_w']}px" + (f" ({state['name']})" if state else "")
                self._cursor[run_id] = (index, len(plan))
                self._progress(run_id, "Testing", index, len(plan), label)
                self._step(run_id, "screen", f"Testing {label}.", unit=label)
                try:
                    unit = self._unit(run, cfg, target, auth, asset, mapping, state, cancel, previous, baseline, offset)
                    offset = unit.pop("next_offset")
                    units.append(unit)
                    completed += 1
                except RunCancelled:
                    cancelled = True
                    units.append({"mapping_id": mapping["id"], "screen": mapping["frame_name"], "status": "Cancelled", "message": "Stopped while this screen was running; its partial results were discarded."})
                    break
                except (UnitError, TargetError) as error:
                    units.append({"mapping_id": mapping["id"], "screen": mapping["frame_name"], "status": "Blocked", "message": str(error)})
                    blockers.append(str(error))
                    self._step(run_id, "screen", str(error), unit=label, status="blocked")
                except BrowserUnavailable as error:
                    raise
                except Exception:
                    log.error("UI/UX unit failed:\n%s", traceback.format_exc())
                    units.append({"mapping_id": mapping["id"], "screen": mapping["frame_name"], "status": "Failed", "message": "This screen could not be tested because of an unexpected error."})
                    blockers.append("An unexpected error stopped one screen.")
                    self._step(run_id, "screen", "This screen could not be tested because of an unexpected error.", unit=label, status="fail")
        except (TargetError, RepoError) as error:
            self._step(run_id, "run", str(error), status="blocked")
            self._finish(run_id, "Blocked", str(error), units, cancel)
            return
        except BrowserUnavailable as error:
            self._step(run_id, "run", f"The test browser is not available: {error}", status="blocked")
            self._finish(run_id, "Blocked", f"The test browser is not available: {error}", units, cancel)
            return
        total = len(plan)
        if cancelled:
            why = "Stopped automatically after being paused for too long" if run_id in self._timed_out else "Stopped by the user"
            status, detail = "Cancelled", f"{why} after {completed} of {total} screens. Completed screens are saved."
        elif completed == total and total:
            status, detail = "Completed", ""
        elif completed:
            status, detail = "Partial", f"{completed} of {total} screens were tested; {total - completed} could not be: " + "; ".join(sorted(set(blockers)))[:400]
        else:
            status, detail = ("Blocked" if blockers else "Failed"), ("No screen could be tested: " + "; ".join(sorted(set(blockers)))[:400])
        self._finish(run_id, status, detail, units, cancel)

    def _plan(self, run, mappings, cfg=None):
        cfg = cfg or {}
        with_states = (not cfg.get("ui_categories")) or "states" in (cfg.get("user_categories") or [])
        plan = []
        for m in mappings:
            if m["status"] == "Rejected":
                continue
            plan.append((m, None))
            if not with_states:
                continue
            for s in m["states"]:
                if s.get("frame_id") and s.get("actions") is not None:
                    plan.append((m, s))
        return plan

    def _auth(self, cfg, target_row):
        if cfg.get("auth", {}).get("type") != "stored":
            return {"type": "none"}
        raw = self.vault.get(f"app:{target_row['id']}")
        if not raw:
            raise TargetError("The run is set to use stored application credentials, but none are stored for this target.")
        try:
            value = json.loads(raw)
            assert value.get("type") in ("basic", "cookie", "form")
        except Exception:
            raise TargetError("The stored application credentials are not valid. Store them again.") from None
        return value

    def _previous_findings(self, run):
        """Findings of the latest finished run for the same version and design (excluding this run) keyed by unit."""
        with self.repo.db.connect() as c:
            row = c.execute("SELECT run_id FROM uiux_runs WHERE document_version_id=? AND asset_id=? AND run_id!=? AND status IN ('Completed','Partial') ORDER BY created_at DESC LIMIT 1",
                            (run["document_version_id"], run["asset_id"], run["run_id"])).fetchone()
        if run["kind"] in ("recheck", "retry") and run["parent_run_id"]:
            parent = run["parent_run_id"]
        else:
            parent = row["run_id"] if row else ""
        if not parent:
            return {"run_id": "", "items": []}
        return {"run_id": parent, "items": self.repo.list_findings(parent, limit=5000)["items"]}

    # ------------------------------------------------------------------ one screen
    def _unit(self, run, cfg, target, auth, asset, mapping, state, cancel, previous, baseline, offset):
        repo = self.repo
        frame = self.registry.frame(state["frame_id"] if state else mapping["frame_id"])
        if frame["asset_id"] != asset["id"]:
            raise UnitError("The mapped frame no longer belongs to this design.")
        vw, vh = mapping["viewport_w"], mapping["viewport_h"]
        cats = set(cfg["categories"])
        ui, sel = bool(cfg.get("ui_categories")), list(cfg.get("user_categories") or [])
        rid = run["run_id"]
        gate = self._gate(rid, cancel)
        tol = structure.Tolerances.from_dict(cfg.get("tolerances"))
        vcfg = compare_visual.VisualConfig(color_threshold=cfg["visual"]["color_threshold"], shift=cfg["visual"]["shift"])
        design_png = None
        if frame["image_path"]:
            design_png = self.registry.frame_image_bytes(frame["id"])
        screen = frame["name"] + (f" [{state['name']}]" if state else "")
        viewport_label = f"{mapping['viewport_name']} {vw}x{vh}"
        notes, extra_nv = [], []
        ctx = {"run_id": run["run_id"], "screen": screen, "viewport": viewport_label, "frame_id": frame["id"], "mapping_id": mapping["id"], "trusted": mapping["status"] == "Approved",
               "design_version": run["version"], "build": run["build_label"], "document_version_id": run["document_version_id"], "structural_available": bool(frame["nodes"])}
        reference = is_reference_only(frame)      # a .fig preview thumbnail: its size is NOT the design size and its pixels are never compared region by region
        ctx["reference_only"] = reference
        label = screen
        video_dir = tempfile.mkdtemp(prefix="uiux-video-") if self.record_video else None
        session = BrowserSession(target, viewport=(vw, vh), auth=auth, cancel=cancel, nav_timeout_ms=min(60000, cfg.get("timeout_s", 300) * 100), color_scheme=cfg.get("colour_scheme", "light"),
                                 headless=self.headless, gate=gate, video_dir=video_dir)
        try:
            with session:
                if auth.get("type") == "form":
                    session.sign_in()
                self._step(rid, "browser", f"Browser started ({vw}x{vh}).", unit=label)
                info = session.open(mapping["route"])
                self._live_frame(rid, session, f"Opened {mapping['route']}")
                self._step(rid, "navigate", f"Opened {mapping['route']} (HTTP {info['status']}).", unit=label, status="pass" if info["status"] < 400 else "fail", page_url=info["final_url"])
                self._progress(rid, "Testing", self._unit_index(run, mapping, state), self._unit_total(run), label, frac=0.2, page=info["final_url"], category="Loading page")
                if info["status"] >= 400:
                    raise UnitError(f"The page returned HTTP {info['status']}; it cannot be compared with the design.")
                if state and state.get("actions"):
                    session.run_actions(state["actions"])
                    self._step(rid, "action", f"Applied state '{state['name']}'.", unit=label, page_url=session.current_url)
                settle = session.stabilize()
                if not settle.get("stable"):
                    notes.append("The page kept changing while it loaded (animations, live data or lazy content); results for dynamic regions may be unreliable. Add an approved mask for dynamic areas.")
                self._live_frame(rid, session, "Page settled")
                probe = session.probe()
                self._step(rid, "inspect", f"Inspected {len(probe['elements'])} elements.", unit=label, page_url=session.current_url)
                png, sw, sh = session.screenshot()
                self._live_frame(rid, session, "Screenshot captured")
                masks = [{"x": m["x"], "y": m["y"], "w": m["w"], "h": m["h"]} for m in mapping["masks"] if "x" in m]
                sels = [m["selector"] for m in mapping["masks"] if "selector" in m]
                if sels:
                    masks += session.mask_rects(sels)
                tab = session.tab_walk() if "accessibility" in cats else None
                page_url = session.current_url
                blocked, failed, console = list(session.blocked), list(session.failed), list(session.console_errors)
        finally:
            self._keep_video(rid, session, screen, mapping, state, video_dir)
        shot_ev = repo.add_evidence(rid, "screenshot", "actual", png, f"Actual: {screen} ({viewport_label})", meta={"mapping_id": mapping["id"], "state": state["name"] if state else "default", "viewport": viewport_label}, width=sw, height=sh)
        self._step(rid, "capture", f"Captured the page ({sw}x{sh}).", unit=label, status="pass", page_url=page_url, evidence_id=shot_ev)
        self._progress(rid, "Testing", self._unit_index(run, mapping, state), self._unit_total(run), label, frac=0.6, page=page_url, category="Comparing with the design")
        if probe.get("truncated"):
            notes.append("The page has more than 4000 visible elements; only the first 4000 were inspected.")
        if not reference and frame["width"] and abs(frame["width"] - vw) > max(tol.viewport_px, 0.02 * vw) and (not ui or "responsive" in sel):
            msg = (f"The design frame is {frame['width']:g}px wide but this screen was tested at a {vw}px viewport. Differences may only reflect responsive behaviour; "
                   "map the frame to a viewport that matches its width, or map a separate frame for this viewport.")
            notes.append(msg)
            extra_nv.append(self._nv(ctx, "responsive", "viewport_mismatch", "Viewport does not match the design frame", msg, classification="Needs Investigation"))
        if ui and "states" in sel and not state and not any(st.get("frame_id") for st in mapping["states"]):
            extra_nv.append(self._nv(ctx, "states", "no_state_frames", "Interactive states",
                                     "No design frame is mapped for an interactive state (hover, focus, open menu...), so states were not tested for this screen. Map a frame to a state under Advanced options."))
        # ---- visual comparison (only against the mapped viewport)
        visual = None
        diff_png = sbs_png = None
        if "visual" not in cats or reference:
            pass            # reference-only designs get a coarse, review-only comparison in the result rows instead
        elif design_png is not None:
            dw = compare_visual._load(design_png).shape[1]
            if abs(dw - sw) > 1 and sw > dw and probe["page"]["hOverflow"]:
                notes.append(f"The page is wider than the viewport ({sw}px); only the first {dw}px (the design width) were compared visually.")
                png_cmp = compare_visual.crop(png, 0, 0, dw, sh)
                visual = compare_visual.compare(design_png, png_cmp, masks, vcfg)
                diff_png = compare_visual.diff_overlay(visual)
                sbs_png = compare_visual.side_by_side(visual)
            elif abs(dw - sw) > 1:
                extra_nv.append(self._nv(ctx, "visual", "viewport_width", "Visual comparison",
                                         f"The design image is {dw}px wide but the application screenshot is {sw}px wide. Visual comparison was skipped because comparing different widths would be meaningless; map the frame to a matching viewport."))
            else:
                visual = compare_visual.compare(design_png, png, masks, vcfg)
                diff_png = compare_visual.diff_overlay(visual)
                sbs_png = compare_visual.side_by_side(visual)
            if visual is not None:
                if visual["size"]["height_mismatch"]:
                    notes.append(f"The design frame is {visual['size']['expected'][1]}px tall and the page is {visual['size']['actual'][1]}px tall; only the overlapping {visual['size']['compared'][1]}px were compared.")
        else:
            extra_nv.append(self._nv(ctx, "visual", "no_design_image", "Visual comparison", "This frame has no design image (upload or export one, or connect Figma with image access), so pixel comparison was not performed."))
        # ---- structural comparison
        checks = []
        match = None
        fonts = {f["stack"]: f["available"] for f in probe["fonts"]}
        design_fonts = {((n.get("text") or {}).get("fontFamily") or "").lower() for n in frame["nodes"] if n.get("type") == "TEXT"} - {""}
        unavailable = sorted({f["first"] for f in probe["fonts"] if not f["available"] and f["first"].lower() in design_fonts})
        ctx["font_unavailable"] = bool(unavailable)
        if frame["nodes"]:
            match = matching.match_elements(frame["nodes"], probe["elements"], (frame["width"], frame["height"]), mapping["element_map"])
            icon_checker = None
            if design_png is not None and visual is not None:
                def icon_checker(bd, be):   # noqa: E306
                    return compare_visual.crop_pair_ratio(design_png, bd, png, be, vcfg)
            res = structure.compare(frame, probe["elements"], match, cats, tol, fonts, icon_checker)
            checks += res["checks"]
            notes += res["notes"]
            if unavailable:
                extra_nv.append(self._nv(ctx, "typography", "font_not_installed", "Design font not installed",
                                         "The design font(s) " + ", ".join(unavailable) + " are not installed in the test browser. Text metrics and glyphs were rendered with a fallback font, so size and wrapping differences may be caused by that alone.", classification="Needs Investigation"))
        else:
            extra_nv.append(self._nv(ctx, "css", "structure_unavailable", "Structural comparison",
                                     "This design source has no structural data (it is an image, or a native file that could not be read), so computed styles, typography and spacing could not be compared with design values. Only the visual comparison and direct page checks were performed."))
        checks += structure.dom_rules(probe, (vw, vh), cats, None if reference else frame["width"])
        a11y = None
        if "accessibility" in cats:
            a11y = accessibility.audit(probe, tab, mapping["route"])
            checks += a11y["checks"]
        if ui:
            checks = [c for c in checks if UC.keep(sel, c)]
            extra_nv = [n for n in extra_nv if self._nv_wanted(n, cats)]
        gate()
        if run["kind"] == "checks":
            wanted = {c["source_finding_fingerprint"] for c in repo.list_checks(run["document_version_id"]) if c["active"] and c["mapping_id"] == mapping["id"]}
        else:
            wanted = None
        built = F.build_findings(ctx, checks, visual, extra_nv)
        items = built["findings"]
        if run["kind"] == "recheck":
            keep = {f["fingerprint"] for f in previous["items"] if f["verifiable"]}
            items = [f for f in items if f["fingerprint"] in keep or f["classification"] == "Not Verifiable"]
        by_user = UC.summarize(sel, checks) if ui else {}
        if ui:
            self._add_special_rows(by_user, sel, visual, extra_nv, state, items)
            for key in sel:
                row = by_user[key]
                self._step(rid, "category", f"{row['label']}: " + (f"{row['executed']} checked, {row['failed']} differ" if row["executed"] else row["status"].lower()) + ".", unit=label, category=key,
                           status=("fail" if row["failed"] else ("pass" if row["executed"] else "info")), page_url=page_url, evidence_id=shot_ev)
                gate()
        # ---- regression against the previous run (same screen + viewport only)
        prev_unit = [f for f in previous["items"] if f["screen"] == screen and (f["browser_evidence"] or {}).get("viewport") == viewport_label]
        regression = F.regression_diff(items, prev_unit) if previous["run_id"] else {"states": {}, "fixed": [], "counts": {}}
        # ---- evidence
        ev = {}
        ev["screenshot"] = shot_ev
        if design_png is not None:
            ev["design"] = repo.add_evidence(run["run_id"], "design", "expected", design_png, f"Design: {screen}", meta={"mapping_id": mapping["id"], "frame_id": frame["id"]})
        if diff_png:
            ev["diff"] = repo.add_evidence(run["run_id"], "diff", "difference", diff_png, f"Difference: {screen}", meta={"mapping_id": mapping["id"], "regions": visual["region_count"]})
            ev["comparison"] = repo.add_evidence(run["run_id"], "comparison", "side_by_side", sbs_png, f"Expected / Actual / Difference: {screen}", meta={"mapping_id": mapping["id"]})
        finding_images = []
        for f in items[:MAX_FINDING_IMAGES * 3]:
            if f["classification"] == "Not Verifiable" or len(finding_images) >= MAX_FINDING_IMAGES:
                continue
            region = f.get("visual_region") or f.get("region")
            if not region or region["w"] * region["h"] > 600000 or region["w"] <= 0:
                continue
            e_box = ((f.get("design_evidence") or {}).get("node") or {}).get("box")
            a_box = ((f.get("browser_evidence") or {}).get("dom") or {}).get("box")
            try:
                shot = compare_visual.triptych(design_png if visual is not None else None, png, diff_png, region, e_box, a_box)
            except Exception:   # an image problem must not lose the finding itself
                continue
            finding_images.append((f, repo.add_evidence(run["run_id"], "finding", "finding", shot, f["title"][:150], meta={"mapping_id": mapping["id"]})))
        # drift against the active baseline's screenshot of this screen
        drift = None
        if baseline:
            snap = [e for e in baseline["snapshot"].get("screens", []) if (e.get("meta") or {}).get("mapping_id") == mapping["id"] and (e.get("meta") or {}).get("state") == (state["name"] if state else "default")]
            if snap:
                try:
                    old_png, _ = repo.evidence_bytes(snap[0]["id"])
                    drift_res = compare_visual.compare(old_png, png, masks, vcfg)
                    drift = {"regions": drift_res["region_count"], "diff_ratio": drift_res["diff_ratio"], "baseline_label": baseline["label"]}
                    if drift_res["regions"]:
                        ev["drift"] = repo.add_evidence(run["run_id"], "baseline_drift", "drift", compare_visual.diff_overlay(drift_res), f"Changes since baseline “{baseline['label']}”: {screen}", meta={"mapping_id": mapping["id"], "regions": drift_res["region_count"]})
                except Exception:
                    drift = {"error": "The baseline screenshot could not be compared."}
        for f in items:
            f["regression_state"] = regression["states"].get(f["fingerprint"], "")
            f["screenshot_evidence_id"] = ev["screenshot"]
        next_offset = repo.store_findings(run["run_id"], items, regression["states"], offset)
        ctx.update(frame_name=frame["name"], page_url=info["final_url"], design_source=self._design_source(asset, frame, reference))
        self._store_results(run, ctx, ui, sel, checks, extra_nv, visual, built, by_user, reference, frame, design_png, png, diff_png, ev, mapping, screen, viewport_label, probe)
        for f, evid in finding_images:
            if f.get("id"):
                repo.link_evidence(evid, f["id"])
        if run["kind"] == "checks":
            present = {f["fingerprint"] for f in items}
            for c in repo.list_checks(run["document_version_id"]):
                if c["active"] and c["mapping_id"] == mapping["id"]:
                    repo.record_check_result(c["id"], "Failed" if c["source_finding_fingerprint"] in present else "Passed", run["run_id"])
        passed = sum(1 for c in checks if c["status"] == "Pass")
        failed_checks = sum(1 for c in checks if c["status"] == "Fail")
        nv = sum(1 for c in checks if c["status"] == "Not Verifiable")
        by_cat = {}
        for c in checks:
            row = by_cat.setdefault(c["category"], {"passed": 0, "failed": 0, "not_verifiable": 0, "explained": 0})
            row[{"Pass": "passed", "Fail": "failed", "Not Verifiable": "not_verifiable", "Explained": "explained"}.get(c["status"], "passed")] += 1
        return {
            "mapping_id": mapping["id"], "screen": screen, "frame_id": frame["id"], "viewport": viewport_label, "state": state["name"] if state else "default", "status": "Tested",
            "page": {"title": probe.get("title", ""), "path": urlsplit(info["final_url"]).path or "/", "http_status": info["status"], "elements": len(probe["elements"]), "lang": probe.get("lang", "")},
            "mapping_trusted": ctx["trusted"], "mapping_confidence": mapping["confidence"],
            "match": match["stats"] if match else None, "unmatched_design": len(match["unmatched_design"]) if match else None,
            "visual": ({"regions": visual["region_count"], "diff_pixels": visual["diff_pixels"], "diff_ratio": visual["diff_ratio"], "masked_pixels_ignored": visual["masked_pixels_ignored"], "size": visual["size"]} if visual else None),
            "checks": {"total": len(checks), "passed": passed, "failed": failed_checks, "not_verifiable": nv, "by_category": by_cat},
            "user_categories": by_user,
            "accessibility": ({"summary": a11y["summary"], "human_review": a11y["human_review"]} if a11y else None),
            "evidence": ev, "notes": notes, "drift": drift,
            "security": {"blocked_requests": len(blocked), "failed_requests": len(failed), "console_errors": len(console), "blocked_samples": [str(b)[:120] for b in blocked[:5]]},
            "reference_only": reference, "design_size": frame_dimensions({**frame, "has_structure": bool(frame["nodes"])}), "viewport_px": [vw, vh],
            "findings": len(items), "regression": regression["counts"], "fixed": [{"title": p["title"], "severity": p["severity"], "category": p["category"]} for p in regression["fixed"]],
            "next_offset": next_offset,
        }

    @staticmethod
    def _design_source(asset, frame, reference):
        kind = (asset.get("asset_kind") or "design")
        if reference:
            return f"{asset.get('file_name') or asset.get('source_file') or kind} (embedded preview image only - no structure)"
        return f"{asset.get('file_name') or asset.get('source_file') or kind} ({'structured design data' if frame.get('nodes') else 'design image'})"

    def _store_results(self, run, ctx, ui, sel, checks, extra_nv, visual, built, by_user, reference, frame, design_png, png, diff_png, ev, mapping, screen, viewport_label, probe=None):
        """Write the PASS / FAIL / Needs Review / Not Verifiable / Not Applicable rows of this screen. A failure here must never lose the run."""
        try:
            rsel = sel if (ui and sel) else list(UC.DEFAULT_SELECTION)
            rows = R.checks_to_rows(ctx, rsel, checks)
            rows += R.extra_rows(ctx, rsel, extra_nv)
            regions = (visual or {}).get("regions", [])
            unexplained = built.get("regions_unexplained", [])
            rows += R.visual_rows(ctx, visual, unexplained, max(0, len(regions) - len(unexplained)))
            extra_images = {}
            if reference and design_png is not None and ("visual" in rsel or "colors" in rsel):
                ref_rows, ref_imgs = R.reference_rows(rsel, design_png, png, frame_dimensions({**frame, "has_structure": False}))
                base = len(rows)
                rows += ref_rows
                extra_images = {base + i: v for i, v in ref_imgs.items()}
            rows += R.coverage_rows(rsel, rows, bool(frame["nodes"]), reference, by_user)
            R.attach_evidence(self.repo, run["run_id"], rows, None if reference else design_png, png, diff_png, ev, extra_images, mapping["id"])
            R.attach_specs(rows, ctx, frame, probe or {}, None if reference else design_png, png, bool(frame["nodes"]))
            for r in rows:
                r["screen"], r["viewport"] = screen, viewport_label
            self.results.store(self.repo.get_run(run["run_id"]), rows, screen, viewport_label)
        except Exception:
            log.error("UI/UX result rows could not be stored:\n%s", traceback.format_exc())

    @staticmethod
    def _nv(ctx, category, prop, label, reason, classification="Not Verifiable"):
        return {"fingerprint": F.fingerprint(category, prop, reason[:40], ctx["screen"], ctx["viewport"]), "category": category, "prop": prop, "component": label, "component_type": "page",
                "title": f"{'Not verifiable' if classification == 'Not Verifiable' else 'Needs investigation'}: {label}", "description": reason, "expected": "", "actual": "", "difference": "",
                "classification": classification, "classification_reason": reason, "severity": "Low" if classification == "Not Verifiable" else "Medium",
                "severity_rationale": "Informational: this was not checked." if classification == "Not Verifiable" else "May affect how reliable typography results are.",
                "confidence": 0.0 if classification == "Not Verifiable" else 0.5, "design_evidence": {}, "browser_evidence": {}, "suggestion": "", "suggestion_source": "",
                "verifiable": 0 if classification == "Not Verifiable" else 1, "region": None, "visual_region": None, "screen": ctx["screen"], "viewport": ctx["viewport"],
                "frame_id": ctx.get("frame_id", ""), "mapping_id": ctx.get("mapping_id", "")}

    # ------------------------------------------------------------------ finish
    def _finish(self, run_id, status, detail, units, cancel):
        repo = self.repo
        items = repo.list_findings(run_id, limit=5000)["items"]
        verified = [f for f in items if f["verifiable"]]
        totals = {
            "findings": len(verified), "confirmed": sum(1 for f in verified if f["classification"] == "Confirmed Difference"), "potential": sum(1 for f in verified if f["classification"] == "Potential Difference"),
            "needs_investigation": sum(1 for f in verified if f["classification"] == "Needs Investigation"), "not_verifiable": sum(1 for f in items if f["classification"] == "Not Verifiable"),
            "critical": sum(1 for f in verified if f["severity"] == "Critical"), "high": sum(1 for f in verified if f["severity"] == "High"),
            "medium": sum(1 for f in verified if f["severity"] == "Medium"), "low": sum(1 for f in verified if f["severity"] == "Low"),
        }
        tested = [u for u in units if u.get("status") == "Tested"]
        regression = {"new": sum(u["regression"].get("new", 0) for u in tested), "persisting": sum(u["regression"].get("persisting", 0) for u in tested),
                      "changed": sum(u["regression"].get("changed", 0) for u in tested), "fixed": sum(u["regression"].get("fixed", 0) for u in tested)}
        summary = {"units": units, "totals": totals, "regression": regression, "screens_tested": len(tested), "screens_not_tested": len(units) - len(tested),
                   "checks": {k: sum(u["checks"][k] for u in tested) for k in ("total", "passed", "failed", "not_verifiable")} if tested else {},
                   "untested": [{"screen": u.get("screen"), "reason": u.get("message", "")} for u in units if u.get("status") != "Tested"],
                   "mappings_unapproved": [u["screen"] for u in tested if not u.get("mapping_trusted")]}
        cfg_now = repo.get_run(run_id)["config"]
        sel = list(cfg_now.get("user_categories") or [])
        if cfg_now.get("ui_categories") and sel:
            merged = UC.merge_summaries([u.get("user_categories") for u in tested]) if tested else {}
            for key in sel:
                if key not in merged:
                    merged[key] = {"label": UC.LABELS[key], "executed": 0, "passed": 0, "failed": 0, "not_verifiable": 0, "explained": 0, "status": "Not run"}
            summary["categories"] = {k: merged[k] for k in sel}
            summary["checks"]["executed"] = sum(r["executed"] for r in merged.values()) if tested else 0
        else:
            summary["checks"]["executed"] = (summary["checks"].get("passed", 0) + summary["checks"].get("failed", 0)) if tested else 0
        summary["checks"].setdefault("total", 0)
        summary["checks"]["skipped_blocked"] = sum(1 for u in units if u.get("status") not in ("Tested",))
        self._finish_results(run_id, status, detail, sel if (cfg_now.get("ui_categories") and sel) else [])
        summary["results"] = self.results.counts(run_id)
        summary["steps"] = len(repo.list_steps(run_id, limit=5000))
        summary["video"] = [e["id"] for e in repo.list_evidence(run_id, "video")]
        ai_status, ai_note = "Not requested", ""
        if status in ("Completed", "Partial") and repo.get_run(run_id)["config"].get("ai", {}).get("enabled"):
            ai_status, ai_note, extras = self._ai(run_id, items, units)
            summary["ai"] = {"status": ai_status, **extras}
        self._step(run_id, "run", {"Completed": "Run completed.", "Partial": "Run finished with some screens not tested.", "Cancelled": "Run stopped; completed results were kept.",
                                    "Blocked": "Run blocked: nothing was tested.", "Failed": "Run failed."}.get(status, status) + (" " + detail[:160] if detail else ""),
                   status={"Completed": "pass", "Partial": "info", "Cancelled": "cancelled"}.get(status, "blocked"))
        self._events.pop(run_id, None)
        repo.update_run(run_id, status=status, status_detail=detail[:500], ended_at=now_iso(), summary=summary, ai_status=ai_status, ai_note=ai_note,
                        progress={"phase": status, "unit": len(tested), "units": len(units), "percent": 100 if status == "Completed" else int(100 * len(tested) / max(len(units), 1)), "message": detail[:200], "at": now_iso()})

    def _finish_results(self, run_id, status, detail, sel):
        """Categories that never ran (stopped, blocked or failed run) are recorded as Not Executed, never as passes."""
        try:
            if not sel:
                return
            have = {r["user_category"] for r in self.results.all(run_id)}
            why = {"Cancelled": "The run was stopped before this category was checked. " + detail[:200], "Partial": "This category was not checked because the screen could not be tested. " + detail[:200],
                   "Blocked": "Nothing was tested: " + detail[:240], "Failed": "The run failed before this category was checked. " + detail[:200]}.get(status)
            if status == "Completed" or why is None:
                return
            rows = R.not_executed_rows(sel, have, why)
            for r in rows:
                r["evidence"] = {"mode": "none", "limitation": "No screenshot: this category was not executed."}
            self.results.store(self.repo.get_run(run_id), rows)
        except Exception:
            log.error("Not-executed rows could not be stored:\n%s", traceback.format_exc())

    def _ai(self, run_id, items, units):
        if not self.agent:
            return "Unavailable", "AI was requested but no AI provider is configured; results are rule-based only.", {}
        try:
            result = self.agent.interpret(run_id, items, units)
        except Exception as error:
            return "Unavailable", f"AI could not be used for this run ({str(error)[:160]}). Results are rule-based only.", {}
        for fid, note in result.get("per_finding", {}).items():
            self.repo.set_ai_note(int(fid), note.get("explanation", ""), note.get("suggested_fix", ""))
        return "Completed", "", {"summary": result.get("summary", ""), "recommendations": result.get("recommendations", []), "uncertainty": result.get("uncertainty", []),
                                 "model": result.get("model", {})}
