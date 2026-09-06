"""
QA AI Studio
Discovery Step Confirmation Dialog
Location: App/UI/KnowledgeHub/discovery_step_confirmation_dialog.py

This is the missing "ask ... for confirmation regarding hierarchy,
fields etc" step. Every previous version of URL discovery went
straight from a Playwright scan to a database write with no human
in the loop at all — the operator only ever saw a few summary lines
in a log box after the fact ("Elements saved: 1 | Skipped: 3").
There was no way to notice a wrong Business Process name, rename a
confusingly-labelled field, or rescue an element the engine could
not confidently locate on its own.

This dialog is shown once per captured screen (once per step of the
real business flow), right after URLDiscoveryEngine.scan_current_view()
runs, and before anything reaches the database:

    - Page Name / Tab Name are editable (pre-filled from the page
      title / a default), since "the page title" is not always a
      meaningful name for a wizard step.
    - Every discovered field, button, link and tab is listed as one
      row: Include (checkbox), Name, Type, Locator (editable),
      Strategy (how that locator was derived), Required (checkbox).
    - Rows the engine could not confidently locate at all are still
      shown — unchecked, with an empty, editable Locator cell — so
      the operator can paste in a locator by hand (e.g. copied from
      the browser's own DevTools) rather than silently losing that
      element. This is the direct fix for the "3 elements had no
      reliable locator and were not stored" problem: it is now the
      operator's call, not a silent drop.
    - A "this is the final step" checkbox lets the operator mark the
      review/submit screen as the end of the flow without QA AI
      Studio ever clicking Submit itself.

Only rows left checked are handed back to DiscoveryRepository —
see confirmed_step().
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


COLUMNS = [
    "Include",
    "Name / Label",
    "Type",
    "Locator",
    "Strategy",
    "Required",
]

COL_INCLUDE = 0
COL_NAME = 1
COL_TYPE = 2
COL_LOCATOR = 3
COL_STRATEGY = 4
COL_REQUIRED = 5

# Anything at or below this locator-strategy tier is a guess, not a
# fact — flagged in the UI rather than pre-checked, so the operator
# makes the call instead of it happening silently.
LOW_CONFIDENCE_STRATEGIES = {None, "", "tag-only", "type", "text"}


class DiscoveryStepConfirmationDialog(QDialog):

    def __init__(self, scan_result, step_index, default_page_name="", parent=None):

        super().__init__(parent)

        self.scan_result = scan_result or {}
        self.step_index = step_index

        self.setWindowTitle(f"Confirm Step {step_index} — What Was Found On This Screen")
        self.resize(920, 620)

        self._row_candidates = []

        self._build_ui(default_page_name)

    # ------------------------------------------------------------
    # UI
    # ------------------------------------------------------------

    def _build_ui(self, default_page_name):

        layout = QVBoxLayout(self)

        header = QLabel(f"Step {self.step_index}: review this screen before it is saved")
        header.setStyleSheet("font-size: 15px; font-weight: bold;")
        layout.addWidget(header)

        url = self.scan_result.get("url") or ""
        sub = QLabel(url)
        sub.setStyleSheet("color: #64748B;")
        sub.setWordWrap(True)
        layout.addWidget(sub)

        # ---------------- Hierarchy fields ----------------

        hierarchy_box = QGroupBox("This Screen's Identity")
        form = QFormLayout(hierarchy_box)

        page_title = self.scan_result.get("page_title") or ""
        page_default = default_page_name or page_title or url or f"Step {self.step_index}"

        self.step_name = QLineEdit(f"Step {self.step_index}: {page_title or page_default}")
        self.page_name = QLineEdit(page_default)
        self.tab_name = QLineEdit("Main View")

        form.addRow("Step name:", self.step_name)
        form.addRow("Page name:", self.page_name)
        form.addRow("Tab / section name:", self.tab_name)

        layout.addWidget(hierarchy_box)

        # ---------------- Elements table ----------------

        elements_label = QLabel("Discovered elements — uncheck anything you don't want stored, edit names/locators freely:")
        layout.addWidget(elements_label)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.setSelectionMode(QAbstractItemView.NoSelection)
        self.table.horizontalHeader().setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(COL_LOCATOR, QHeaderView.Stretch)
        self.table.verticalHeader().setVisible(False)

        self._populate_table()

        layout.addWidget(self.table)

        skipped_count = sum(
            1 for c in self._row_candidates
            if (c.get("locator_strategy") in LOW_CONFIDENCE_STRATEGIES and not c.get("locator"))
        )
        if skipped_count:
            warn = QLabel(
                f"{skipped_count} element(s) had no reliable locator and are unchecked below — "
                "type one in manually (e.g. copied from the browser's DevTools) if you want to keep it."
            )
            warn.setStyleSheet("color: #B45309;")
            warn.setWordWrap(True)
            layout.addWidget(warn)

        # ---------------- Final step ----------------

        self.final_step_checkbox = QCheckBox(
            "This is the final screen before Submit — mark this as the end of the flow "
            "(QA AI Studio will still never click Submit itself)."
        )
        layout.addWidget(self.final_step_checkbox)

        # ---------------- Buttons ----------------

        button_row = QHBoxLayout()
        button_row.addStretch()

        self.btn_discard = QPushButton("Discard This Step")
        self.btn_discard.clicked.connect(self.reject)

        self.btn_next = QPushButton("Capture Another Step →")
        self.btn_next.setStyleSheet(
            "background-color: #2563EB; color: white; font-weight: bold; padding: 6px 14px;"
        )
        self.btn_next.clicked.connect(self._accept_and_continue)

        self.btn_finish = QPushButton("This Is the Final Step — Finish Flow")
        self.btn_finish.setStyleSheet(
            "background-color: #15803D; color: white; font-weight: bold; padding: 6px 14px;"
        )
        self.btn_finish.clicked.connect(self._accept_and_finish)

        button_row.addWidget(self.btn_discard)
        button_row.addWidget(self.btn_next)
        button_row.addWidget(self.btn_finish)

        layout.addLayout(button_row)

        self._is_final = False

    def _populate_table(self):

        candidates = []

        for f in self.scan_result.get("fields", []):
            candidates.append(dict(f, _kind="field"))

        for b in self.scan_result.get("buttons", []):
            candidates.append(dict(b, _kind="button"))

        for l in self.scan_result.get("links", []):
            candidates.append(dict(l, _kind="link"))

        for t in self.scan_result.get("tabs", []):
            candidates.append(dict(t, _kind="tab"))

        self._row_candidates = candidates

        self.table.setRowCount(len(candidates))

        for row, candidate in enumerate(candidates):

            include_checkbox = QCheckBox()
            has_locator = bool((candidate.get("locator") or "").strip())
            good_strategy = (
                candidate.get("locator_strategy") not in LOW_CONFIDENCE_STRATEGIES
                and candidate.get("locator_validation", "unique") == "unique"
            )
            include_checkbox.setChecked(has_locator and good_strategy)
            self.table.setCellWidget(row, COL_INCLUDE, self._centered(include_checkbox))

            name = (
                candidate.get("text")
                or candidate.get("aria_label")
                or candidate.get("placeholder")
                or candidate.get("name")
                or candidate.get("id")
                or f"{candidate.get('_kind', 'element')} {row + 1}"
            )
            self.table.setItem(row, COL_NAME, QTableWidgetItem(str(name)[:200]))

            # Kind first ("field"/"button"/"link"/"tab") so the column
            # reads consistently regardless of whether the underlying
            # HTML element happened to carry an explicit type="" attribute.
            element_type = candidate.get("_kind") or candidate.get("type") or candidate.get("tag") or ""
            type_item = QTableWidgetItem(str(element_type))
            type_item.setFlags(type_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, COL_TYPE, type_item)

            locator_item = QTableWidgetItem(candidate.get("locator") or "")
            if not has_locator or not good_strategy:
                locator_item.setForeground(Qt.darkYellow)
            self.table.setItem(row, COL_LOCATOR, locator_item)

            strategy = candidate.get("locator_strategy") or "none"
            validation = candidate.get("locator_validation") or "unvalidated"
            quality = candidate.get("locator_quality") or "unknown"
            strategy_item = QTableWidgetItem(f"{strategy} / {validation} / {quality}")
            strategy_item.setFlags(strategy_item.flags() & ~Qt.ItemIsEditable)
            self.table.setItem(row, COL_STRATEGY, strategy_item)

            required_checkbox = QCheckBox()
            required_checkbox.setChecked(bool(candidate.get("required")))
            self.table.setCellWidget(row, COL_REQUIRED, self._centered(required_checkbox))

    @staticmethod
    def _centered(widget):
        from PySide6.QtWidgets import QWidget, QHBoxLayout as _HBox
        wrapper = QWidget()
        box = _HBox(wrapper)
        box.addWidget(widget)
        box.setAlignment(Qt.AlignCenter)
        box.setContentsMargins(0, 0, 0, 0)
        return wrapper

    # ------------------------------------------------------------
    # Result extraction
    # ------------------------------------------------------------

    def _accept_and_continue(self):
        self._is_final = False
        self.accept()

    def _accept_and_finish(self):
        self._is_final = True
        self.accept()

    def is_final_step(self):
        return self._is_final or self.final_step_checkbox.isChecked()

    def confirmed_step(self):
        """
        Returns the step dict shaped for
        DiscoveryRepository.save_flow_result(), containing ONLY the
        elements the operator left checked, with whatever edits they
        made to name/locator/required.
        """

        elements = []

        for row, candidate in enumerate(self._row_candidates):

            include_widget = self.table.cellWidget(row, COL_INCLUDE)
            include_checkbox = include_widget.findChild(QCheckBox) if include_widget else None

            if not include_checkbox or not include_checkbox.isChecked():
                continue

            locator = (self.table.item(row, COL_LOCATOR).text() or "").strip()

            if not locator:
                # Operator left it checked but didn't supply a locator —
                # skip rather than store an empty/unreliable one.
                continue

            name = (self.table.item(row, COL_NAME).text() or "").strip()

            element_type = (self.table.item(row, COL_TYPE).text() or "").strip()

            required_widget = self.table.cellWidget(row, COL_REQUIRED)
            required_checkbox = required_widget.findChild(QCheckBox) if required_widget else None
            is_required = bool(required_checkbox and required_checkbox.isChecked())

            alternates = []

            engine_locator = candidate.get("locator")
            if engine_locator and engine_locator != locator:
                alternates.append(
                    {"strategy": candidate.get("locator_strategy") or "engine", "locator": engine_locator}
                )

            # Always offer the computed XPath alternate too (not just
            # the engine's original locator), unless it's literally
            # identical to something already in the list — e.g. a
            # dynamic-id element where the XPath alternate IS what the
            # engine picked as primary in the first place.
            xpath_alternative = candidate.get("xpath_alternative")
            if (
                xpath_alternative
                and xpath_alternative != locator
                and xpath_alternative not in (a["locator"] for a in alternates)
            ):
                alternates.append(
                    {"strategy": "xpath", "locator": xpath_alternative}
                )

            alternate_locators = alternates or None

            elements.append(
                {
                    "name": name,
                    "element_type": element_type,
                    "locator": locator,
                    "locator_strategy": self.table.item(row, COL_STRATEGY).text() or "operator-confirmed",
                    "alternate_locators": alternate_locators,
                    "placeholder": candidate.get("placeholder", ""),
                    "is_required": is_required,
                }
            )

        return {
            "step_name": self.step_name.text().strip() or f"Step {self.step_index}",
            "page_name": self.page_name.text().strip() or f"Step {self.step_index}",
            "page_url": self.scan_result.get("url") or "",
            "page_title": self.scan_result.get("page_title") or "",
            "tab_name": self.tab_name.text().strip() or "Main View",
            "is_end_step": self.is_final_step(),
            "elements": elements,
        }
