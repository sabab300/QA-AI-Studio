"""
==========================================================
QA AI Studio

Knowledge Hub
Domain Manager

Version : 1.0

Features:
    - Enterprise Domain Management
    - Domain CRUD
    - Active / Inactive status
    - MetadataManager integration
==========================================================
"""

from datetime import datetime

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QHeaderView,
    QMessageBox,
    QDialog,
    QLineEdit,
    QTextEdit,
    QDialogButtonBox,
    QComboBox
)

from Core.metadata_manager import MetadataManager


# ======================================================
# Domain Dialog
# ======================================================

class DomainDialog(QDialog):

    def __init__(self, parent=None, data=None):

        super().__init__(parent)

        self.setWindowTitle(
            "Domain"
        )

        self.resize(
            400,
            250
        )

        self.data = data

        layout = QVBoxLayout(self)


        self.name = QLineEdit()

        self.name.setPlaceholderText(
            "Domain Name"
        )


        self.description = QTextEdit()

        self.description.setPlaceholderText(
            "Description"
        )


        self.status = QComboBox()

        self.status.addItems(
            [
                "Active",
                "Inactive"
            ]
        )


        layout.addWidget(
            QLabel("Name")
        )

        layout.addWidget(
            self.name
        )


        layout.addWidget(
            QLabel("Description")
        )

        layout.addWidget(
            self.description
        )


        layout.addWidget(
            QLabel("Status")
        )

        layout.addWidget(
            self.status
        )


        buttons = QDialogButtonBox(
            QDialogButtonBox.Save |
            QDialogButtonBox.Cancel
        )


        buttons.accepted.connect(
            self.accept
        )

        buttons.rejected.connect(
            self.reject
        )


        layout.addWidget(
            buttons
        )


        if data:

            self.name.setText(
                str(data[1])
            )

            self.description.setText(
                str(data[2] or "")
            )

            self.status.setCurrentText(
                str(data[3])
            )



# ======================================================
# Domain Manager Page
# ======================================================

class DomainManagerPage(QWidget):

    def __init__(self):

        super().__init__()

        self.manager = MetadataManager()

        self.rows = []

        self.build_ui()

        self.load_data()



    # ==================================================
    # UI
    # ==================================================

    def build_ui(self):

        layout = QVBoxLayout(
            self
        )


        title = QLabel(
            "Enterprise Domain Management"
        )

        title.setObjectName(
            "SectionTitle"
        )


        layout.addWidget(
            title
        )


        toolbar = QHBoxLayout()


        self.add_btn = QPushButton(
            "Add Domain"
        )

        self.edit_btn = QPushButton(
            "Edit"
        )

        self.delete_btn = QPushButton(
            "Delete"
        )

        self.status_btn = QPushButton(
            "Activate / Deactivate"
        )

        self.refresh_btn = QPushButton(
            "Refresh"
        )


        toolbar.addWidget(
            self.add_btn
        )

        toolbar.addWidget(
            self.edit_btn
        )

        toolbar.addWidget(
            self.delete_btn
        )

        toolbar.addWidget(
            self.status_btn
        )

        toolbar.addWidget(
            self.refresh_btn
        )


        toolbar.addStretch()


        layout.addLayout(
            toolbar
        )


        self.table = QTableWidget()


        self.table.setColumnCount(
            6
        )


        self.table.setHorizontalHeaderLabels(
            [
                "ID",
                "Domain",
                "Description",
                "Status",
                "Created",
                "Modified"
            ]
        )


        self.table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch
        )


        self.table.setSelectionBehavior(
            QTableWidget.SelectRows
        )


        self.table.setEditTriggers(
            QTableWidget.NoEditTriggers
        )


        layout.addWidget(
            self.table
        )


        self.add_btn.clicked.connect(
            self.add_domain
        )

        self.edit_btn.clicked.connect(
            self.edit_domain
        )

        self.delete_btn.clicked.connect(
            self.delete_domain
        )

        self.status_btn.clicked.connect(
            self.toggle_status
        )

        self.refresh_btn.clicked.connect(
            self.load_data
        )


    # ==================================================
    # Load
    # ==================================================

    def load_data(self):

        try:

            self.rows = self.manager.list_domains()

        except Exception:

            self.rows = []


        self.table.setRowCount(
            0
        )


        for row in self.rows:

            index = self.table.rowCount()

            self.table.insertRow(
                index
            )


            for col, value in enumerate(row):

                self.table.setItem(
                    index,
                    col,
                    QTableWidgetItem(
                        str(value)
                    )
                )


    # ==================================================
    # Selected
    # ==================================================

    def selected_domain(self):

        row = self.table.currentRow()

        if row < 0:

            return None


        return self.rows[row]


    # ==================================================
    # Add
    # ==================================================

    def add_domain(self):

        dialog = DomainDialog(
            self
        )


        if dialog.exec():

            try:

                self.manager.create_domain(
                    dialog.name.text(),
                    dialog.description.toPlainText()
                )

                self.load_data()


            except Exception as ex:

                QMessageBox.critical(
                    self,
                    "Error",
                    str(ex)
                )


    # ==================================================
    # Edit
    # ==================================================

    def edit_domain(self):

        row = self.selected_domain()

        if not row:
            return


        dialog = DomainDialog(
            self,
            row
        )


        if dialog.exec():

            self.manager.update_domain(
                row[0],
                dialog.name.text(),
                dialog.description.toPlainText()
            )

            self.manager.update_domain_status(
                row[0],
                dialog.status.currentText()
            )


            self.load_data()



    # ==================================================
    # Delete
    # ==================================================

    def delete_domain(self):

        row = self.selected_domain()

        if not row:
            return


        confirm = QMessageBox.question(
            self,
            "Delete Domain",
            f"Delete {row[1]}?"
        )


        if confirm == QMessageBox.Yes:

            self.manager.delete_domain(
                row[0]
            )

            self.load_data()



    # ==================================================
    # Status
    # ==================================================

    def toggle_status(self):

        row = self.selected_domain()

        if not row:
            return


        new_status = (
            "Inactive"
            if row[3] == "Active"
            else "Active"
        )


        self.manager.update_domain_status(
            row[0],
            new_status
        )


        self.load_data()