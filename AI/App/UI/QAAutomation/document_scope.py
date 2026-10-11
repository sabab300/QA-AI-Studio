"""Document-owned version controls shared by Desktop automation tabs."""
from PySide6.QtWidgets import QComboBox, QLabel
from Shared.document_hierarchy import DocumentHierarchy


class DocumentScope:
    def __init__(self, page, layout):
        self.page = page
        self.document_type = QComboBox()
        self.version = QComboBox()
        layout.addWidget(QLabel('Document Type'), 2, 0)
        layout.addWidget(self.document_type, 2, 1)
        layout.addWidget(QLabel('Version'), 2, 2)
        layout.addWidget(self.version, 2, 3)
        page.knowledge_name.currentTextChanged.connect(self.refresh_types)
        self.document_type.currentTextChanged.connect(self.refresh_versions)
        self.version.currentTextChanged.connect(self.invalidate_workspace)
        self.refresh_types()

    def invalidate_workspace(self, *_):
        # Rows loaded for the previous owner cannot remain actionable after a
        # parent/type/version selection changes.
        if hasattr(self.page, 'test_cases'):
            self.page.test_cases = []
        table=getattr(self.page, 'table', None)
        if table is not None:
            table.setRowCount(0)

    def rows(self):
        connection = self.page.metadata.db.get_connection()
        try:
            return DocumentHierarchy(connection).catalog(
                domain=self.page.domain.currentText(), module=self.page.module.currentText(),
                knowledge_name=self.page.knowledge_name.currentText())
        finally:
            connection.close()

    def refresh_types(self, *_):
        self.document_type.blockSignals(True)
        self.document_type.clear()
        if all(control.currentText() for control in (self.page.domain, self.page.module, self.page.knowledge_name)):
            self.document_type.addItems(sorted({row['document_type'] for row in self.rows() if row['document_type']}))
        self.document_type.setEnabled(bool(self.document_type.count()))
        self.document_type.blockSignals(False)
        self.refresh_versions()

    def refresh_versions(self, *_):
        self.version.clear()
        if self.document_type.currentText():
            for row in self.rows():
                if row['document_type'] == self.document_type.currentText() and row['document_version_id']:
                    self.version.addItem(row['version'], row['document_version_id'])
        self.version.setEnabled(bool(self.version.count()))

    def filter_cases(self, cases):
        owner = self.version.currentData()
        return [case for case in cases if owner is not None and case.get('document_version_id') == owner]
