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

import json
from datetime import datetime
from urllib.parse import urlparse

from Database.db_manager import DatabaseManager
from Core.logger import Logger


DEFAULT_BUSINESS_PROCESS = "General"

DEFAULT_VARIANT = "Default"

SCANNED_VIEW_TAB_NAME = "(scanned view)"


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
        nothing usable enough exists. Priority: data-testid > id >
        name > aria-label (text-based) > placeholder. A bare
        tag/type combination (e.g. "input[type='text']") is
        deliberately NOT accepted — it matches too many elements on
        a real page to be trustworthy automation knowledge.
        """

        tag = candidate.get("tag") or "input"

        alternates = []

        primary = None

        strategy = None

        data_testid = candidate.get("data_testid")

        if data_testid:

            css = f"[data-testid='{data_testid}']"

            alternates.append({"strategy": "data-testid", "locator": css})

            primary, strategy = css, "data-testid"

        element_id = candidate.get("id")

        if element_id:

            css = f"#{element_id}"

            alternates.append({"strategy": "id", "locator": css})

            if primary is None:

                primary, strategy = css, "id"

        name = candidate.get("name")

        if name:

            css = f"{tag}[name='{name}']"

            alternates.append({"strategy": "name", "locator": css})

            if primary is None:

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

        if primary is None:

            return None

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

    def _get_or_create_variant(self, business_process_id, name):

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

        if row:

            variant_id = row[0]

        else:

            now = datetime.now().isoformat()

            cursor.execute(
                """
                INSERT INTO discovery_variants
                (business_process_id, name, status, created_date, modified_date)
                VALUES (?, ?, 'Active', ?, ?)
                """,
                (business_process_id, name, now, now),
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
        