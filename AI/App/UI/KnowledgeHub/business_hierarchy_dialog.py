"""
QA AI Studio - Business Process Hierarchy Review Dialog
Location: App/UI/KnowledgeHub/business_hierarchy_dialog.py
"""

from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QTreeWidget, QTreeWidgetItem, QGroupBox, QFormLayout, QMessageBox
)
from PySide6.QtCore import Qt


class BusinessHierarchyDialog(QDialog):
    """
    Modal dialog allowing users to inspect, modify, and confirm discovered
    Business Hierarchy and Field Mappings prior to final database storage.
    """

    def __init__(self, discovery_data: dict, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Review Discovered Business Process Hierarchy")
        self.resize(700, 550)
        self.discovery_data = discovery_data
        self.confirmed_data = None
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        # Header Notice
        header = QLabel("AI Discovered Business Process Hierarchy")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #1E293B;")
        layout.addWidget(header)

        sub_header = QLabel("Verify or edit the extracted application hierarchy before committing to Knowledge Hub.")
        sub_header.setStyleSheet("color: #64748B; margin-bottom: 10px;")
        layout.addWidget(sub_header)

        # Hierarchy Input Group (Editable)
        group_box = QGroupBox("Domain & Process Metadata")
        form_layout = QFormLayout(group_box)

        self.txt_app_name = QLineEdit(self.discovery_data.get("application_name", "Pakistan Single Window"))
        self.txt_domain = QLineEdit(self.discovery_data.get("domain_name", "Customs Clearance"))
        self.txt_process = QLineEdit(self.discovery_data.get("business_process", "SD Export Declaration"))
        self.txt_variant = QLineEdit(self.discovery_data.get("variant_name", "Standard Form V1"))

        form_layout.addRow("Application:", self.txt_app_name)
        form_layout.addRow("Domain / Module:", self.txt_domain)
        form_layout.addRow("Business Process:", self.txt_process)
        form_layout.addRow("Process Variant:", self.txt_variant)

        layout.addWidget(group_box)

        # Discovered Elements Hierarchy Tree View
        tree_label = QLabel("Discovered Steps, Tabs & Locators:")
        tree_label.setStyleSheet("font-weight: bold; margin-top: 5px;")
        layout.addWidget(tree_label)

        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(["Hierarchy / Element Name", "Type / Locator", "Origin Context"])
        self.tree.setColumnWidth(0, 250)
        self.tree.setColumnWidth(1, 250)

        self._populate_tree()
        layout.addWidget(self.tree)

        # Action Buttons
        button_box = QHBoxLayout()
        button_box.addStretch()

        self.btn_cancel = QPushButton("Cancel / Abort")
        self.btn_cancel.clicked.connect(self.reject)

        self.btn_save = QPushButton("Confirm & Save to Knowledge Hub")
        self.btn_save.setStyleSheet("background-color: #2563EB; color: white; font-weight: bold; padding: 6px 12px;")
        self.btn_save.clicked.connect(self._on_confirm)

        button_box.addWidget(self.btn_cancel)
        button_box.addWidget(self.btn_save)
        layout.addLayout(button_box)

    def _populate_tree(self):
        """Populates hierarchy tree grouped by discovered tabs/pages."""
        pages = self.discovery_data.get("pages", [])
        fields = self.discovery_data.get("fields", [])
        buttons = self.discovery_data.get("buttons", [])

        # Root Node
        root_item = QTreeWidgetItem(self.tree, [self.txt_process.text(), "Process Root", "URL Discovery"])

        # Group by Tab / Context
        tabs = self.discovery_data.get("tabs", [])
        if not tabs:
            tabs = [{"text": "Main View", "locator": "root"}]

        for tab in tabs:
            tab_name = tab.get("text", "Default Context")
            tab_node = QTreeWidgetItem(root_item, [f"Tab: {tab_name}", "Step / Container", tab.get("locator", "")])

            # Add fields associated with this tab
            for f in fields:
                if f.get("tab_origin") == tab_name or not f.get("tab_origin"):
                    field_label = f.get("name") or f.get("placeholder") or f.get("element_id") or "Input Field"
                    QTreeWidgetItem(tab_node, [field_label, f.get("locator", "N/A"), "Field"])

            # Add buttons associated with this tab
            for b in buttons:
                if b.get("tab_origin") == tab_name or not b.get("tab_origin"):
                    btn_label = f.get("text") or "Action Control"
                    QTreeWidgetItem(tab_node, [btn_label, b.get("locator", "N/A"), "Action"])

        self.tree.expandAll()

    def _on_confirm(self):
        """Extracts modified values from input fields and prepares payload for DB commit."""
        self.confirmed_data = {
            "application_name": self.txt_app_name.text().strip(),
            "domain_name": self.txt_domain.text().strip(),
            "business_process": self.txt_process.text().strip(),
            "variant_name": self.txt_variant.text().strip(),
            "raw_discovery": self.discovery_data
        }
        self.accept()