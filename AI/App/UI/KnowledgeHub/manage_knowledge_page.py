# Replace: App/UI/KnowledgeHub/manage_knowledge_page.py

"""
==========================================================
QA AI Studio

Knowledge Hub

Manage Knowledge

Version : 4.0  (Tree View)

Tree hierarchy (per your requirement):
    Domain -> Module -> Knowledge Name -> Document Type -> Upload Source

Platform / Category / Business Process are no longer shown — those
were AI Smart Upload's internal classification fields, not part of
your required hierarchy.

Fix Notes carried over from v3.0:
    • Reads columns by NAME (sqlite3.Row) instead of position number
      — the old position-based reads were showing wrong data for
      several columns due to columns added later via ALTER TABLE.
    • Edit dialog lets you change Domain, Module, Knowledge Name,
      Version, Document Type, Tags, Summary on any file — from
      either Manual or AI Smart Upload.

Selection rules:
    • Only a FILE (leaf node, the deepest level) is an actual
      database row — View/Edit/Delete/Versions act on that.
    • Selecting a Domain/Module/Knowledge Name/Document Type group
      just expands it; those buttons are disabled until you drill
      down to an actual file.
==========================================================
"""

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QFormLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QTextEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QMessageBox,
    QDialog,
    QDialogButtonBox,
    QComboBox,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
)

from Core.metadata_manager import MetadataManager
from Core.repository_manager import RepositoryManager
from Core.vector_store import VectorStore
from Core.upload_pipeline import UploadPipeline
from Core.discovery_repository import (
    DiscoveryRepository,
    CAPTURED_FLOW_SOURCE_TYPE,
)

from Core.api_collection_repository import (
    ApiCollectionRepository,
    API_COLLECTION_SOURCE_TYPE,
)
from PySide6.QtCore import Signal


ROW_ID_ROLE = Qt.UserRole

# Marks an endpoint node (a leaf under an imported API Collection's
# knowledge_items row) so edit_selected() can tell it apart from a
# regular file leaf, which uses ROW_ID_ROLE instead. A node only
# ever carries one of the two roles.
ENDPOINT_ID_ROLE = Qt.UserRole + 1

HTTP_METHODS = [
    "GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS",
]


# ==========================================================
# Read-only details dialog
# ==========================================================

class KnowledgeDetailDialog(QDialog):

    def __init__(self, data, parent=None):

        super().__init__(parent)

        self.setWindowTitle("Knowledge Details")

        self.resize(600, 420)

        layout = QVBoxLayout(self)

        text = QTextEdit()

        text.setReadOnly(True)

        for key, value in data.items():

            text.append(f"{key}: {value}")

        layout.addWidget(text)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)

        buttons.accepted.connect(self.accept)

        layout.addWidget(buttons)


# ==========================================================
# Edit dialog
# ==========================================================

DOCUMENT_TYPES = [
    "General",
    "SRS",
    "CRF",
    "Test Case",
    "API",
    "SOP",
    "Release Notes",
    "Technical Document",
    "Other",
]


class EditKnowledgeDialog(QDialog):

    def __init__(self, row, metadata_manager, parent=None):

        super().__init__(parent)

        self.row = row

        self.metadata_manager = metadata_manager

        self.setWindowTitle(
            f"Edit — {row['knowledge_name']}"
        )

        self.resize(520, 420)

        self.build_ui()

    # ------------------------------------------------------

    def build_ui(self):

        layout = QVBoxLayout(self)

        grid = QGridLayout()

        grid.setHorizontalSpacing(12)

        grid.setVerticalSpacing(10)

        grid.setColumnStretch(1, 1)


        self.domain = QComboBox()

        self.domain.setEditable(True)

        self.populate_domains()

        self.domain.setCurrentText(self.row["domain"] or "")


        self.module = QComboBox()

        self.module.setEditable(True)

        self.populate_modules(self.row["domain"] or "")

        self.module.setCurrentText(self.row["module"] or "")


        self.knowledge_name = QLineEdit(
            self.row["knowledge_name"] or ""
        )


        self.version = QLineEdit(
            self.row["version"] or "1.0"
        )


        self.document_type = QComboBox()

        self.document_type.addItems(DOCUMENT_TYPES)

        current_doc_type = self.row["document_type"] or "General"

        index = self.document_type.findText(current_doc_type)

        self.document_type.setCurrentIndex(
            index if index >= 0 else 0
        )


        self.tags = QLineEdit(self.row["tags"] or "")

        self.tags.setPlaceholderText("comma,separated,tags")


        self.summary = QTextEdit()

        self.summary.setPlainText(self.row["summary"] or "")

        self.summary.setMinimumHeight(100)


        file_label = QLabel(
            self.row["file_name"] or "(no file name on record)"
        )

        file_label.setStyleSheet("color: gray;")


        grid.addWidget(QLabel("Upload Source"), 0, 0)

        grid.addWidget(file_label, 0, 1)

        grid.addWidget(QLabel("Domain"), 1, 0)

        grid.addWidget(self.domain, 1, 1)

        grid.addWidget(QLabel("Module"), 2, 0)

        grid.addWidget(self.module, 2, 1)

        grid.addWidget(QLabel("Knowledge Name"), 3, 0)

        grid.addWidget(self.knowledge_name, 3, 1)

        grid.addWidget(QLabel("Version"), 4, 0)

        grid.addWidget(self.version, 4, 1)

        grid.addWidget(QLabel("Document Type"), 5, 0)

        grid.addWidget(self.document_type, 5, 1)

        grid.addWidget(QLabel("Tags"), 6, 0)

        grid.addWidget(self.tags, 6, 1)

        grid.addWidget(QLabel("Summary"), 7, 0)

        grid.addWidget(self.summary, 7, 1)


        layout.addLayout(grid)


        self.domain.currentTextChanged.connect(
            self.on_domain_changed
        )


        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(self.save)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    # ------------------------------------------------------

    def populate_domains(self):

        try:

            self.domain.addItems(
                self.metadata_manager.list_domains()
            )

        except Exception:

            pass

    def populate_modules(self, domain_name):

        current_text = self.module.currentText()

        self.module.clear()

        if domain_name:

            try:

                self.module.addItems(
                    self.metadata_manager.list_modules(domain_name)
                )

            except Exception:

                pass

        if current_text:

            self.module.setCurrentText(current_text)

    def on_domain_changed(self, domain_name):

        self.populate_modules(domain_name)

    # ------------------------------------------------------

    def save(self):

        domain = self.domain.currentText().strip()

        module = self.module.currentText().strip()

        knowledge_name = self.knowledge_name.text().strip()

        if not (domain and module and knowledge_name):

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Domain, Module, and Knowledge Name are required."
            )

            return

        try:

            self.metadata_manager.ensure_domain_and_module(
                domain, module
            )

        except Exception:

            pass

        try:

            self.metadata_manager.update_knowledge_item(
                self.row["id"],
                domain=domain,
                module=module,
                knowledge_name=knowledge_name,
                version=self.version.text().strip() or "1.0",
                document_type=self.document_type.currentText(),
                tags=self.tags.text().strip(),
                summary=self.summary.toPlainText().strip(),
            )

            self.accept()

        except Exception as ex:

            QMessageBox.critical(self, "Save Failed", str(ex))


# ==========================================================
# Postman-style endpoint editor (view + edit one imported request)
# ==========================================================

class ApiEndpointEditorDialog(QDialog):
    """
    Lets someone view and edit one endpoint from an imported Postman
    Collection directly in Manage Knowledge — method, URL, headers,
    and body — the same fields Postman itself shows, instead of the
    single flattened text label the tree previously rendered with no
    way to inspect or change anything underneath it.

    Saves back to the api_endpoints row via
    ApiCollectionRepository.update_endpoint() — this edits ONLY the
    stored copy of this request; it does not re-import or touch the
    original .json file.
    """

    def __init__(self, endpoint, repository, parent=None):

        super().__init__(parent)

        self.endpoint = endpoint

        self.repository = repository

        self.setWindowTitle(
            f"Endpoint — {endpoint.get('name') or '(unnamed)'}"
        )

        self.resize(640, 560)

        self.build_ui()

    # ------------------------------------------------------

    def build_ui(self):

        layout = QVBoxLayout(self)

        if self.endpoint.get("folder_path"):

            folder_label = QLabel(
                f"Folder: {self.endpoint['folder_path']}"
            )

            folder_label.setStyleSheet("color: gray;")

            layout.addWidget(folder_label)

        form = QFormLayout()

        self.method = QComboBox()

        self.method.setEditable(True)

        self.method.addItems(HTTP_METHODS)

        current_method = (self.endpoint.get("method") or "GET").upper()

        index = self.method.findText(current_method)

        if index >= 0:

            self.method.setCurrentIndex(index)

        else:

            self.method.setCurrentText(current_method)

        self.name = QLineEdit(self.endpoint.get("name") or "")

        self.url = QLineEdit(
            self.endpoint.get("url_raw")
            or self.endpoint.get("url_resolved")
            or ""
        )

        self.url.setPlaceholderText(
            "https://host/path — {{variables}} are allowed"
        )

        form.addRow("Method:", self.method)

        form.addRow("Name:", self.name)

        form.addRow("URL:", self.url)

        layout.addLayout(form)

        # --------------------------------------------------
        # Headers
        # --------------------------------------------------

        layout.addWidget(QLabel("Headers"))

        self.headers_table = QTableWidget(0, 2)

        self.headers_table.setHorizontalHeaderLabels(["Key", "Value"])

        self.headers_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.Stretch
        )

        self.headers_table.setMinimumHeight(140)

        self._load_headers()

        layout.addWidget(self.headers_table)

        header_buttons = QHBoxLayout()

        add_header_btn = QPushButton("Add Header")

        add_header_btn.clicked.connect(self._add_header_row)

        remove_header_btn = QPushButton("Remove Selected")

        remove_header_btn.clicked.connect(self._remove_header_row)

        header_buttons.addWidget(add_header_btn)

        header_buttons.addWidget(remove_header_btn)

        header_buttons.addStretch()

        layout.addLayout(header_buttons)

        # --------------------------------------------------
        # Body
        # --------------------------------------------------

        body_mode = self.endpoint.get("body_mode") or "(none)"

        layout.addWidget(QLabel(f"Body ({body_mode}):"))

        self.body = QTextEdit()

        self.body.setPlainText(self.endpoint.get("body_raw") or "")

        self.body.setMinimumHeight(140)

        layout.addWidget(self.body)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )

        buttons.accepted.connect(self.save)

        buttons.rejected.connect(self.reject)

        layout.addWidget(buttons)

    # ------------------------------------------------------

    def _load_headers(self):

        try:

            headers = json.loads(self.endpoint.get("headers_json") or "[]")

        except (TypeError, ValueError):

            headers = []

        for header in headers:

            self._add_header_row(
                header.get("key", ""), header.get("value", "")
            )

    def _add_header_row(self, key="", value=""):

        row = self.headers_table.rowCount()

        self.headers_table.insertRow(row)

        self.headers_table.setItem(row, 0, QTableWidgetItem(key))

        self.headers_table.setItem(row, 1, QTableWidgetItem(value))

    def _remove_header_row(self):

        rows = sorted(
            {index.row() for index in self.headers_table.selectedIndexes()},
            reverse=True,
        )

        for row in rows:

            self.headers_table.removeRow(row)

    def _collect_headers(self):

        headers = []

        for row in range(self.headers_table.rowCount()):

            key_item = self.headers_table.item(row, 0)

            value_item = self.headers_table.item(row, 1)

            key = key_item.text().strip() if key_item else ""

            if not key:

                continue

            headers.append(
                {
                    "key": key,
                    "value": value_item.text() if value_item else "",
                }
            )

        return headers

    # ------------------------------------------------------

    def save(self):

        url = self.url.text().strip()

        if not url:

            QMessageBox.warning(
                self, "QA AI Studio", "URL cannot be empty."
            )

            return

        try:

            self.repository.update_endpoint(
                self.endpoint["id"],
                method=self.method.currentText().strip().upper() or "GET",
                name=self.name.text().strip() or "(unnamed)",
                url_raw=url,
                # Manual edits are the source of truth from here on —
                # save the same value as "resolved" so script
                # generation and any future view both show exactly
                # what was typed rather than a stale {{variable}}
                # resolution from the original import.
                url_resolved=url,
                headers_json=json.dumps(self._collect_headers()),
                body_raw=self.body.toPlainText(),
            )

            self.accept()

        except Exception as ex:

            QMessageBox.critical(self, "Save Failed", str(ex))


# ==========================================================
# Main Page
# ==========================================================

class ManageKnowledgePage(QWidget):

    knowledge_changed = Signal()

    def __init__(self):

        super().__init__()

        self.manager = MetadataManager()
        self.repository = RepositoryManager()
        self.vector_store = VectorStore()
        self.pipeline = UploadPipeline()

        self.rows = []

        self.rows_by_id = {}

        self.build_ui()

        self.load_data()

    # ======================================================
    # UI
    # ======================================================

    def build_ui(self):

        layout = QVBoxLayout(self)

        title = QLabel("Manage Knowledge")

        title.setObjectName("SectionTitle")

        layout.addWidget(title)


        toolbar = QHBoxLayout()

        self.search = QLineEdit()

        self.search.setPlaceholderText("Search knowledge...")

        self.refresh_btn = QPushButton("Refresh")

        self.expand_all_btn = QPushButton("Expand All")

        self.collapse_all_btn = QPushButton("Collapse All")

        self.view_btn = QPushButton("View Details")

        self.edit_btn = QPushButton("Edit")

        # Only enabled for an endpoint node (an imported API
        # Collection request) — sends a REAL HTTP request to the
        # real, imported URL right now, no test case needed first.
        # See run_selected_endpoint() / Core/api_automation_runner.py.
        self.run_api_btn = QPushButton("Run Now")

        self.version_btn = QPushButton("Versions")

        self.delete_btn = QPushButton("Delete")

        toolbar.addWidget(self.search)

        toolbar.addWidget(self.refresh_btn)

        toolbar.addWidget(self.expand_all_btn)

        toolbar.addWidget(self.collapse_all_btn)

        toolbar.addWidget(self.view_btn)

        toolbar.addWidget(self.edit_btn)

        toolbar.addWidget(self.run_api_btn)

        toolbar.addWidget(self.version_btn)

        toolbar.addWidget(self.delete_btn)

        layout.addLayout(toolbar)


        self.tree = QTreeWidget()

        self.tree.setHeaderLabels(
            ["Domain / Module / Knowledge Name / Version / "
             "Document Type / File",
             "Status", "Confidence"]
        )

        self.tree.setColumnWidth(0, 480)

        self.tree.itemDoubleClicked.connect(
            self.edit_selected
        )

        self.tree.itemSelectionChanged.connect(
            self.update_button_states
        )

        layout.addWidget(self.tree)


        # Events

        self.refresh_btn.clicked.connect(self.load_data)

        self.expand_all_btn.clicked.connect(self.tree.expandAll)

        self.collapse_all_btn.clicked.connect(self.tree.collapseAll)

        self.search.textChanged.connect(self.search_data)

        self.view_btn.clicked.connect(self.view_details)

        self.edit_btn.clicked.connect(self.edit_selected)

        self.run_api_btn.clicked.connect(self.run_selected_endpoint)

        self.version_btn.clicked.connect(self.show_versions)

        self.delete_btn.clicked.connect(self.delete_selected)

        self.update_button_states()

    # ======================================================
    # Load
    # ======================================================

    def load_data(self):

        keyword = self.search.text().strip()

        try:

            if keyword:

                self.rows = self.manager.search(keyword)

            else:

                self.rows = self.manager.list_all()

        except Exception:

            self.rows = []

        self.rows_by_id = {
            row["id"]: row for row in self.rows
        }

        self.populate_tree(
            expand=bool(keyword)
        )

    def search_data(self):

        self.load_data()

    # ======================================================
    # Tree
    # ======================================================

    def populate_tree(self, expand=False):

        self.tree.clear()

        # domain -> module -> knowledge_name -> version -> document_type -> [rows]
        grouped = {}

        for row in self.rows:

            domain = row["domain"] or "Unspecified"

            module = row["module"] or "Unspecified"

            knowledge_name = row["knowledge_name"] or "Unspecified"

            version = row["version"] or "Unspecified"

            doc_type = row["document_type"] or "Unspecified"

            grouped.setdefault(domain, {}) \
                   .setdefault(module, {}) \
                   .setdefault(knowledge_name, {}) \
                   .setdefault(version, {}) \
                   .setdefault(doc_type, []) \
                   .append(row)

        for domain, modules in sorted(grouped.items()):

            domain_count = self._count_files(modules)

            domain_item = QTreeWidgetItem(
                [f"{domain} ({domain_count})", "", ""]
            )

            self.tree.addTopLevelItem(domain_item)

            for module, knowledge_names in sorted(modules.items()):

                module_count = self._count_files(knowledge_names)

                module_item = QTreeWidgetItem(
                    [f"{module} ({module_count})", "", ""]
                )

                domain_item.addChild(module_item)

                for kn, versions in sorted(knowledge_names.items()):

                    kn_count = self._count_files(versions)

                    kn_item = QTreeWidgetItem(
                        [f"{kn} ({kn_count})", "", ""]
                    )

                    module_item.addChild(kn_item)

                    for version, doc_types in sorted(versions.items()):

                        version_count = sum(
                            len(files) for files in doc_types.values()
                        )

                        version_item = QTreeWidgetItem(
                            [f"v{version} ({version_count})", "", ""]
                        )

                        kn_item.addChild(version_item)

                        for doc_type, file_rows in sorted(doc_types.items()):

                            doc_item = QTreeWidgetItem(
                                [f"{doc_type} ({len(file_rows)})", "", ""]
                            )

                            version_item.addChild(doc_item)

                            for file_row in file_rows:

                                file_item = QTreeWidgetItem([
                                    file_row["file_name"] or "(unnamed file)",
                                    file_row["status"] or "",
                                    str(file_row["confidence"] or ""),
                                ])

                                file_item.setData(
                                    0, ROW_ID_ROLE, file_row["id"]
                                )

                                doc_item.addChild(file_item)

                                # A row filed here by AI Smart Upload's
                                # guided URL capture has no real file
                                # behind it — nest the captured Steps/
                                # Fields/Locators under it instead.

                                if file_row["source_type"] == CAPTURED_FLOW_SOURCE_TYPE:

                                    self._populate_captured_flow_children(
                                        file_item, file_row["id"]
                                    )

                                # Same idea, for an imported Postman
                                # Collection (QA Automation -> API
                                # Upload) — nest its real endpoints
                                # under this node instead of a file
                                # preview, per the unified-hierarchy
                                # requirement.
                                elif file_row["source_type"] == API_COLLECTION_SOURCE_TYPE:

                                    self._populate_api_collection_children(
                                        file_item, file_row["id"]
                                    )

        if expand:

            self.tree.expandAll()

        else:

            self.tree.expandToDepth(0)

        self.update_button_states()

    def _populate_captured_flow_children(self, file_item, knowledge_item_id):
        """
        Nests every AI Smart Upload guided capture filed under this
        Knowledge Hub node as: Captured Flow -> Step -> Field/Locator.
        """

        try:
            flows = DiscoveryRepository().get_captured_flows_for_knowledge_item(
                knowledge_item_id
            )
        except Exception:
            flows = []

        for flow in flows:

            flow_item = QTreeWidgetItem([
                "Captured Flow: "
                f"{flow.get('application_name')} / "
                f"{flow.get('business_process_name')} / "
                f"{flow.get('variant_name')}",
                "",
                "",
            ])

            file_item.addChild(flow_item)

            for step in flow.get("steps", []):

                step_label = step.get("step_name") or (
                    f"Step {step.get('step_order')}"
                )

                if step.get("is_end_step"):
                    step_label += " (final step)"

                step_item = QTreeWidgetItem(
                    [f"{step.get('step_order')}. {step_label}", "", ""]
                )

                flow_item.addChild(step_item)

                for element in step.get("elements", []):

                    element_label = (
                        f"{element.get('name') or '(unnamed)'} "
                        f"[{element.get('element_type') or ''}] — "
                        f"{element.get('locator') or ''}"
                    )

                    element_item = QTreeWidgetItem([
                        element_label,
                        "Required" if element.get("is_required") else "",
                        element.get("locator_strategy") or "",
                    ])

                    step_item.addChild(element_item)

    def _populate_api_collection_children(self, file_item, knowledge_item_id):
        """
        Nests every endpoint of a Postman Collection imported here
        (QA Automation -> API Upload) as: Collection -> Endpoint.

        Same reasoning as _populate_captured_flow_children() above —
        more than one collection can be linked to the same Knowledge
        Name/Version over time (e.g. a refreshed export), so this
        shows one "Collection" child per import rather than
        assuming there is only one.
        """

        try:
            endpoints = ApiCollectionRepository().get_endpoints_for_knowledge_item(
                knowledge_item_id
            )
        except Exception:
            # Never let a Knowledge Hub tree refresh fail just
            # because the API collection tables couldn't be read.
            endpoints = []

        for endpoint in endpoints:

            folder_prefix = (
                f"{endpoint.get('folder_path')} / "
                if endpoint.get("folder_path") else ""
            )

            endpoint_label = (
                f"{endpoint.get('method') or ''} "
                f"{folder_prefix}{endpoint.get('name') or '(unnamed)'} "
                f"— {endpoint.get('url_resolved') or endpoint.get('url_raw') or ''}"
            )

            endpoint_item = QTreeWidgetItem([endpoint_label, "", ""])

            endpoint_item.setData(
                0, ENDPOINT_ID_ROLE, endpoint.get("id")
            )

            file_item.addChild(endpoint_item)

    @staticmethod
    def _count_files(subtree):

        total = 0

        for value in subtree.values():

            if isinstance(value, dict):

                total += ManageKnowledgePage._count_files(value)

            else:

                total += len(value)

        return total

    # ======================================================
    # Selection
    # ======================================================

    def selected_item(self):
        """
        Returns the underlying DB row for the selected item, but
        ONLY if a file (leaf node) is selected — group nodes
        (Domain/Module/Knowledge Name/Document Type) return None.
        """

        items = self.tree.selectedItems()

        if not items:

            return None

        item = items[0]

        row_id = item.data(0, ROW_ID_ROLE)

        if row_id is None:

            return None

        return self.rows_by_id.get(row_id)

    def selected_endpoint_id(self):
        """
        Returns the api_endpoints.id for the selected node, but ONLY
        if it's an endpoint leaf under an imported API Collection —
        every other node (file leaves included) returns None here.
        """

        items = self.tree.selectedItems()

        if not items:

            return None

        return items[0].data(0, ENDPOINT_ID_ROLE)

    def update_button_states(self):

        has_file_selected = self.selected_item() is not None

        has_endpoint_selected = self.selected_endpoint_id() is not None

        self.view_btn.setEnabled(has_file_selected)

        self.edit_btn.setEnabled(has_file_selected or has_endpoint_selected)

        self.run_api_btn.setEnabled(has_endpoint_selected)

        self.version_btn.setEnabled(has_file_selected)

        self.delete_btn.setEnabled(has_file_selected)

    # ======================================================
    # Details
    # ======================================================

    def view_details(self):

        row = self.selected_item()

        if not row:

            return

        data = {
            "ID": row["id"],
            "Domain": row["domain"],
            "Module": row["module"],
            "Knowledge Name": row["knowledge_name"],
            "Document Type": row["document_type"],
            "Upload Source": row["file_name"],
            "Version": row["version"],
            "Summary": row["summary"],
            "Tags": row["tags"],
            "Confidence": row["confidence"],
            "Status": row["status"],
        }

        dialog = KnowledgeDetailDialog(data, self)

        dialog.exec()

    # ======================================================
    # Edit
    # ======================================================

    def edit_selected(self):

        endpoint_id = self.selected_endpoint_id()

        if endpoint_id is not None:

            self.edit_selected_endpoint(endpoint_id)

            return

        row = self.selected_item()

        if not row:

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Select a specific file (the deepest level of the "
                "tree) to edit."
            )

            return

        dialog = EditKnowledgeDialog(row, self.manager, self)

        if dialog.exec() == QDialog.Accepted:

            self.load_data()

            self.knowledge_changed.emit()

    def edit_selected_endpoint(self, endpoint_id):

        repository = ApiCollectionRepository()

        endpoint = repository.get_endpoint(endpoint_id)

        if not endpoint:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "This endpoint could not be loaded — it may have "
                "been removed by a newer import."
            )

            return

                dialog = ApiEndpointEditorDialog(endpoint, repository, self)

        if dialog.exec() == QDialog.Accepted:

            self.load_data()

    # ======================================================
    # Run Now — "Execute Against Real Server" for a single endpoint,
    # no test case required. See Core/api_automation_runner.py.
    # ======================================================

    def run_selected_endpoint(self):

        endpoint_id = self.selected_endpoint_id()

        if endpoint_id is None:

            return

        repository = ApiCollectionRepository()

        endpoint = repository.get_endpoint(endpoint_id)

        if not endpoint:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "This endpoint could not be loaded — it may have "
                "been removed by a newer import."
            )

            return

        method = endpoint.get("method", "")

        url = endpoint.get("url_resolved") or endpoint.get("url_raw", "")

        confirm = QMessageBox.question(
            self,
            "Run Now?",
            f"This sends a REAL HTTP request:\n\n{method} {url}\n\n"
            f"Make sure this points at a TEST/UAT server, not "
            f"production. Continue?",
            QMessageBox.Yes | QMessageBox.No,
        )

        if confirm != QMessageBox.Yes:

            return

        outcome = self.run_single_api_endpoint(
            endpoint.get("name") or "(unnamed)", endpoint
        )

        if outcome == "Pass":

            QMessageBox.information(
                self, "QA AI Studio", "Marked as Pass."
            )

        elif outcome == "Fail":

            QMessageBox.information(
                self, "QA AI Studio", "Marked as Fail."
            )

        # "Cancelled" needs no further message — the operator already
        # chose to stop partway through (missing variable, or Cancel
        # on the result dialog).

    def run_single_api_endpoint(self, label, endpoint):
        """
        Thin wrapper kept on this page for readability at its call
        site (run_selected_endpoint() above) — the actual resolve/
        send/show-result/retry loop is
        UI.QAAutomation.api_run_dialogs.run_api_endpoint_interactive(),
        shared with QA Automation Execute's "Execute Against Real
        Server" (see test_execution_page.py's
        run_api_automation_rows()) so both entry points behave
        identically. Returns "Pass", "Fail", or "Cancelled".
        """

        from UI.QAAutomation.api_run_dialogs import (
            run_api_endpoint_interactive,
        )

        return run_api_endpoint_interactive(self, label, endpoint)

    # ======================================================
    # Versions
    # ======================================================

    def show_versions(self):

        row = self.selected_item()

        if not row:

            return

        versions = self.manager.get_versions(row["id"])

        message = ""

        for item in versions:

            message += (
                f"Version: {item[0]}\n"
                f"SHA256: {item[1]}\n"
                f"Path: {item[2]}\n"
                f"Created: {item[3]}\n\n"
            )

        QMessageBox.information(
            self,
            "Version History",
            message or "No versions found."
        )

    # ======================================================
    # Delete
    # ======================================================

    def delete_selected(self):

        row = self.selected_item()

        if not row:

            return

        confirm = QMessageBox.question(
            self,
            "Delete Knowledge",
            f"Delete '{row['file_name']}' from "
            f"{row['knowledge_name']}?"
        )

        if confirm != QMessageBox.Yes:

            return

        try:

            self.repository.delete_knowledge(
                row["domain"],
                row["module"],
                row["knowledge_name"],
            )

            self.vector_store.delete_by_knowledge_name(
                row["knowledge_name"]
            )

            self.manager.delete(row["id"])

            QMessageBox.information(
                self, "QA AI Studio", "Knowledge deleted."
            )

            self.load_data()

            self.knowledge_changed.emit()

        except Exception as ex:

            QMessageBox.critical(self, "Delete Failed", str(ex))