"""Create ClickUp tasks from AI UI/UX Testing results, reusing QA AI Studio's saved ClickUp configurations.

Nothing here talks to ClickUp on its own account: it uses ``Core.test_case_publishing`` (saved, encrypted configuration, HTTPS-only
request helper with secret masking and the workspace / space / list discovery already used by Test Case publishing). The only thing
the existing helper cannot do is upload a file, so a small multipart upload with the same safeguards lives here.

Guarantees
  * Every kind of result (PASS, FAIL, Needs Review, Not Verifiable, ...) can become a ticket of the matching type; PASS is never a defect.
  * A result with a ticket is not ticketed again by accident: an explicit "Create another" and a fresh attempt token are required.
    The attempt token is also an idempotency key, so a double click, retry or refresh cannot create two tasks.
  * An attachment is reported as uploaded only if ClickUp accepted it; failures are returned and stored.
  * The token is never returned, logged or written to a task.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import urllib.error
import urllib.request
from urllib.parse import urlsplit

from .repo import RepoError
from .results import FAIL, NOT_APPLICABLE, NOT_EXECUTED, NOT_VERIFIABLE, PASS, REVIEW

ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")
MAX_ATTACHMENT = 10 * 1024 * 1024
PRIORITY = {"Critical": 1, "High": 2, "Medium": 3, "Low": 4}
TASK_TYPES = {FAIL: "Defect", REVIEW: "Review", NOT_VERIFIABLE: "Investigation", PASS: "Verification", NOT_APPLICABLE: "Note", NOT_EXECUTED: "Follow-up"}
TAGS = {FAIL: "ui-ux-fail", REVIEW: "ui-ux-review", NOT_VERIFIABLE: "ui-ux-investigation", PASS: "ui-ux-pass", NOT_APPLICABLE: "ui-ux-note", NOT_EXECUTED: "ui-ux-not-run"}


class TicketError(ValueError):
    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.status, self.extra = status, extra


def title_for(result: dict) -> str:
    name = result["name"]
    r = result["result"]
    if r == FAIL:
        return f"[UI/UX][FAIL] {name}"[:250]
    if r == PASS:
        return f"[UI/UX][PASS] {name} verified against design"[:250]
    if r == REVIEW:
        return f"[UI/UX][REVIEW] Verify {name}"[:250]
    if r == NOT_VERIFIABLE:
        return f"[UI/UX][INVESTIGATION] {name} cannot be verified"[:250]
    if r == NOT_EXECUTED:
        return f"[UI/UX][NOT RUN] {name}"[:250]
    return f"[UI/UX][N/A] {name}"[:250]


def page_url(run: dict) -> str:
    units = (run.get("summary") or {}).get("units") or []
    path = next((u.get("page", {}).get("path", "") for u in units if u.get("status") == "Tested"), "")
    return (run.get("app_url") or "") + (path if path and path != "/" else "")


def description_for(run: dict, result: dict, intro: str = "") -> str:
    members = result.get("members") or []
    rep = members[0] if members else {}
    lines = ["QA AI Studio — AI UI/UX Testing", ""]
    if intro:
        lines += [intro, ""]
    lines += [f"Run ID: {run['run_id']}", f"Test ID: {result['test_id']}", f"Design: {run.get('source_file') or run.get('knowledge_name', '')} (version {run.get('version', '')})",
              f"Target website: {page_url(run)}",
              f"Category: {result['category_label']}", f"Result: {result['result']}", f"Severity: {result['severity'] or 'n/a'}", f"Viewport: {result['viewport']}", f"Executed: {result['executed_at']}", "",
              "Expected (design):", result["expected"], "", "Actual (website):", result["actual"], "", "Reason:", result["reason"], ""]
    sp = (result.get("evidence") or {}).get("spec") or {}
    if sp:
        lines += ["Defect location:", f"- Page: {sp.get('page_url') or page_url(run)}", f"- Screen / design frame: {sp.get('screen') or '—'}", f"- Component: {sp.get('element') or '—'}"]
        if sp.get("website_location"):
            lines.append(f"- Location on the page: {sp['website_location']}")
        if sp.get("design_location"):
            lines.append(f"- Location in the design: {sp['design_location']}")
        if sp.get("locator"):
            lines.append(f"- Element locator: {sp['locator']}  ({sp.get('locator_kind', '')})")
        if sp.get("position"):
            lines.append(f"- Position in page: {sp['position']}")
        if sp.get("also_affected_total"):
            lines.append(f"- Also affected: {sp['also_affected_total']} more element(s) in this group" + (": " + ", ".join(sp.get("also_affected") or [])[:300] if sp.get("also_affected") else ""))
        lines.append("")
        fields = [f for f in sp.get("fields", []) if f["field"] not in ("Screen / page", "Location (hierarchy)")]
        if fields:
            lines += ["Expected vs actual specification:", "Field | Expected (design) | Actual (website) | Result"]
            for f in fields[:24]:
                lines.append(f"{f['field']} | {f['expected']} | {f['actual']} | {'DIFFERS' if f['status'] == 'differs' else ('match' if f['status'] == 'match' else f['status'])}")
            lines.append("")
        if sp.get("differences"):
            lines += ["Difference:"] + [f"- {d['field']}: expected {d['expected']}, actual {d['actual']}" for d in sp["differences"][:12]] + [""]
        if sp.get("inferred"):
            lines += ["Note: some values were read from the design picture, not from design data, and are marked as inferred.", ""]
        if sp.get("suggested_fix"):
            lines += ["Suggested fix (evidence-based suggestion only):", sp["suggested_fix"], ""]
    if len(members) > 1:
        lines.append(f"Grouped measurements ({result['member_count']} elements, first {min(len(members), 10)} shown):")
        for m in members[:10]:
            lines.append(f"- {m['component']}: expected {m['expected']}, actual {m['actual']}")
        lines.append("")
    lines += ["Reproduction steps:", f"1. Open {page_url(run)} in Chromium at viewport {result['viewport'] or 'default'}."]
    n = 2
    if rep.get("selector"):
        lines.append(f"{n}. Locate the element {rep['selector']}.")
        n += 1
    lines += [f"{n}. Compare it with the approved design '{run.get('source_file', '')}' (version {run.get('version', '')}).", f"{n + 1}. Observed: {result['actual']}. Expected: {result['expected']}.", "",
              "Evidence: expected design, actual website and difference images are attached when available, and are stored in QA AI Studio under this Run ID and Test ID."]
    ev = result.get("evidence") or {}
    if ev.get("limitation"):
        lines += ["", "Evidence limitation: " + ev["limitation"]]
    return "\n".join(str(x) for x in lines)[:15000]


def _mask(text: str, secret: str) -> str:
    text = str(text)
    if secret:
        text = text.replace(secret, "********")
    return re.sub(r"(?i)(authorization|token|secret)\s*[:=]\s*[^\s,;]+", r"\1=********", text)[:600]


class ClickUpTickets:
    def __init__(self, results, repo, service=None, uploader=None):
        self.results, self.repo = results, repo
        self._service = service
        self.uploader = uploader or self._multipart_upload

    @property
    def service(self):
        if self._service is None:
            from Core.test_case_publishing import PublishingService
            self._service = PublishingService()
        return self._service

    # ------------------------------------------------------------------ configuration & discovery
    def configurations(self) -> list:
        out = []
        for c in self.service.repository.list_configs("CLICKUP"):
            s = c.get("settings") or {}
            out.append({"id": c["id"], "name": c["name"], "is_default": c["is_default"], "workspace_id": str(s.get("workspace_id") or ""), "space_id": str(s.get("space_id") or ""),
                        "folder_id": str(s.get("folder_id") or ""), "list_id": str(s.get("list_id") or ""), "secret_is_set": c["secret_is_set"], "custom_item_id": int(s.get("custom_item_id") or 0)})
        return out

    def _config(self, config_id):
        cfg = self.service.repository.get_config(int(config_id)) if str(config_id).isdigit() else None
        if not cfg or cfg["target"] != "CLICKUP":
            raise TicketError("The ClickUp configuration was not found. Configure ClickUp under Reports > Test Cases Publish first.", 404)
        if not cfg["secret_is_set"]:
            raise TicketError("The selected ClickUp configuration has no saved token.", 422)
        return cfg

    def discover(self, config_id, kind: str, parent_id: str = "") -> dict:
        self._config(config_id)
        if parent_id and not ID_RE.match(parent_id):
            raise TicketError("The ClickUp destination ID is not valid.", 422)
        try:
            return self.service.discover_clickup(int(config_id), kind, parent_id)
        except ValueError as error:
            raise TicketError(str(error), 422) from None

    def metadata(self, config_id, list_id: str) -> dict:
        cfg = self._config(config_id)
        if not ID_RE.match(str(list_id or "")):
            raise TicketError("Choose a ClickUp list.", 422)
        s = cfg.get("settings") or {}
        try:
            return self.service.discover_clickup_values({"configuration_id": int(config_id), "workspace_id": str(s.get("workspace_id") or ""), "space_id": str(s.get("space_id") or "")}, "metadata", list_id)
        except ValueError as error:
            raise TicketError(str(error), 422) from None

    # ------------------------------------------------------------------ prefill
    def prefill(self, result_id: str) -> dict:
        r = self._result(result_id)
        run = self.repo.get_run(r["run_id"])
        kind = TASK_TYPES[r["result"]]
        intro = {FAIL: "A difference from the approved design was confirmed.", REVIEW: "This result needs a human review before it can be called a pass or a defect.",
                 NOT_VERIFIABLE: "This could not be verified with the available design data; please decide how to obtain the missing evidence.",
                 PASS: "This check passed. The ticket records the verified result (for example as an approved baseline or an improvement idea).",
                 NOT_APPLICABLE: "No applicable check existed for this category.", NOT_EXECUTED: "This category was not executed."}[r["result"]]
        ev = r.get("evidence") or {}
        return {"result": {k: r[k] for k in ("id", "test_id", "run_id", "name", "user_category", "category_label", "result", "expected", "actual", "reason", "severity", "viewport", "member_count")},
                "task_type": kind, "task_types": sorted(set(TASK_TYPES.values())), "title": title_for(r), "description": description_for(run, r, intro),
                "expected": r["expected"], "actual": r["actual"], "priority": PRIORITY.get(r["severity"], 3 if r["result"] in (REVIEW, FAIL) else 4), "severity": r["severity"],
                "tags": ["ui-ux", TAGS[r["result"]], r["user_category"]], "evidence": {k: ev.get(k) for k in ("design", "actual", "diff", "page_design", "page_actual", "page_diff", "mode", "limitation")},
                "existing": r["tickets"], "ticket_status": r["ticket_status"], "attempt_token": secrets.token_hex(12), "configurations": self.configurations()}

    def _result(self, result_id):
        try:
            return self.results.get(result_id)
        except LookupError:
            raise TicketError("The result does not exist.", 404) from None

    # ------------------------------------------------------------------ create
    def create(self, result_id: str, body: dict, user: str) -> dict:
        r = self._result(result_id)
        run = self.repo.get_run(r["run_id"])
        cfg = self._config(body.get("config_id"))
        s = cfg.get("settings") or {}
        list_id = str(body.get("list_id") or s.get("list_id") or "")
        if not ID_RE.match(list_id):
            raise TicketError("Choose the ClickUp list for the ticket.", 422)
        title = str(body.get("title") or "").strip()
        if not title:
            raise TicketError("Enter a ticket title.", 422)
        token = str(body.get("attempt_token") or "")
        if not re.match(r"^[A-Za-z0-9]{8,64}$", token):
            raise TicketError("The ticket form is out of date. Close it and open it again.", 422)
        if r["ticket_status"] == "Created" and not body.get("create_another"):
            raise TicketError("A ClickUp ticket was already created for this result.", 409, existing=r["tickets"][-1], code="exists")
        key = hashlib.sha256(f"{result_id}|{cfg['id']}|{list_id}|{token}".encode()).hexdigest()
        prior = self.results.find_by_key(key)
        if prior and prior["outcome"] == "Created":
            return {"status": "exists", "ticket": prior, "warnings": []}          # a double click or retry of the same attempt
        if prior and prior["outcome"] == "Pending":
            raise TicketError("This ticket is already being created.", 409, code="pending")
        task_type = str(body.get("task_type") or TASK_TYPES[r["result"]])[:30]
        if prior is None:
            self.results.add_link(r["run_id"], result_id, key, cfg["id"], list_id, task_type, "Pending", user, title=title)
        else:                           # an earlier attempt with this token failed: retry it
            self.results.update_link(key, outcome="Pending", error="")
        try:
            # the destination must exist and be reachable with the saved token
            self.service.discover_clickup_values({"configuration_id": cfg["id"], "workspace_id": str(s.get("workspace_id") or ""), "space_id": str(s.get("space_id") or "")}, "metadata", list_id)
            payload = {"name": title, "description": str(body.get("description") or description_for(run, r))[:30000]}
            prio = body.get("priority")
            if isinstance(prio, int) and 1 <= prio <= 4:
                payload["priority"] = prio
            if body.get("status"):
                payload["status"] = str(body["status"])[:80]
            assignees = [int(a) for a in (body.get("assignees") or []) if str(a).isdigit()][:10]
            if assignees:
                payload["assignees"] = assignees
            tags = [str(t)[:50] for t in (body.get("tags") or []) if str(t).strip()][:12]
            if tags:
                payload["tags"] = tags
            if int(s.get("custom_item_id") or 0):
                payload["custom_item_id"] = int(s["custom_item_id"])
            created = self.service._request(cfg, "POST", f"/list/{list_id}/task", payload)
            task_id, url = str(created.get("id") or ""), str(created.get("url") or "")
            if not task_id:
                raise ValueError("ClickUp did not return a task ID.")
            if url and urlsplit(url).scheme != "https":
                url = ""
        except ValueError as error:
            self.results.update_link(key, outcome="Failed", error=_mask(error, ""))
            raise TicketError("The ClickUp ticket was not created: " + _mask(error, ""), 502, code="clickup_failed") from None
        except Exception as error:      # noqa: BLE001 - nothing may escape with a secret in it
            self.results.update_link(key, outcome="Failed", error="Unexpected error while contacting ClickUp.")
            raise TicketError("The ClickUp ticket was not created because of an unexpected error.", 502, code="clickup_failed") from error
        attachments, warnings = [], []
        ev = r.get("evidence") or {}
        wanted = body.get("attach") if isinstance(body.get("attach"), list) else ["design", "actual", "diff"]
        names = {"design": "expected-design", "actual": "actual-website", "diff": "difference"}
        for slot in ("design", "actual", "diff"):
            evid = ev.get(slot)
            if slot not in wanted or not evid:
                continue
            try:
                data, mime = self.repo.evidence_bytes(evid)
                if len(data) > MAX_ATTACHMENT:
                    raise ValueError("The image is larger than 10 MB.")
                self.uploader(cfg["id"], task_id, f"{r['test_id']}-{names[slot]}.png", data)
                attachments.append({"slot": slot, "name": f"{r['test_id']}-{names[slot]}.png", "status": "uploaded"})
            except Exception as error:      # noqa: BLE001
                attachments.append({"slot": slot, "name": f"{r['test_id']}-{names[slot]}.png", "status": "failed", "error": _mask(error, "")})
                warnings.append(f"The {names[slot].replace('-', ' ')} image could not be attached to ClickUp ({_mask(error, '')[:120]}). It is available in QA AI Studio under {r['test_id']}.")
        link = self.results.update_link(key, outcome="Created", task_id=task_id, task_url=url, task_status=str(created.get("status", {}).get("status", "") if isinstance(created.get("status"), dict) else created.get("status") or ""),
                                       attachments=attachments, task_title=title)
        return {"status": "created", "ticket": link, "warnings": warnings}

    # ------------------------------------------------------------------ file upload (multipart, HTTPS only, secret masked)
    def _multipart_upload(self, config_id, task_id: str, filename: str, data: bytes):
        raw = self.service.repository.get_config(int(config_id), include_secret=True)
        base = (raw.get("base_url") or "https://api.clickup.com/api/v2").rstrip("/")
        if not base.lower().startswith("https://"):
            raise ValueError("External integration URL must use HTTPS.")
        if not ID_RE.match(task_id):
            raise ValueError("Invalid ClickUp task ID.")
        boundary = "----qaai" + secrets.token_hex(12)
        safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", filename)[:100]
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"attachment\"; filename=\"{safe_name}\"\r\nContent-Type: image/png\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
        request = urllib.request.Request(f"{base}/task/{task_id}/attachment", data=body, method="POST",
                                         headers={"Authorization": raw.get("secret") or "", "Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                payload = json.loads(response.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as error:
            raise ValueError(_mask(f"ClickUp rejected the attachment (HTTP {error.code})", raw.get("secret") or "")) from None
        except (urllib.error.URLError, TimeoutError) as error:
            raise ValueError("ClickUp could not be reached to upload the attachment.") from None
        if not (payload.get("id") or payload.get("url")):
            raise ValueError("ClickUp did not confirm the attachment.")
