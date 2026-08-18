"""
QA AI Studio
Business Flow Start Dialog
Location: App/UI/KnowledgeHub/business_flow_start_dialog.py

Shown exactly once, right after login succeeds, before the operator
starts walking the real business process in the still-open browser
window. Confirms the hierarchy names the capture will be filed
under (Application / Business Process / Variant) up front, and
explains the capture workflow, so the operator isn't guessing what
"Capture This Screen" is going to do.

Business Process / Variant were previously auto-guessed from the
URL path with no way to correct them (see discovery_repository.py's
module docstring) — this dialog is what turns that guess into
something the operator confirms or overrides before a single
element is ever saved.

Domain / Module / Knowledge Name / Version were added so a captured
flow can be filed under the SAME Knowledge Hub node an uploaded
document (SRS, CRF, etc.) lives under — see
discovery_repository.py's _get_or_create_captured_knowledge_item().
They are optional: leave Knowledge Name blank and the flow is still
saved exactly as before, just not linked into that tree.
"""

from urllib.parse import urlparse

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from Core.metadata_manager import MetadataManager


class BusinessFlowStartDialog(QDialog):

    def __init__(self, source_url, parent=None):

        super().__init__(parent)

        self.setWindowTitle("Start Business Flow Capture")
        self.resize(520, 420)

        self.metadata_manager = MetadataManager()

        parsed = urlparse(source_url or "")

        segments = [seg for seg in (parsed.path or "").split("/") if seg]

        default_application = parsed.hostname or (source_url or "")
        default_business_process = segments[0] if segments else "General"
        default_variant = segments[1] if len(segments) > 1 else "Default"

        layout = QVBoxLayout(self)

        intro = QLabel(
            "The browser window will stay open on the authenticated "
            "application. Perform the real business process yourself, "
            "screen by screen — open the form, fill a tab, move to the "
            "next step — and click “Capture This Screen” in QA AI "
            "Studio after each one. You will be asked to confirm what "
            "was found before it is saved.\n\n"
            "QA AI Studio will never click Submit, Save, Delete or any "
            "other data-changing control on its own. When you reach the "
            "final review/submit screen, capture it, mark it as the "
            "final step, and finish — without actually submitting."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()

        self.application_name = QLineEdit(default_application)
        self.business_process = QLineEdit(default_business_process)
        self.variant_name = QLineEdit(default_variant)

        form.addRow("Application:", self.application_name)
        form.addRow("Business Process:", self.business_process)
        form.addRow("Process Variant:", self.variant_name)

        layout.addLayout(form)

        note = QLabel(
            "These can be corrected later in Knowledge Hub — Manage "
            "Knowledge if needed, but getting them right now means "
            "QA Automation, QA Engineering and the AI Assistant all see "
            "this business process filed under one consistent name."
        )
        note.setWordWrap(True)
        note.setStyleSheet("color: #64748B;")
        layout.addWidget(note)

        tree_note = QLabel(
            "Optional — file this flow under the same Domain / Module / "
            "Knowledge Name / Version tree as your uploaded documents, "
            "so both show up together in Manage Knowledge. Leave "
            "Knowledge Name blank to skip this."
        )
        tree_note.setWordWrap(True)
        tree_note.setStyleSheet("color: #64748B;")
        layout.addWidget(tree_note)

        tree_form = QFormLayout()

        self.domain_combo = QComboBox()
        self.domain_combo.setEditable(True)
        self.domain_combo.addItem("")
        self.domain_combo.addItems(self.metadata_manager.list_domains())
        self.domain_combo.currentTextChanged.connect(self._on_domain_changed)

        self.module_combo = QComboBox()
        self.module_combo.setEditable(True)
        self.module_combo.addItem("")

        self.knowledge_name = QLineEdit()
        self.knowledge_name.setPlaceholderText(
            "Leave blank to save this flow without linking it to Knowledge Hub"
        )

        self.version = QLineEdit()
        self.version.setPlaceholderText("Optional — defaults to 1.0")

        tree_form.addRow("Domain:", self.domain_combo)
        tree_form.addRow("Module:", self.module_combo)
        tree_form.addRow("Knowledge Name:", self.knowledge_name)
        tree_form.addRow("Version (optional):", self.version)

        layout.addLayout(tree_form)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.button(QDialogButtonBox.Ok).setText("Start Capturing")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    def _on_domain_changed(self, domain_name):
        """
        Refills the Module dropdown for whichever Domain is now
        selected/typed, same pattern as the Upload New Knowledge
        dialog uses. A domain typed fresh (not yet saved) just gets
        an empty Module list — get_or_create_module() creates it on
        save, same as everywhere else in Knowledge Hub.
        """

        self.module_combo.clear()
        self.module_combo.addItem("")

        domain_name = (domain_name or "").strip()

        if not domain_name:

            return

        self.module_combo.addItems(
            self.metadata_manager.list_modules(domain_name)
        )

    def values(self):

        return {
            "application_name": self.application_name.text().strip(),
            "business_process_name": self.business_process.text().strip(),
            "variant_name": self.variant_name.text().strip(),
            "domain": self.domain_combo.currentText().strip(),
            "module": self.module_combo.currentText().strip(),
            "knowledge_name": self.knowledge_name.text().strip(),
            "version": self.version.text().strip(),
        }