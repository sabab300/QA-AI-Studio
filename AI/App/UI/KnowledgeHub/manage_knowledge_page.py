"""
==========================================================
QA AI Studio

Knowledge Hub

Manage Knowledge

Version : 5.0

Enterprise Knowledge Manager

Features
--------
• Tree Based Knowledge Manager
• Domain → Module → Knowledge hierarchy
• Search
• CRUD Toolbar
• Editable Properties
• Repository Integration
• Metadata Integration
==========================================================
"""

from pathlib import Path

from PySide6.QtCore import Qt

from PySide6.QtWidgets import (
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QSplitter,
    QLabel,
    QPushButton,
    QLineEdit,
    QTreeWidget,
    QTreeWidgetItem,
    QGroupBox,
    QFormLayout,
    QComboBox,
    QMessageBox,
    QInputDialog,
    QFrame
)

from Core.metadata_manager import MetadataManager
from Core.repository_manager import RepositoryManager
from Core.vector_store import VectorStore


class ManageKnowledgePage(QWidget):

    def __init__(self):

        super().__init__()

        self.manager = MetadataManager()
        self.repository = RepositoryManager()
        self.vector_store = VectorStore()

        self.current_record = None

        self.build_ui()

        self.load_tree()

    # ======================================================
    # UI
    # ======================================================

    def build_ui(self):

        root = QVBoxLayout(self)

        title = QLabel("Enterprise Knowledge Manager")
        title.setObjectName("SectionTitle")

        root.addWidget(title)

        # --------------------------------------------------
        # Search
        # --------------------------------------------------

        search_layout = QHBoxLayout()

        self.search = QLineEdit()

        self.search.setPlaceholderText(
            "Search Domain, Module or Knowledge..."
        )

        self.refresh_btn = QPushButton("Refresh")

        search_layout.addWidget(self.search)

        search_layout.addWidget(self.refresh_btn)

        root.addLayout(search_layout)

        # --------------------------------------------------
        # Toolbar
        # --------------------------------------------------

        toolbar = QHBoxLayout()

        self.add_domain_btn = QPushButton("New Domain")

        self.add_module_btn = QPushButton("New Module")

        self.add_knowledge_btn = QPushButton("New Knowledge")

        self.rename_btn = QPushButton("Rename")

        self.move_btn = QPushButton("Move")

        self.delete_btn = QPushButton("Delete")

        toolbar.addWidget(self.add_domain_btn)

        toolbar.addWidget(self.add_module_btn)

        toolbar.addWidget(self.add_knowledge_btn)

        toolbar.addSpacing(15)

        toolbar.addWidget(self.rename_btn)

        toolbar.addWidget(self.move_btn)

        toolbar.addWidget(self.delete_btn)

        toolbar.addStretch()

        root.addLayout(toolbar)

        # --------------------------------------------------
        # Splitter
        # --------------------------------------------------

        splitter = QSplitter(Qt.Horizontal)

        root.addWidget(splitter)

        # ==================================================
        # LEFT
        # ==================================================

        left = QWidget()

        left_layout = QVBoxLayout(left)

        self.tree = QTreeWidget()

        self.tree.setHeaderLabel(
            "Knowledge Repository"
        )

        self.tree.setAlternatingRowColors(True)

        self.tree.setAnimated(True)

        left_layout.addWidget(self.tree)

        splitter.addWidget(left)

        # ==================================================
        # RIGHT
        # ==================================================

        right = QWidget()

        right_layout = QVBoxLayout(right)

        info_group = QGroupBox(
            "Knowledge Information"
        )

        form = QFormLayout(info_group)

        self.domain = QLineEdit()

        self.module = QLineEdit()

        self.knowledge = QLineEdit()

        self.version = QLineEdit()

        self.document_type = QComboBox()

        self.document_type.addItems([
            "SRS",
            "CRF",
            "API",
            "Test Case",
            "SQL",
            "Automation",
            "SOP",
            "Release Notes",
            "Other"
        ])

        self.upload_source = QLineEdit()

        form.addRow("Domain", self.domain)

        form.addRow("Module", self.module)

        form.addRow("Knowledge Name", self.knowledge)

        form.addRow("Version", self.version)

        form.addRow("Document Type", self.document_type)

        form.addRow("Upload Source", self.upload_source)

        right_layout.addWidget(info_group)

        button_bar = QHBoxLayout()

        self.save_btn = QPushButton("Save")

        self.cancel_btn = QPushButton("Cancel")

        button_bar.addStretch()

        button_bar.addWidget(self.save_btn)

        button_bar.addWidget(self.cancel_btn)

        right_layout.addLayout(button_bar)

        right_layout.addStretch()

        splitter.addWidget(right)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)

        # --------------------------------------------------
        # Events
        # --------------------------------------------------

        self.refresh_btn.clicked.connect(
            self.load_tree
        )

        self.search.textChanged.connect(
            self.filter_tree
        )

        self.tree.itemClicked.connect(
            self.on_tree_selected
        )

        self.add_domain_btn.clicked.connect(
            self.add_domain
        )

        self.add_module_btn.clicked.connect(
            self.add_module
        )

        self.add_knowledge_btn.clicked.connect(
            self.add_knowledge
        )

        self.rename_btn.clicked.connect(
            self.rename_item
        )

        self.move_btn.clicked.connect(
            self.move_item
        )

        self.delete_btn.clicked.connect(
            self.delete_item
        )

        self.save_btn.clicked.connect(
            self.save_changes
        )

        self.cancel_btn.clicked.connect(
            self.load_selected
        )

    # ======================================================
    # Load Tree
    # ======================================================

    def load_tree(self):

        self.tree.clear()

        try:

            tree = self.manager.get_tree()

        except Exception as ex:

            QMessageBox.critical(
                self,
                "QA AI Studio",
                str(ex)
            )

            return

        for domain_name in sorted(tree.keys()):

            domain_item = QTreeWidgetItem(
                [domain_name]
            )

            domain_item.setData(
                0,
                Qt.UserRole,
                {
                    "type": "domain",
                    "domain": domain_name
                }
            )

            self.tree.addTopLevelItem(
                domain_item
            )

            modules = tree[domain_name]

            for module_name in sorted(modules.keys()):

                module_item = QTreeWidgetItem(
                    [module_name]
                )

                module_item.setData(
                    0,
                    Qt.UserRole,
                    {
                        "type": "module",
                        "domain": domain_name,
                        "module": module_name
                    }
                )

                domain_item.addChild(
                    module_item
                )

                knowledges = modules[module_name]

                for knowledge_name in sorted(
                    knowledges.keys()
                ):

                    knowledge_item = QTreeWidgetItem(
                        [knowledge_name]
                    )

                    knowledge_item.setData(
                        0,
                        Qt.UserRole,
                        {
                            "type": "knowledge",
                            "domain": domain_name,
                            "module": module_name,
                            "knowledge": knowledge_name
                        }
                    )

                    module_item.addChild(
                        knowledge_item
                    )

        self.tree.expandAll()


    # ======================================================
    # Search
    # ======================================================

    def filter_tree(self, text):

        text = text.lower().strip()

        for i in range(self.tree.topLevelItemCount()):

            domain_item = self.tree.topLevelItem(i)

            domain_visible = False

            for j in range(domain_item.childCount()):

                module_item = domain_item.child(j)

                module_visible = False

                for k in range(module_item.childCount()):

                    knowledge_item = module_item.child(k)

                    visible = (
                        text == ""
                        or text in knowledge_item.text(0).lower()
                        or text in module_item.text(0).lower()
                        or text in domain_item.text(0).lower()
                    )

                    knowledge_item.setHidden(
                        not visible
                    )

                    if visible:

                        module_visible = True

                        domain_visible = True

                module_item.setHidden(
                    not module_visible
                )

            domain_item.setHidden(
                not domain_visible
            )


    # ======================================================
    # Selection
    # ======================================================

    def on_tree_selected(self, item):

        data = item.data(
            0,
            Qt.UserRole
        )

        if not data:

            return

        if data["type"] != "knowledge":

            self.current_record = None

            self.clear_editor()

            return

        record = self.manager.get_knowledge_item(

            data["domain"],

            data["module"],

            data["knowledge"]

        )

        if not record:

            return

        self.current_record = record

        self.load_selected()


    # ======================================================
    # Load Selected
    # ======================================================

    def load_selected(self):

        if not self.current_record:

            self.clear_editor()

            return

        row = self.current_record

        self.domain.setText(
            row[1]
        )

        self.module.setText(
            row[2]
        )

        self.knowledge.setText(
            row[3]
        )

        self.version.setText(
            row[4]
        )

        index = self.document_type.findText(
            row[19] if len(row) > 19 else ""
        )

        if index >= 0:

            self.document_type.setCurrentIndex(
                index
            )

        else:

            self.document_type.setCurrentIndex(0)

        upload_source = ""

        if len(row) > 7:

            upload_source = row[7]

        self.upload_source.setText(
            str(upload_source)
        )


    # ======================================================
    # Clear Editor
    # ======================================================

    def clear_editor(self):

        self.domain.clear()

        self.module.clear()

        self.knowledge.clear()

        self.version.clear()

        self.upload_source.clear()

        self.document_type.setCurrentIndex(0)

        # ======================================================
    # New Domain
    # ======================================================

    def add_domain(self):

        name, ok = QInputDialog.getText(
            self,
            "New Domain",
            "Domain Name"
        )

        if not ok or not name.strip():
            return

        name = name.strip()

        try:

            item = QTreeWidgetItem([name])

            item.setData(
                0,
                Qt.UserRole,
                {
                    "type": "domain",
                    "domain": name
                }
            )

            self.tree.addTopLevelItem(item)

            self.tree.setCurrentItem(item)

        except Exception as ex:

            QMessageBox.critical(
                self,
                "QA AI Studio",
                str(ex)
            )


    # ======================================================
    # New Module
    # ======================================================

    def add_module(self):

        item = self.tree.currentItem()

        if item is None:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select a Domain first."
            )

            return

        data = item.data(0, Qt.UserRole)

        if data["type"] == "module":

            item = item.parent()

            data = item.data(0, Qt.UserRole)

        if data["type"] != "domain":

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select a Domain first."
            )

            return

        module_name, ok = QInputDialog.getText(
            self,
            "New Module",
            "Module Name"
        )

        if not ok or not module_name.strip():
            return

        module_name = module_name.strip()

        module_item = QTreeWidgetItem([module_name])

        module_item.setData(
            0,
            Qt.UserRole,
            {
                "type": "module",
                "domain": data["domain"],
                "module": module_name
            }
        )

        item.addChild(module_item)

        item.setExpanded(True)

        self.tree.setCurrentItem(module_item)


    # ======================================================
    # New Knowledge
    # ======================================================

    def add_knowledge(self):

        item = self.tree.currentItem()

        if item is None:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select a Module first."
            )

            return

        data = item.data(0, Qt.UserRole)

        if data["type"] == "knowledge":

            item = item.parent()

            data = item.data(0, Qt.UserRole)

        if data["type"] != "module":

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Select a Module first."
            )

            return

        name, ok = QInputDialog.getText(
            self,
            "New Knowledge",
            "Knowledge Name"
        )

        if not ok or not name.strip():
            return

        name = name.strip()

        child = QTreeWidgetItem([name])

        child.setData(
            0,
            Qt.UserRole,
            {
                "type": "knowledge",
                "domain": data["domain"],
                "module": data["module"],
                "knowledge": name
            }
        )

        item.addChild(child)

        item.setExpanded(True)

        self.tree.setCurrentItem(child)


    # ======================================================
    # Rename
    # ======================================================

    def rename_item(self):

        item = self.tree.currentItem()

        if item is None:
            return

        value, ok = QInputDialog.getText(

            self,

            "Rename",

            "New Name",

            text=item.text(0)

        )

        if not ok or not value.strip():
            return

        item.setText(
            0,
            value.strip()
        )


    # ======================================================
    # Delete
    # ======================================================

    def delete_item(self):

        item = self.tree.currentItem()

        if item is None:
            return

        answer = QMessageBox.question(

            self,

            "Delete",

            f"Delete '{item.text(0)}' ?"

        )

        if answer != QMessageBox.Yes:
            return

        parent = item.parent()

        if parent:

            parent.removeChild(item)

        else:

            index = self.tree.indexOfTopLevelItem(item)

            self.tree.takeTopLevelItem(index)

        self.clear_editor()


    # ======================================================
    # Move Knowledge
    # ======================================================

    def move_item(self):

        if not self.current_record:

            QMessageBox.information(
                self,
                "QA AI Studio",
                "Select a Knowledge item first."
            )
            return

        domains = self.manager.get_domains()

        if not domains:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "No domains available."
            )
            return

        domain, ok = QInputDialog.getItem(
            self,
            "Move Knowledge",
            "Target Domain",
            domains,
            0,
            False
        )

        if not ok:
            return

        modules = self.manager.get_modules(domain)

        if not modules:

            QMessageBox.warning(
                self,
                "QA AI Studio",
                "Selected domain has no modules."
            )
            return

        module, ok = QInputDialog.getItem(
            self,
            "Move Knowledge",
            "Target Module",
            modules,
            0,
            False
        )

        if not ok:
            return

        try:

            self.manager.move_knowledge(

                self.current_record[0],

                domain,

                module

            )

            self.load_tree()

            QMessageBox.information(

                self,

                "QA AI Studio",

                "Knowledge moved successfully."

            )

        except Exception as ex:

            QMessageBox.critical(

                self,

                "QA AI Studio",

                str(ex)

            )


    # ======================================================
    # Save Changes
    # ======================================================

    def save_changes(self):

        if not self.current_record:

            QMessageBox.information(

                self,

                "QA AI Studio",

                "Select a Knowledge item."

            )

            return

        try:

            self.manager.update_knowledge(

                knowledge_id=self.current_record[0],

                domain=self.domain.text().strip(),

                module=self.module.text().strip(),

                knowledge_name=self.knowledge.text().strip(),

                version=self.version.text().strip(),

                document_type=self.document_type.currentText(),

                upload_source=self.upload_source.text().strip()

            )

            self.load_tree()

            QMessageBox.information(

                self,

                "QA AI Studio",

                "Knowledge updated successfully."

            )

        except Exception as ex:

            QMessageBox.critical(

                self,

                "QA AI Studio",

                str(ex)

            )
    