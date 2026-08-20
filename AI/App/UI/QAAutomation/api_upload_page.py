# Create: App/UI/QAAutomation/api_upload_page.py

"""
==========================================================
QA AI Studio

QA Automation -> API Upload

Version : 1.0

Same fix pattern already shipped for Playwright (URL Knowledge):
QA Automation's "API" automation type was generating scripts purely
from a test case's plain-English text, so the AI guessed endpoint
paths, field names and auth headers that don't exist. This page lets
someone upload a real Postman Collection (.json export) so API
script generation can use REAL endpoints instead
(Core/api_collection_repository.py + the grounding logic added to
Core/test_execution_manager.py's generate_automation()).

Flow:
    Select a Postman Collection .json file
        |
        v
    (Optional) File it under Domain / Module / Knowledge Name /
    Version, same as URL Knowledge and Manual Upload — so it shows
    up in Manage Knowledge next to any document describing the same
    API, and so API automation generation for THAT scope finds it
        |
        v
    Import  (parsing is fast — runs directly on the UI thread, no
    background worker needed)
        |
        v
    Preview table of every endpoint that was found + a plain-
    language summary of what was saved / skipped

Imports are re-runnable: re-importing the same file under the same
Domain/Module/Knowledge Name/Version reuses the existing linked
Knowledge Hub node rather than duplicating it (see
ApiCollectionRepository._get_or_create_linked_knowledge_item()), but
each import still creates a new collection + endpoint rows — this
page is for adding/refreshing collections, not for editing one in
place.
==========================================================
"""

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QComboBox,
    QFileDialog,
    QMessageBox,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QHeaderView,
    QScrollArea,
)

from Core.api_collection_repository import ApiCollectionRepository
from Core.metadata_manager import MetadataManager


class ApiUploadPage(QWidget):

    def __init__(self):

        super().__init__()

        self.repository = ApiCollectionRepository()

        self.metadata_manager = MetadataManager()

        self.selected_file = None

        self.build_ui()

        self.refresh_collections()

    # ======================================================

        """
        The whole page is wrapped in a QScrollArea — this page has a
        file picker, an optional 4-field Domain/Module/Knowledge
        Name/Version form, an import summary log, AND two result
        tables all in one column, which is too much to fit in a
        fixed-height panel on a normal window size. Without a
        scroll area, Qt squeezes every widget below its natural
        size to make everything fit, which is what caused the
        Domain/Module/Knowledge Name/Version rows to visually
        overlap — the exact same layout bug AI Smart Upload hit
        before it was wrapped in a QScrollArea (see
        smart_upload_page.py's v3.0 notes). Same fix, applied here.
        """

    def build_ui(self):           


        outer = QVBoxLayout(self)

        outer.setContentsMargins(0, 0, 0, 0)

        outer.setSpacing(0)

        scroll = QScrollArea()

        scroll.setWidgetResizable(True)

        scroll.setFrameShape(QScrollArea.NoFrame)

        outer.addWidget(scroll)

        content = QWidget()

        scroll.setWidget(content)

        root = QVBoxLayout(content)

        root.setContentsMargins(20, 20, 20, 20)

        root.setSpacing(15)

        title = QLabel("API Upload")

        title.setObjectName("SectionTitle")

        root.addWidget(title)

        intro = QLabel(
            "Upload a Postman Collection export (.json) so QA "
            "Automation's API script generation can use real "
            "endpoints, methods, headers and bodies instead of "
            "guessing them from the test case's wording. In "
            "Postman: right-click the collection -> Export -> "
            "Collection v2.1."
        )

        intro.setWordWrap(True)

        root.addWidget(intro)

        # --------------------------------------------------
        # File selection
        # --------------------------------------------------

        file_row = QHBoxLayout()

        self.file_path_field = QLineEdit()

        self.file_path_field.setReadOnly(True)

        self.file_path_field.setPlaceholderText(
            "No file selected"
        )

        browse_btn = QPushButton("Browse...")

        browse_btn.clicked.connect(self.select_file)

        file_row.addWidget(self.file_path_field, 1)

        file_row.addWidget(browse_btn)

        root.addLayout(file_row)

        # --------------------------------------------------
        # Optional Knowledge Hub filing
        # --------------------------------------------------

        tree_note = QLabel(
            "Optional — file this collection under the same "
            "Domain / Module / Knowledge Name / Version tree as "
            "your uploaded documents, so both show up together in "
            "Manage Knowledge, AND so API script generation for "
            "test cases filed under that same scope can find these "
            "endpoints. Leave Knowledge Name blank to import "
            "unlinked."
        )

        tree_note.setWordWrap(True)

        tree_note.setStyleSheet("color: #64748B;")

        root.addWidget(tree_note)

        form = QFormLayout()

        self.domain_combo = QComboBox()

        self.domain_combo.setEditable(True)

        self.domain_combo.addItem("")

        self.domain_combo.addItems(self.metadata_manager.list_domains())

        self.domain_combo.currentTextChanged.connect(
            self._on_domain_changed
        )

        self.module_combo = QComboBox()

        self.module_combo.setEditable(True)

        self.module_combo.addItem("")

        self.knowledge_name = QLineEdit()

        self.knowledge_name.setPlaceholderText(
            "Leave blank to import without linking to Knowledge Hub"
        )

        self.version = QLineEdit()

        self.version.setPlaceholderText("Optional — defaults to 1.0")

        form.addRow("Domain:", self.domain_combo)

        form.addRow("Module:", self.module_combo)

        form.addRow("Knowledge Name:", self.knowledge_name)

        form.addRow("Version (optional):", self.version)

        root.addLayout(form)

        # --------------------------------------------------
        # Import action
        # --------------------------------------------------

        import_row = QHBoxLayout()

        self.import_btn = QPushButton("Import Collection")

        self.import_btn.setMinimumHeight(36)

        self.import_btn.clicked.connect(self.import_collection)

        import_row.addWidget(self.import_btn)

        import_row.addStretch()

        root.addLayout(import_row)

        # --------------------------------------------------
        # Import summary log
        # --------------------------------------------------

        self.log = QTextEdit()

        self.log.setReadOnly(True)

        self.log.setMinimumHeight(140)

        self.log.setMaximumHeight(160)

        self.log.setPlaceholderText(
            "Import results will appear here."
        )

        root.addWidget(self.log)

        # --------------------------------------------------
        # Endpoint preview (most recent import)
        # --------------------------------------------------

        preview_label = QLabel("Endpoints Found")

        root.addWidget(preview_label)

        self.endpoints_table = QTableWidget(0, 4)

        self.endpoints_table.setHorizontalHeaderLabels(
            ["Method", "Name", "Folder", "URL"]
        )

        self.endpoints_table.horizontalHeader().setSectionResizeMode(
            3, QHeaderView.Stretch
        )

        self.endpoints_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )

        # A fixed minimum height (rather than a layout stretch
        # factor) so this table stays readable inside a QScrollArea
        # — a scrollable content widget grows to fit its content
        # instead of a fixed panel size, so a stretch factor here
        # would have nothing to expand into and the table could
        # collapse to just its header.
        self.endpoints_table.setMinimumHeight(220)

        root.addWidget(self.endpoints_table)

        # --------------------------------------------------
        # Previously imported collections
        # --------------------------------------------------

        collections_label = QLabel("Previously Imported Collections")

        root.addWidget(collections_label)

        self.collections_table = QTableWidget(0, 5)

        self.collections_table.setHorizontalHeaderLabels(
            ["Collection", "Domain", "Module", "Knowledge Name",
             "Version"]
        )

        self.collections_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.Stretch
        )

        self.collections_table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )

        self.collections_table.itemSelectionChanged.connect(
        self.on_collection_selected
        )

        self.collections_table.setMinimumHeight(180)

        root.addWidget(self.collections_table)

        root.addStretch()

    # ======================================================
    # Domain / Module cascade
    # ======================================================

    def _on_domain_changed(self, domain_name):

        self.module_combo.clear()

        self.module_combo.addItem("")

        domain_name = (domain_name or "").strip()

        if not domain_name:

            return

        self.module_combo.addItems(
            self.metadata_manager.list_modules(domain_name)
        )

    # ======================================================
    # File selection
    # ======================================================

    def select_file(self):

        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Postman Collection",
            "",
            "Postman Collection (*.json);;All Files (*.*)",
        )

        if not file_path:

            return

        self.selected_file = file_path

        self.file_path_field.setText(file_path)

    # ======================================================
    # Import
    # ======================================================

    def import_collection(self):

        if not self.selected_file:

            QMessageBox.warning(
                self,
                "No File Selected",
                "Select a Postman Collection (.json) file first."
            )

            return

        domain = self.domain_combo.currentText().strip()

        module = self.module_combo.currentText().strip()

        knowledge_name = self.knowledge_name.text().strip()

        version = self.version.text().strip()

        result = self.repository.import_postman_collection(
            self.selected_file,
            domain=domain or None,
            module=module or None,
            knowledge_name=knowledge_name or None,
            version=version or None,
        )

        if not result.get("success"):

            self.log.setPlainText(
                f"Import failed: {result.get('error', 'Unknown error.')}"
            )

            QMessageBox.critical(
                self,
                "Import Failed",
                result.get("error", "Unknown error.")
            )

            return

        summary_lines = [
            f"Imported '{result['collection_name']}' successfully.",
            f"Folders found: {result['folders_found']}",
            f"Endpoints saved: {result['endpoints_saved']}",
            f"Endpoints skipped: {result['endpoints_skipped']}",
        ]

        if result["skipped_endpoints"]:

            summary_lines.append("")

            summary_lines.append("Skipped:")

            for skipped in result["skipped_endpoints"]:

                summary_lines.append(
                    f"  - {skipped.get('name', '(unnamed)')}: "
                    f"{skipped.get('reason', '')}"
                )

        if result.get("knowledge_item_id"):

            summary_lines.append("")

            summary_lines.append(
                "Filed under Manage Knowledge — API automation "
                "generation for test cases in this same Domain / "
                "Module / Knowledge Name will now use these "
                "endpoints."
            )

        else:

            summary_lines.append("")

            summary_lines.append(
                "Imported without linking to a Domain / Module / "
                "Knowledge Name — fill those in and re-import to "
                "ground API automation generation in this "
                "collection."
            )

        self.log.setPlainText("\n".join(summary_lines))

        self.populate_endpoints_table(
            self.repository.list_endpoints(result["collection_id"])
        )

        self.refresh_collections()

    # ======================================================
    # Tables
    # ======================================================

    def populate_endpoints_table(self, endpoints):

        self.endpoints_table.setRowCount(0)

        for endpoint in endpoints:

            row = self.endpoints_table.rowCount()

            self.endpoints_table.insertRow(row)

            self.endpoints_table.setItem(
                row, 0, QTableWidgetItem(endpoint.get("method", ""))
            )

            self.endpoints_table.setItem(
                row, 1, QTableWidgetItem(endpoint.get("name", ""))
            )

            self.endpoints_table.setItem(
                row, 2, QTableWidgetItem(endpoint.get("folder_path", ""))
            )

            self.endpoints_table.setItem(
                row,
                3,
                QTableWidgetItem(
                    endpoint.get("url_resolved")
                    or endpoint.get("url_raw", "")
                ),
            )

    def refresh_collections(self):

        collections = self.repository.list_collections()

        self.collections_table.setRowCount(0)

        for collection in collections:

            row = self.collections_table.rowCount()

            self.collections_table.insertRow(row)

            self.collections_table.setItem(
                row, 0, QTableWidgetItem(collection.get("name", ""))
            )

            self.collections_table.setItem(
                row, 1, QTableWidgetItem(collection.get("domain", ""))
            )

            self.collections_table.setItem(
                row, 2, QTableWidgetItem(collection.get("module", ""))
            )

            self.collections_table.setItem(
                row,
                3,
                QTableWidgetItem(collection.get("knowledge_name", "")),
            )

            self.collections_table.setItem(
                row, 4, QTableWidgetItem(collection.get("version", ""))
            )

            # Keep the collection id handy for on_collection_selected()
            # without adding a visible column for it.
            self.collections_table.item(row, 0).setData(
                Qt.UserRole, collection.get("id")
            )

    def on_collection_selected(self):

        selected_rows = self.collections_table.selectionModel().selectedRows()

        if not selected_rows:

            return

        row = selected_rows[0].row()

        collection_id = self.collections_table.item(row, 0).data(
            Qt.UserRole
        )

        if collection_id is None:

            return

        self.populate_endpoints_table(
            self.repository.list_endpoints(collection_id)
        )