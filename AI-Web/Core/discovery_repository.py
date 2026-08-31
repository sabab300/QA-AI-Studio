# Create: AI/Core/discovery_repository.py

"""
QA AI Studio
Discovery Repository

Version: 1.0

Persists what URLDiscoveryEngine.discover() finds into the
discovery_* schema tables (Application -> Business Process ->
Variant -> Page -> Tab -> [Section] -> Element, plus Workflow Step
and Session logging). Without this, every discovery run's data dies
the moment the app closes — this is the missing link that makes
discovery data actually reach Knowledge Hub.

Naming, for now (per explicit decision, until the LLM semantic
classification step exists):
    - Application name: the URL's hostname, unless the caller
      passes an explicit one. Honest and traceable — never guesses
      at a business-friendly name like "PSW" from a domain string.
    - Business Process / Variant: derived from the URL's path
      segments (first segment -> Business Process, second -> "General"
      / "Default" fallback when the path is too shallow). These are
      placeholder names, not real business classification — the
      docstring on save_discovery_result() says so again at the call
      site so this doesn't get mistaken for the real thing later.
    - Page name: the page title if one was captured, else the URL
      path.
    - Tab: every tab label the engine found becomes its own Tab row
      (so the tree shows what tabs exist), but a single-page scan
      can only ever discover the elements of whichever tab was
      showing at scan time — this module has no reliable signal for
      *which* tab that was, so it does not guess. Discovered
      elements attach to a single synthetic "(scanned view)" tab
      rather than being mis-attributed to a specific tab label. The
      other tab rows are created empty, ready for a future
      multi-tab crawl pass to fill in.

Locator quality — the fix called out from testing on a prior pass:
a locator built from nothing but tag+type (e.g. "input[type='text']")
matches every similar field on the page and is not usable, reliable
automation knowledge. This module refuses to store one. Fields with
no usable signal (no data-testid / id / name / aria-label /
placeholder) are counted and reported as skipped, not silently
dropped and not stored with a misleading locator.
"""

import hashlib
import json
from datetime import datetime
from urllib.parse import urlparse

from Database.db_manager import DatabaseManager
from Core.logger import Logger
from Core.metadata_manager import MetadataManager
from Core.url_discovery_engine import (
    build_xpath_alternative,
    looks_dynamically_generated,
    stable_locator_prefix,
)


DEFAULT_BUSINESS_PROCESS = "General"

DEFAULT_VARIANT = "Default"

SCANNED_VIEW_TAB_NAME = "(scanned view)"

# Marks a knowledge_items row that exists only to file a captured
# URL/business-flow under the Domain -> Module -> Knowledge Name ->
# Version tree — there is no uploaded file behind it, so it is never
# treated as a document by anything that expects one (embedding
# queue, file preview, etc.). manage_knowledge_page.py checks this
# to render its children (steps/fields) instead of a file preview.
CAPTURED_FLOW_SOURCE_TYPE = "URL_CAPTURE"

CAPTURED_FLOW_DOCUMENT_TYPE = "URL Capture"

DEFAULT_CAPTURED_VERSION = "1.0"


class DiscoveryRepository:

    def __init__(self):

        self.logger = Logger.get_logger()

        self.db = DatabaseManager()

    # ================================================================
    # Public entry point
    # ================================================================

    def save_discovery_result(
        self,
        discover_result,
        source_url,
        auth_type=None,
        application_name=None,
        business_process_name=None,
        variant_name=None,
    ):
        """
        Persists one URLDiscoveryEngine.discover() result.

        `application_name` / `business_process_name` /
        `variant_name` are optional overrides. Leave them out and
        this auto-names from the URL — reasonable as a placeholder,
        NOT a substitute for the real per-page LLM classification
        step (Business Process / Variant is a semantic judgment call
        a URL path can't reliably make; see module docstring).

        Returns:
            {
                "success": bool,
                "session_id": int,
                "application_id": int | None,
                "business_process_id": int | None,
                "variant_id": int | None,
                "page_id": int | None,
                "tabs_created": int,
                "elements_saved": int,
                "elements_skipped_low_confidence": int,
                "skipped_elements": [ {reason, tag, text}, ... ],
                "error": str | None,
            }
        """

        summary = {
            "success": False,
            "session_id": None,
            "application_id": None,
            "business_process_id": None,
            "variant_id": None,
            "page_id": None,
            "tabs_created": 0,
            "elements_saved": 0,
            "elements_skipped_low_confidence": 0,
            "skipped_elements": [],
            "error": None,
        }

        parsed = urlparse(source_url)

        session_id = self._start_session(source_url, auth_type)

        summary["session_id"] = session_id

        try:

            if not discover_result.get("success"):

                error = discover_result.get(
                    "error", "Discovery result reported failure."
                )

                summary["error"] = error

                self._finish_session(
                    session_id, status="Failed", error_message=error
                )

                return summary

            final_url = discover_result.get("final_url") or source_url

            page_data = (
                discover_result["pages"][0]
                if discover_result.get("pages")
                else {}
            )

            # --------------------------------------------------
            # Hierarchy: Application -> Business Process -> Variant
            # --------------------------------------------------

            app_name = application_name or (
                parsed.hostname or source_url
            )

            application_id = self._get_or_create_application(
                app_name, base_url=f"{parsed.scheme}://{parsed.hostname}"
            )

            summary["application_id"] = application_id

            bp_name = (
                business_process_name
                or self._first_path_segment(final_url)
                or DEFAULT_BUSINESS_PROCESS
            )

            business_process_id = self._get_or_create_business_process(
                application_id, bp_name
            )

            summary["business_process_id"] = business_process_id

            var_name = (
                variant_name
                or self._second_path_segment(final_url)
                or DEFAULT_VARIANT
            )

            variant_id = self._get_or_create_variant(
                business_process_id, var_name
            )

            summary["variant_id"] = variant_id

            # --------------------------------------------------
            # Page
            # --------------------------------------------------

            page_title = page_data.get("title") or ""

            page_name = page_title or self._path_only(final_url)

            page_id = self._get_or_create_page(
                variant_id, page_name, final_url, page_title
            )

            summary["page_id"] = page_id

            # --------------------------------------------------
            # Tabs + Elements
            #
            # Preferred path: discover_result["tab_scans"] (one
            # entry per tab actually visited, each with its own
            # fields/buttons — see URLDiscoveryEngine's multi-tab
            # walk). Elements attach to the tab they were actually
            # found under.
            #
            # Fallback path: older/simpler discover_result shapes
            # with no tab_scans — everything attaches to a single
            # synthetic "(scanned view)" tab, same as before.
            # --------------------------------------------------

            tab_scans = discover_result.get("tab_scans") or []

            tabs_created = 0

            elements_saved = 0

            elements_skipped = 0

            skipped_elements = []

            visited_tab_names = set()

            if tab_scans:

                for order, tab_scan in enumerate(tab_scans):

                    tab_name = (
                        tab_scan.get("tab_name") or SCANNED_VIEW_TAB_NAME
                    )

                    tab_id = self._get_or_create_tab(
                        page_id, tab_name, tab_order=order
                    )

                    tabs_created += 1

                    visited_tab_names.add(tab_name.strip().lower())

                    candidates = list(
                        tab_scan.get("fields", [])
                    ) + list(tab_scan.get("buttons", []))

                    saved, skipped, skipped_detail = (
                        self._save_elements_for_tab(tab_id, candidates)
                    )

                    elements_saved += saved

                    elements_skipped += skipped

                    skipped_elements.extend(skipped_detail)

                # Placeholder rows for any tab label that was seen
                # on the page but never successfully clicked (e.g.
                # disabled at scan time) — empty for now, ready for
                # a future pass.
                for order, tab_entry in enumerate(
                    discover_result.get("tabs", []), start=len(tab_scans)
                ):

                    label = (
                        tab_entry.get("text")
                        or tab_entry.get("aria_label")
                        or tab_entry.get("title")
                    )

                    if not label:

                        continue

                    if label.strip().lower() in visited_tab_names:

                        continue

                    self._get_or_create_tab(
                        page_id, label.strip(), tab_order=order
                    )

                    tabs_created += 1

            else:

                scanned_tab_id = self._get_or_create_tab(
                    page_id, SCANNED_VIEW_TAB_NAME, tab_order=0
                )

                tabs_created = 1

                for order, tab_entry in enumerate(
                    discover_result.get("tabs", []), start=1
                ):

                    label = (
                        tab_entry.get("text")
                        or tab_entry.get("aria_label")
                        or tab_entry.get("title")
                    )

                    if not label:

                        continue

                    if label.strip() == SCANNED_VIEW_TAB_NAME:

                        continue

                    self._get_or_create_tab(
                        page_id, label.strip(), tab_order=order
                    )

                    tabs_created += 1

                candidates = list(
                    discover_result.get("fields", [])
                ) + list(discover_result.get("buttons", []))

                elements_saved, elements_skipped, skipped_elements = (
                    self._save_elements_for_tab(
                        scanned_tab_id, candidates
                    )
                )

            summary["tabs_created"] = tabs_created

            summary["elements_saved"] = elements_saved

            summary["elements_skipped_low_confidence"] = elements_skipped

            summary["skipped_elements"] = skipped_elements

            summary["success"] = True

            self._finish_session(
                session_id,
                status="Completed",
                pages_discovered_count=1,
                elements_discovered_count=summary["elements_saved"],
                application_id=application_id,
            )

        except Exception as error:

            self.logger.exception("Discovery Repository save failed.")

            summary["error"] = str(error)

            self._finish_session(
                session_id, status="Failed", error_message=str(error)
            )

        return summary

    # ================================================================
    # Guided business-flow capture (AI Smart Upload -> URL Knowledge)
    # ================================================================

    def save_flow_result(
        self,
        steps,
        source_url,
        auth_type=None,
        application_name=None,
        business_process_name=None,
        variant_name=None,
        domain=None,
        module=None,
        knowledge_name=None,
        version=None,
    ):
        """
        Persists a full, operator-confirmed, multi-step business-flow
        capture — the "login, then walk the real business flow to
        Submit, confirming each screen along the way" feature.

        Unlike save_discovery_result() (a single shallow scan),
        `steps` is an ORDERED list of screens the operator actually
        walked through and reviewed one at a time in
        DiscoveryStepConfirmationDialog before it ever reached this
        method — every element in it has already been accepted or
        hand-corrected by a person, so nothing here is silently
        dropped for a low-confidence locator the way
        save_discovery_result()'s single-shot path can. It also
        populates discovery_workflow_steps (previously never written
        to by anything), which is what lets QA Automation replay the
        flow in the recorded order instead of guessing which screen
        follows which, and lets QA Engineering / the AI Assistant
        answer "what fields does step 3 of SD Registration have"
        directly from stored knowledge instead of re-discovering it.

        `steps`: ordered list of
            {
                "step_name": str,
                "page_name": str,
                "page_url": str,
                "page_title": str,
                "tab_name": str,
                "is_end_step": bool,
                "elements": [
                    {
                        "name": str,
                        "element_type": str,
                        "locator": str,
                        "locator_strategy": str,
                        "alternate_locators": [ {strategy, locator}, ... ] | None,
                        "placeholder": str,
                        "is_required": bool,
                    },
                    ...
                ],
            }

        Returns a summary shaped like save_discovery_result()'s, plus
        "steps_saved" and "workflow_step_ids".
        """

        summary = {
            "success": False,
            "session_id": None,
            "application_id": None,
            "business_process_id": None,
            "variant_id": None,
            "page_id": None,
            "tabs_created": 0,
            "elements_saved": 0,
            "elements_skipped_low_confidence": 0,
            "skipped_elements": [],
            "steps_saved": 0,
            "workflow_step_ids": [],
            "knowledge_item_id": None,
            "error": None,
        }

        parsed = urlparse(source_url)

        session_id = self._start_session(source_url, auth_type)

        summary["session_id"] = session_id

        if not steps:

            summary["error"] = "No steps were captured for this flow."

            self._finish_session(
                session_id, status="Failed", error_message=summary["error"]
            )

            return summary

        try:

            app_name = application_name or (parsed.hostname or source_url)

            application_id = self._get_or_create_application(
                app_name, base_url=f"{parsed.scheme}://{parsed.hostname}"
            )

            summary["application_id"] = application_id

            bp_name = (
                business_process_name
                or self._first_path_segment(source_url)
                or DEFAULT_BUSINESS_PROCESS
            )

            business_process_id = self._get_or_create_business_process(
                application_id, bp_name
            )

            summary["business_process_id"] = business_process_id

            var_name = (
                variant_name
                or self._second_path_segment(source_url)
                or DEFAULT_VARIANT
            )

            knowledge_item_id = self._get_or_create_captured_knowledge_item(
                domain, module, knowledge_name, version
            )

            summary["knowledge_item_id"] = knowledge_item_id

            variant_id = self._get_or_create_variant(
                business_process_id,
                var_name,
                linked_knowledge_item_id=knowledge_item_id,
            )

            summary["variant_id"] = variant_id

            previous_step_id = None

            last_page_id = None

            for order, step in enumerate(steps):

                page_url = step.get("page_url") or source_url

                page_name = (
                    step.get("page_name")
                    or step.get("step_name")
                    or f"Step {order + 1}"
                )

                page_title = step.get("page_title") or ""

                page_id = self._get_or_create_page(
                    variant_id, page_name, page_url, page_title
                )

                last_page_id = page_id

                tab_name = step.get("tab_name") or SCANNED_VIEW_TAB_NAME

                tab_id = self._get_or_create_tab(page_id, tab_name, tab_order=0)

                summary["tabs_created"] += 1

                saved, skipped, skipped_detail = (
                    self._save_confirmed_elements_for_tab(
                        tab_id, step.get("elements", [])
                    )
                )

                summary["elements_saved"] += saved

                summary["elements_skipped_low_confidence"] += skipped

                summary["skipped_elements"].extend(skipped_detail)

                step_name = step.get("step_name") or page_name

                is_end_step = bool(step.get("is_end_step"))

                workflow_step_id = self._add_workflow_step(
                    variant_id=variant_id,
                    step_order=order + 1,
                    page_id=page_id,
                    tab_id=tab_id,
                    step_name=step_name,
                    depends_on_step_id=previous_step_id,
                    is_end_step=is_end_step,
                )

                summary["workflow_step_ids"].append(workflow_step_id)

                previous_step_id = workflow_step_id

                summary["steps_saved"] += 1

            summary["page_id"] = last_page_id

            summary["success"] = True

            self._finish_session(
                session_id,
                status="Completed",
                pages_discovered_count=summary["steps_saved"],
                elements_discovered_count=summary["elements_saved"],
                application_id=application_id,
            )

        except Exception as error:

            self.logger.exception("Discovery Repository save_flow_result failed.")

            summary["error"] = str(error)

            self._finish_session(
                session_id, status="Failed", error_message=str(error)
            )

        return summary

    def _save_confirmed_elements_for_tab(self, tab_id, elements):
        """
        Same shape of return as _save_elements_for_tab(), but for
        elements that have ALREADY been reviewed and accepted (or
        hand-corrected) by the operator in the confirmation dialog —
        so this only re-validates that a locator is actually present
        rather than re-deriving one from raw attributes.
        """

        saved = 0

        skipped = 0

        skipped_detail = []

        seen_in_this_tab = set()

        for el in elements:

            locator = (el.get("locator") or "").strip()

            if not locator:

                skipped += 1

                skipped_detail.append(
                    {
                        "reason": "no locator provided",
                        "tag": el.get("element_type", ""),
                        "text": (el.get("name") or "")[:80],
                    }
                )

                continue

            if locator in seen_in_this_tab:

                continue

            seen_in_this_tab.add(locator)

            alternates = el.get("alternate_locators")

            alternates_json = json.dumps(alternates) if alternates else None

            self._add_element(
                tab_id=tab_id,
                name=(el.get("name") or "")[:200],
                element_type=el.get("element_type") or "",
                locator=locator,
                locator_strategy=el.get("locator_strategy") or "operator-confirmed",
                alternate_locators=alternates_json,
                placeholder=el.get("placeholder", ""),
                is_required=bool(el.get("is_required")),
            )

            saved += 1

        return saved, skipped, skipped_detail

    def _add_workflow_step(
        self,
        variant_id,
        step_order,
        page_id,
        tab_id,
        step_name,
        depends_on_step_id,
        is_end_step,
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        now = datetime.now().isoformat()

        cursor.execute(
            """
            INSERT INTO discovery_workflow_steps
            (variant_id, step_order, page_id, tab_id, step_name,
             depends_on_step_id, is_end_step, created_date, modified_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                variant_id,
                step_order,
                page_id,
                tab_id,
                step_name,
                depends_on_step_id,
                1 if is_end_step else 0,
                now,
                now,
            ),
        )

        step_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return step_id

    # ================================================================
    # Read-back APIs for downstream features
    #
    # This is the point of storing the flow in the first place: QA
    # Automation should be able to replay a captured business
    # process step by step instead of guessing, QA Engineering
    # should be able to generate test cases against real fields
    # instead of assumed ones, and the AI Assistant should be able
    # to answer questions about the application from stored fact
    # rather than from a fresh (and possibly re-triggered) discovery
    # run.
    # ================================================================

    def find_variant_id(
        self, application_name, business_process_name, variant_name
    ):
        """
        Looks up a previously captured flow's variant id by its name
        chain, without re-running discovery.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "SELECT id FROM discovery_applications WHERE name = ?",
            (application_name,),
        )

        row = cursor.fetchone()

        if not row:

            conn.close()

            return None

        application_id = row[0]

        cursor.execute(
            """
            SELECT id FROM discovery_business_processes
            WHERE application_id = ? AND name = ?
            """,
            (application_id, business_process_name),
        )

        row = cursor.fetchone()

        if not row:

            conn.close()

            return None

        business_process_id = row[0]

        cursor.execute(
            """
            SELECT id FROM discovery_variants
            WHERE business_process_id = ? AND name = ?
            """,
            (business_process_id, variant_name),
        )

        row = cursor.fetchone()

        conn.close()

        return row[0] if row else None

    def get_flow_by_variant(self, variant_id):
        """
        Returns the ordered list of workflow steps recorded for a
        variant, each with its page/tab identity and the elements
        captured on it — the shape QA Automation / QA Engineering /
        the AI Assistant can consume directly.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT ws.id, ws.step_order, ws.step_name, ws.is_end_step,
                   p.id, p.name, p.url, t.id, t.name
            FROM discovery_workflow_steps ws
            JOIN discovery_pages p ON p.id = ws.page_id
            LEFT JOIN discovery_tabs t ON t.id = ws.tab_id
            WHERE ws.variant_id = ?
            ORDER BY ws.step_order ASC
            """,
            (variant_id,),
        )

        rows = cursor.fetchall()

        steps = []

        for (
            step_id, step_order, step_name, is_end_step,
            page_id, page_name, page_url, tab_id, tab_name,
        ) in rows:

            elements = []

            if tab_id:

                cursor.execute(
                    """
                    SELECT name, element_type, locator, locator_strategy,
                           alternate_locators, placeholder, is_required
                    FROM discovery_elements
                    WHERE tab_id = ? AND status = 'Active'
                    """,
                    (tab_id,),
                )

                for (
                    name, element_type, locator, locator_strategy,
                    alternate_locators, placeholder, is_required,
                ) in cursor.fetchall():

                    elements.append(
                        {
                            "name": name,
                            "element_type": element_type,
                            "locator": locator,
                            "locator_strategy": locator_strategy,
                            "alternate_locators": (
                                json.loads(alternate_locators)
                                if alternate_locators
                                else []
                            ),
                            "placeholder": placeholder,
                            "is_required": bool(is_required),
                        }
                    )

            steps.append(
                {
                    "step_id": step_id,
                    "step_order": step_order,
                    "step_name": step_name,
                    "is_end_step": bool(is_end_step),
                    "page_id": page_id,
                    "page_name": page_name,
                    "page_url": page_url,
                    "tab_id": tab_id,
                    "tab_name": tab_name,
                    "elements": elements,
                }
            )

        conn.close()

        return steps

    def get_captured_flows_for_knowledge_item(self, knowledge_item_id):
        """
        The other direction of the link: given a Knowledge Hub node
        (a knowledge_items.id), returns every captured business flow
        filed under it, each with its full ordered step/element
        detail from get_flow_by_variant(). This is what
        manage_knowledge_page.py calls to nest a captured flow's
        Steps -> Fields/Locators under the matching row in the tree.
        """

        if not knowledge_item_id:

            return []

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT dv.id, dv.name, dbp.name, da.name
            FROM discovery_variants dv
            JOIN discovery_business_processes dbp
                ON dbp.id = dv.business_process_id
            JOIN discovery_applications da
                ON da.id = dbp.application_id
            WHERE dv.linked_knowledge_item_id = ?
            """,
            (knowledge_item_id,),
        )

        rows = cursor.fetchall()

        conn.close()

        flows = []

        for variant_id, variant_name, business_process_name, application_name in rows:

            flows.append(
                {
                    "variant_id": variant_id,
                    "variant_name": variant_name,
                    "business_process_name": business_process_name,
                    "application_name": application_name,
                                        "steps": self.get_flow_by_variant(variant_id),
                }
            )

        return flows

    def get_elements_for_scope(self, domain, module, knowledge_name):
        """
        Returns every element captured by ANY URL Knowledge Capture
        filed under this Domain / Module / Knowledge Name (any
        version, any number of separate capture sessions) — this is
        what grounds Playwright script generation: a test case filed
        under the same Domain/Module/Knowledge Name as a capture can
        pull its REAL locators (including any XPath alternate)
        instead of the AI guessing. Mirrors
        ApiCollectionRepository.get_endpoints_for_scope() for API
        Automation.

        Each returned element dict also carries page_name/step_name
        (which screen/step it came from) so a prompt can show that
        for context — everything else is exactly the shape
        get_flow_by_variant() already returns per element (name,
        element_type, locator, locator_strategy, alternate_locators,
        placeholder, is_required).
        """

        domain = (domain or "").strip()

        module = (module or "").strip()

        knowledge_name = (knowledge_name or "").strip()

        if not domain or not module or not knowledge_name:

            return []

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id FROM knowledge_items
            WHERE domain = ? AND module = ? AND knowledge_name = ?
              AND source_type = ?
            """,
            (domain, module, knowledge_name, CAPTURED_FLOW_SOURCE_TYPE),
        )

        knowledge_item_ids = [row[0] for row in cursor.fetchall()]

        conn.close()

        elements = []

        for knowledge_item_id in knowledge_item_ids:

            flows = self.get_captured_flows_for_knowledge_item(
                knowledge_item_id
            )

            for flow in flows:

                for step in flow.get("steps", []):

                    for element in step.get("elements", []):

                        elements.append(
                            dict(
                                element,
                                page_name=step.get("page_name") or "",
                                step_name=step.get("step_name") or "",
                            )
                        )

        return elements    

    # ================================================================
    # Locator quality
    # ================================================================

    def _save_elements_for_tab(self, tab_id, candidates):
        """
        Saves whichever of `candidates` have a usable locator under
        `tab_id`, deduplicating by locator within this tab (the
        underlying insert is already tab-scoped, so the same
        locator text legitimately appearing under two different
        tabs is NOT treated as a duplicate — only repeats within the
        same tab are).

        Returns (saved_count, skipped_count, skipped_detail_list).
        """

        saved = 0

        skipped = 0

        skipped_detail = []

        seen_in_this_tab = set()

        for candidate in candidates:

            built = self._build_locator(candidate)

            if built is None:

                skipped += 1

                skipped_detail.append(
                    {
                        "reason": "no usable locator signal "
                        "(no data-testid / id / name / "
                        "aria-label / placeholder)",
                        "tag": candidate.get("tag", ""),
                        "text": candidate.get("text", "")[:80],
                    }
                )

                continue

            locator, strategy, alternates = built

            if locator in seen_in_this_tab:

                continue

            seen_in_this_tab.add(locator)

            self._add_element(
                tab_id=tab_id,
                name=(
                    candidate.get("text")
                    or candidate.get("aria_label")
                    or candidate.get("placeholder")
                    or candidate.get("name")
                    or ""
                )[:200],
                element_type=(
                    candidate.get("type") or candidate.get("tag") or ""
                ),
                locator=locator,
                locator_strategy=strategy,
                alternate_locators=alternates,
                placeholder=candidate.get("placeholder", ""),
                is_required=bool(candidate.get("required")),
            )

            saved += 1

        return saved, skipped, skipped_detail

    def _build_locator(self, candidate):
        """
        Returns (locator, strategy, alternates_json) or None if
        nothing usable enough exists. Priority: data-testid > id
        (unless it looks dynamically generated) > name (same caveat)
        > aria-label > placeholder > short visible text > an XPath
        fallback built from a dynamic-looking id/name's stable
        prefix > an already-computed engine locator (if it isn't a
        bare tag/type guess). A bare tag/type combination on its own
        (e.g. "input[type='text']") is still never accepted as a
        PRIMARY locator — it matches too many elements on a real
        page to be trustworthy automation knowledge.

        Fix (this pass): candidates coming from
        URLDiscoveryEngine.scan_current_view() now carry id / name /
        data-testid / aria-label / placeholder / text on EVERY
        element type, including buttons and links — previously
        button/link candidate dicts only ever had "locator" and
        "text" (sometimes not even that), so nearly every button
        failed every check below and was silently skipped, which is
        exactly why Playwright automation built on top of this data
        kept coming out empty or unreliable. The two additions below
        (short visible text, and trusting the engine's own locator
        when it's better than a bare tag/type guess) are what let
        icon-less nav buttons and links survive without an id/name.
        """

        tag = candidate.get("tag") or candidate.get("tag_name") or "input"

        alternates = []

        primary = None

        strategy = None

        data_testid = candidate.get("data_testid")

        if data_testid:

            css = f"[data-testid='{data_testid}']"

            alternates.append({"strategy": "data-testid", "locator": css})

            primary, strategy = css, "data-testid"

            element_id = candidate.get("id")

        # An id that looks freshly generated (a UUID, a long digit/hex
        # run, one long opaque token — see looks_dynamically_generated()
        # in url_discovery_engine.py) is never trusted as a PRIMARY
        # locator here: it may pass today and silently stop matching
        # anything the next time the framework regenerates it. Still
        # recorded as an alternate for reference — just never promoted
        # to primary the way a hand-authored id is.
        id_is_dynamic = bool(element_id) and looks_dynamically_generated(element_id)

        if element_id:

            css = f"#{element_id}"

            alternates.append({"strategy": "id", "locator": css})

            if primary is None and not id_is_dynamic:

                primary, strategy = css, "id"

        name = candidate.get("name")

        name_is_dynamic = bool(name) and looks_dynamically_generated(name)

        if name:

            css = f"{tag}[name='{name}']"

            alternates.append({"strategy": "name", "locator": css})

            if primary is None and not name_is_dynamic:

                primary, strategy = css, "name"

        aria_label = candidate.get("aria_label")

        if aria_label:

            css = f"{tag}[aria-label='{aria_label}']"

            alternates.append({"strategy": "aria-label", "locator": css})

            if primary is None:

                primary, strategy = css, "aria-label"

        placeholder = candidate.get("placeholder")

        if placeholder:

            css = f"{tag}[placeholder='{placeholder}']"

            alternates.append({"strategy": "placeholder", "locator": css})

            if primary is None:

                primary, strategy = css, "placeholder"

                text = (candidate.get("text") or "").strip()

        if text and len(text) < 40:

            css = f"{tag}:has-text('{text}')"

            alternates.append({"strategy": "text", "locator": css})

            if primary is None:

                primary, strategy = css, "text"

        # Nothing stable found yet, but there WAS an id/name — it was
        # just dynamic-looking. Rather than fall straight through to
        # "no usable locator", try to salvage an XPath built on
        # whatever hand-authored prefix survives stripping the
        # dynamic-looking tail (e.g. "field-8827261" -> "field"). This
        # is the same fallback URLDiscoveryEngine._generate_locator()
        # applies itself — kept here too since this function derives
        # its own primary/strategy independently rather than always
        # trusting the engine's precomputed candidate["locator"].
        if primary is None and id_is_dynamic:

            stable = stable_locator_prefix(element_id)

            if stable:

                xpath = f"//{tag}[starts-with(@id, '{stable}')]"

                alternates.append(
                    {"strategy": "xpath-dynamic-id", "locator": xpath}
                )

                primary, strategy = xpath, "xpath-dynamic-id"

        if primary is None and name_is_dynamic:

            stable = stable_locator_prefix(name)

            if stable:

                xpath = f"//{tag}[starts-with(@name, '{stable}')]"

                alternates.append(
                    {"strategy": "xpath-dynamic-name", "locator": xpath}
                )

                primary, strategy = xpath, "xpath-dynamic-name"

        engine_locator = candidate.get("locator")

        engine_strategy = candidate.get("locator_strategy")

        if (
            primary is None
            and engine_locator
            and engine_strategy not in (None, "tag-only", "type")
        ):

            alternates.append(
                {"strategy": f"engine-{engine_strategy}", "locator": engine_locator}
            )

            primary, strategy = engine_locator, f"engine-{engine_strategy}"

        if primary is None:

            return None

        # Whatever ended up PRIMARY above, also make sure a generic
        # XPath alternate is on hand in the database when one is
        # buildable and isn't just a duplicate of something already
        # recorded — not only for the dynamic-id/name fallback case
        # above, so every element carries an XPath option regardless
        # of how confident its primary locator already is.
        xpath_alternative = build_xpath_alternative(tag, candidate)

        if (
            xpath_alternative
            and xpath_alternative != primary
            and xpath_alternative not in (a["locator"] for a in alternates)
        ):

            alternates.append(
                {"strategy": "xpath", "locator": xpath_alternative}
            )

        return primary, strategy, json.dumps(alternates)

    # ================================================================
    # URL-based auto-naming helpers
    # ================================================================

    def _first_path_segment(self, url):

        segments = [
            segment
            for segment in urlparse(url).path.split("/")
            if segment
        ]

        return segments[0] if segments else None

    def _second_path_segment(self, url):

        segments = [
            segment
            for segment in urlparse(url).path.split("/")
            if segment
        ]

        return segments[1] if len(segments) > 1 else None

    def _path_only(self, url):

        parsed = urlparse(url)

        return parsed.path or url

    # ================================================================
    # Get-or-create hierarchy helpers
    # ================================================================

    def _get_or_create_application(self, name, base_url):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "SELECT id FROM discovery_applications WHERE name = ?",
            (name,),
        )

        row = cursor.fetchone()

        if row:

            application_id = row[0]

        else:

            now = datetime.now().isoformat()

            cursor.execute(
                """
                INSERT INTO discovery_applications
                (name, base_url, status, created_date, modified_date)
                VALUES (?, ?, 'Active', ?, ?)
                """,
                (name, base_url, now, now),
            )

            application_id = cursor.lastrowid

            conn.commit()

        conn.close()

        return application_id    

    def _get_or_create_business_process(self, application_id, name):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id FROM discovery_business_processes
            WHERE application_id = ? AND name = ?
            """,
            (application_id, name),
        )

        row = cursor.fetchone()

        if row:

            business_process_id = row[0]

        else:

            now = datetime.now().isoformat()

            cursor.execute(
                """
                INSERT INTO discovery_business_processes
                (application_id, name, status, created_date, modified_date)
                VALUES (?, ?, 'Active', ?, ?)
                """,
                (application_id, name, now, now),
            )

            business_process_id = cursor.lastrowid

            conn.commit()

        conn.close()

        return business_process_id

    def _get_or_create_captured_knowledge_item(
        self, domain, module, knowledge_name, version
    ):
        """
        Files a captured business flow under the SAME Domain ->
        Module -> Knowledge Name -> Version tree that uploaded
        documents live in, by creating (or reusing) a placeholder
        knowledge_items row for it.

        Returns None (no linkage — flow is saved unlinked, exactly
        as before this feature existed) unless domain, module AND
        knowledge_name are all given; version is optional and
        defaults to DEFAULT_CAPTURED_VERSION.

        Re-running a capture against the same Domain/Module/Knowledge
        Name/Version reuses the existing row rather than creating a
        duplicate node in the tree every time.
        """

        domain = (domain or "").strip()

        module = (module or "").strip()

        knowledge_name = (knowledge_name or "").strip()

        version = (version or "").strip() or DEFAULT_CAPTURED_VERSION

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
            (domain, module, knowledge_name, version, CAPTURED_FLOW_SOURCE_TYPE),
        )

        row = cursor.fetchone()

        if row:

            conn.close()

            return row[0]

        conn.close()

        metadata_manager = MetadataManager()

        domain_id = metadata_manager.get_or_create_domain(domain)

        module_id = metadata_manager.get_or_create_module(domain, module)

        knowledge_path = f"{domain}/{module}/{knowledge_name}/{version}"

        sentinel_sha256 = hashlib.sha256(
            f"captured-flow::{knowledge_path}".encode("utf-8")
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
            VALUES (?, ?, ?, ?, ?, ?, NULL, ?, ?, 0, '', 'CAPTURED_FLOW',
                    ?, ?, 'ACTIVE', ?, ?, ?, ?)
            """,
            (
                domain,
                module,
                knowledge_name,
                version,
                knowledge_path,
                "(no file — captured business flow)",
                knowledge_path,
                sentinel_sha256,
                CAPTURED_FLOW_SOURCE_TYPE,
                CAPTURED_FLOW_DOCUMENT_TYPE,
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

    def _get_or_create_variant(
        self, business_process_id, name, linked_knowledge_item_id=None
    ):
        """
        `linked_knowledge_item_id`, when provided, is what ties this
        captured URL-knowledge variant to a Domain/Module/Knowledge
        Name/Version node in the document tree — see
        _get_or_create_captured_knowledge_item(). Kept in sync on
        every call (not just at creation) so re-running a capture
        against a variant that predates this link still picks it up.
        """

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id FROM discovery_variants
            WHERE business_process_id = ? AND name = ?
            """,
            (business_process_id, name),
        )

        row = cursor.fetchone()

        now = datetime.now().isoformat()

        if row:

            variant_id = row[0]

            if linked_knowledge_item_id is not None:

                cursor.execute(
                    """
                    UPDATE discovery_variants
                    SET linked_knowledge_item_id = ?, modified_date = ?
                    WHERE id = ?
                    """,
                    (linked_knowledge_item_id, now, variant_id),
                )

                conn.commit()

        else:

            cursor.execute(
                """
                INSERT INTO discovery_variants
                (business_process_id, name, linked_knowledge_item_id,
                 status, created_date, modified_date)
                VALUES (?, ?, ?, 'Active', ?, ?)
                """,
                (business_process_id, name, linked_knowledge_item_id, now, now),
            )

            variant_id = cursor.lastrowid

            conn.commit()

        conn.close()

        return variant_id

    def _get_or_create_page(self, variant_id, name, url, page_title):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT id FROM discovery_pages
            WHERE variant_id = ? AND url = ?
            """,
            (variant_id, url),
        )

        row = cursor.fetchone()

        if row:

            page_id = row[0]

            now = datetime.now().isoformat()

            cursor.execute(
                """
                UPDATE discovery_pages
                SET name = ?, page_title = ?, modified_date = ?
                WHERE id = ?
                """,
                (name, page_title, now, page_id),
            )

            conn.commit()

        else:

            now = datetime.now().isoformat()

            cursor.execute(
                """
                INSERT INTO discovery_pages
                (variant_id, name, url, page_title, status,
                 created_date, modified_date)
                VALUES (?, ?, ?, ?, 'Active', ?, ?)
                """,
                (variant_id, name, url, page_title, now, now),
            )

            page_id = cursor.lastrowid

            conn.commit()

        conn.close()

        return page_id

    def _get_or_create_tab(self, page_id, name, tab_order):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            "SELECT id FROM discovery_tabs WHERE page_id = ? AND name = ?",
            (page_id, name),
        )

        row = cursor.fetchone()

        if row:

            tab_id = row[0]

        else:

            now = datetime.now().isoformat()

            cursor.execute(
                """
                INSERT INTO discovery_tabs
                (page_id, name, tab_order, status, created_date, modified_date)
                VALUES (?, ?, ?, 'Active', ?, ?)
                """,
                (page_id, name, tab_order, now, now),
            )

            tab_id = cursor.lastrowid

            conn.commit()

        conn.close()

        return tab_id

    def _add_element(
        self,
        tab_id,
        name,
        element_type,
        locator,
        locator_strategy,
        alternate_locators,
        placeholder,
        is_required,
    ):

        conn = self.db.get_connection()

        cursor = conn.cursor()

        # Avoid re-inserting the exact same locator under this tab on
        # a repeat discovery run.
        cursor.execute(
            "SELECT id FROM discovery_elements WHERE tab_id = ? AND locator = ?",
            (tab_id, locator),
        )

        row = cursor.fetchone()

        if row:

            conn.close()

            return row[0]

        now = datetime.now().isoformat()

        cursor.execute(
            """
            INSERT INTO discovery_elements
            (tab_id, name, element_type, locator, locator_strategy,
             alternate_locators, placeholder, is_required, status,
             created_date, modified_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'Active', ?, ?)
            """,
            (
                tab_id,
                name,
                element_type,
                locator,
                locator_strategy,
                alternate_locators,
                placeholder,
                1 if is_required else 0,
                now,
                now,
            ),
        )

        element_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return element_id

    # ================================================================
    # discovery_sessions bookkeeping
    # ================================================================

    def _start_session(self, url, auth_type):

        now = datetime.now().isoformat()

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            INSERT INTO discovery_sessions
            (application_id, url, source_area, auth_type, status, started_date)
            VALUES (NULL, ?, 'discovery_repository', ?, 'Running', ?)
            """,
            (url, auth_type, now),
        )

        session_id = cursor.lastrowid

        conn.commit()

        conn.close()

        return session_id

    def _finish_session(
        self,
        session_id,
        status,
        pages_discovered_count=0,
        elements_discovered_count=0,
        application_id=None,
        error_message=None,
    ):

        now = datetime.now().isoformat()

        conn = self.db.get_connection()

        cursor = conn.cursor()

        cursor.execute(
            """
            UPDATE discovery_sessions
            SET status=?,
                pages_discovered_count=?,
                elements_discovered_count=?,
                application_id=COALESCE(?, application_id),
                error_message=?,
                completed_date=?
            WHERE id=?
            """,
            (
                status,
                pages_discovered_count,
                elements_discovered_count,
                application_id,
                error_message,
                now,
                session_id,
            ),
        )

        conn.commit()

        conn.close()
        