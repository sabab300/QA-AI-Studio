"""
==========================================================
QA AI Studio

Knowledge Hub

Manage Knowledge

Production Version 3.0
==========================================================
"""

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QTextEdit,
    QDialog,
    QDialogButtonBox
)

from Core.metadata_manager import MetadataManager
from Core.repository_manager import RepositoryManager
from Core.vector_store import VectorStore


class KnowledgeDetailDialog(QDialog):

    def __init__(self, data, parent=None):

        super().__init__(parent)

        self.setWindowTitle("Knowledge Details")
        self.resize(750, 600)

        layout = QVBoxLayout(self)

        viewer = QTextEdit()
        viewer.setReadOnly(True)

        for key, value in data.items():
            viewer.append(f"{key}:")
            viewer.append(str(value))
            viewer.append("")

        layout.addWidget(viewer)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok
        )

        buttons.accepted.connect(
            self.accept
        )

        layout.addWidget(buttons)


class ManageKnowledgePage(QWidget):

    def __init__(self):

        super().__init__()

        self.manager = MetadataManager()
        self.repository = RepositoryManager()
        self.vector_store = VectorStore()

        self.rows = []

        self.build_ui()

        self.load_data()

    # ==================================================
    # UI
    # ==================================================

    def build_ui(self):

        main_layout = QVBoxLayout(self)

        title = QLabel("Manage Knowledge")

        title.setObjectName(
            "SectionTitle"
        )

        main_layout.addWidget(title)

        toolbar = QHBoxLayout()

        self.search = QLineEdit()

        self.search.setPlaceholderText(
            "Search..."
        )

        self.search.setMaximumWidth(350)

        toolbar.addWidget(
            self.search
        )

        toolbar.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.view_btn = QPushButton("Details")
        self.version_btn = QPushButton("Versions")
        self.delete_btn = QPushButton("Delete")

        buttons = [
            self.refresh_btn,
            self.view_btn,
            self.version_btn,
            self.delete_btn
        ]

        for button in buttons:

            button.setMinimumWidth(95)
            button.setMaximumWidth(95)
            button.setFixedHeight(30)

            toolbar.addWidget(button)

        main_layout.addLayout(toolbar)

        self.table = QTableWidget()

        headers = [

            "ID",
            "Domain",
            "Module",
            "Knowledge",
            "Version",
            "Platform",
            "Category",
            "Document",
            "Confidence",
            "Status"

        ]

        self.table.setColumnCount(len(headers))
        self.table.setHorizontalHeaderLabels(headers)

        self.table.setSelectionBehavior(
            QTableWidget.SelectRows
        )

        self.table.setSelectionMode(
            QTableWidget.SingleSelection
        )

        self.table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )

        self.table.setAlternatingRowColors(True)

        self.table.verticalHeader().setVisible(False)

        header = self.table.horizontalHeader()

        header.setStretchLastSection(False)

        widths = [
            60,
            120,
            120,
            220,
            80,
            120,
            120,
            120,
            90,
            100
        ]

        for column, width in enumerate(widths):
            header.setSectionResizeMode(
                column,
                QHeaderView.Interactive
            )
            self.table.setColumnWidth(
                column,
                width
            )

        header.setSectionResizeMode(
            3,
            QHeaderView.Stretch
        )

        main_layout.addWidget(self.table)

        self.refresh_btn.clicked.connect(
            self.load_data
        )

        self.search.textChanged.connect(
            self.search_data
        )

        self.view_btn.clicked.connect(
            self.view_details
        )

        self.version_btn.clicked.connect(
            self.show_versions
        )

        self.delete_btn.clicked.connect(
            self.delete_selected
        )

        # ======================================================
    # Load Data
    # ======================================================

    def load_data(self):

        try:

            keyword = self.search.text().strip()

            if keyword:

                self.rows = self.manager.search(keyword)

            else:

                self.rows = self.manager.list_all()

        except Exception:

            self.rows = []

        self.populate_table()


    # ======================================================
    # Search
    # ======================================================

    def search_data(self):

        self.load_data()


    # ======================================================
    # Populate Table
    # ======================================================

    def populate_table(self):

        self.table.setSortingEnabled(False)

        self.table.clearContents()

        self.table.setRowCount(0)

        if not self.rows:

            return

        for row in self.rows:

            r = self.table.rowCount()

            self.table.insertRow(r)

            values = [

                row[0],     # ID
                row[1],     # Domain
                row[2],     # Module
                row[3],     # Knowledge
                row[4],     # Version
                row[16],    # Platform
                row[17],    # Category
                row[19],    # Document Type
                row[15],    # Confidence
                row[20],    # Status

            ]

            for c, value in enumerate(values):

                item = QTableWidgetItem("" if value is None else str(value))

                self.table.setItem(r, c, item)

        self.table.resizeRowsToContents()

        self.table.setSortingEnabled(True)


    # ======================================================
    # Selected Item
    # ======================================================

    def selected_item(self):

        row_index = self.table.currentRow()

        if row_index < 0:

            QMessageBox.information(

                self,

                "QA AI Studio",

                "Please select a knowledge record."

            )

            return None

        if row_index >= len(self.rows):

            return None

        return self.rows[row_index]

        # ======================================================
    # Details
    # ======================================================

    def view_details(self):

        row = self.selected_item()

        if row is None:

            return

        data = {

            "ID": row[0],
            "Domain": row[1],
            "Module": row[2],
            "Knowledge": row[3],
            "Version": row[4],
            "Summary": row[13],
            "Tags": row[14],
            "Confidence": row[15],
            "Platform": row[16],
            "Category": row[17],
            "Business Process": row[18],
            "Document Type": row[19],
            "Status": row[20]

        }

        dialog = KnowledgeDetailDialog(
            data,
            self
        )

        dialog.exec()


    # ======================================================
    # Versions
    # ======================================================

    def show_versions(self):

        row = self.selected_item()

        if row is None:

            return

        versions = self.manager.get_versions(
            row[0]
        )

        if not versions:

            QMessageBox.information(

                self,

                "Version History",

                "No version history found."

            )

            return

        text = ""

        for version in versions:

            text += (
                f"Version : {version[0]}\n"
                f"SHA256  : {version[1]}\n"
                f"Path    : {version[2]}\n"
                f"Created : {version[3]}\n\n"
            )

        QMessageBox.information(

            self,

            "Version History",

            text

        )


    # ======================================================
    # Delete
    # ======================================================

    def delete_selected(self):

        row = self.selected_item()

        if row is None:

            return

        knowledge_id = row[0]
        domain = row[1]
        module = row[2]
        knowledge_name = row[3]

        answer = QMessageBox.question(

            self,

            "Delete Knowledge",

            (
                f"Delete '{knowledge_name}'?\n\n"
                "This will permanently remove:\n"
                "- Repository files\n"
                "- Database records\n"
                "- ChromaDB vectors"
            ),

            QMessageBox.Yes | QMessageBox.No,

            QMessageBox.No

        )

        if answer != QMessageBox.Yes:

            return

        try:

            _, files_deleted = self.repository.delete_knowledge(
                domain,
                module,
                knowledge_name
            )

            vectors_deleted = self.vector_store.delete_by_knowledge_name(
                knowledge_name
            )

            db_result = self.manager.delete(
                knowledge_id
            )

            QMessageBox.information(

                self,

                "QA AI Studio",

                f"""Knowledge deleted successfully.

            Repository Files : {files_deleted}

            Vector Chunks : {vectors_deleted}

            Knowledge Records : {db_result['knowledge']}

            Versions Removed : {db_result['versions']}

            Embedding Queue : {db_result['queue']}
            """
            )

            self.load_data()

        except Exception as ex:

            QMessageBox.critical(

                self,

                "Delete Failed",

                str(ex)

            )

        # ======================================================
    # Refresh
    # ======================================================

    def refresh(self):

        self.load_data()


    # ======================================================
    # Public API
    # ======================================================

    def reload(self):

        self.load_data()


    # ======================================================
    # Cleanup
    # ======================================================

    def closeEvent(self, event):

        try:

            self.table.clearSelection()

        except Exception:

            pass

        super().closeEvent(event)