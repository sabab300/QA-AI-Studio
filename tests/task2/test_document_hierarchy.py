"""Canonical ownership tests use SQLite memory, never the active runtime."""
from pathlib import Path
import sqlite3
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from Shared.document_hierarchy import DocumentHierarchy, HierarchyConflict


class OwnershipTests(unittest.TestCase):
    def setUp(self):
        self.connection=sqlite3.connect(':memory:');self.connection.execute('PRAGMA foreign_keys=ON')
        self.connection.executescript('CREATE TABLE domains(id INTEGER PRIMARY KEY,name TEXT);CREATE TABLE modules(id INTEGER PRIMARY KEY,domain_id INTEGER REFERENCES domains(id),name TEXT);INSERT INTO domains VALUES(1,\'Domain\');INSERT INTO modules VALUES(1,1,\'Module\');')
        self.hierarchy=DocumentHierarchy(self.connection);self.hierarchy.ensure_schema()
    def tearDown(self): self.connection.close()
    def test_independent_document_versions(self):
        first=self.hierarchy.ensure_context(1,1,'Knowledge','SRS','1.0')
        second=self.hierarchy.ensure_context(1,1,'Knowledge','CRF','1.0')
        self.assertNotEqual(first['document_version_id'],second['document_version_id'])
        self.assertNotEqual(first['document_type_id'],second['document_type_id'])
        self.assertEqual(self.hierarchy.ensure_context(1,1,'Knowledge','SRS','1.0'),first)
        self.hierarchy.ensure_context(1,1,'Knowledge','CRF','2.0')
        self.assertEqual(len(self.hierarchy.versions(first['document_type_id'])),1)
        self.assertEqual(len(self.hierarchy.versions(second['document_type_id'])),2)
    def test_wrong_parent_rejected(self):
        with self.assertRaises(HierarchyConflict): self.hierarchy.ensure_context(2,1,'Knowledge','SRS','1.0')
    def test_legacy_source_dictionary_factory_and_conflict(self):
        self.connection.execute('''CREATE TABLE knowledge_items(id INTEGER PRIMARY KEY,
            domain_id INTEGER,module_id INTEGER,domain TEXT,module TEXT,
            knowledge_name TEXT,document_type TEXT,version TEXT)''')
        self.connection.execute("INSERT INTO knowledge_items VALUES(7,1,1,'Domain','Module','Knowledge','CRF','1.0')")
        factory=lambda cursor,row: {column[0]:row[index] for index,column in enumerate(cursor.description)}
        self.connection.row_factory=factory
        owner=self.hierarchy.resolve_source(7)
        self.assertEqual(owner['document_type'],'CRF')
        self.assertIs(self.connection.row_factory,factory)
        self.assertEqual(self.hierarchy.resolve_source(7),owner)
        with self.assertRaisesRegex(sqlite3.IntegrityError,'ownership conflict'):
            self.connection.execute("UPDATE knowledge_items SET document_type='SRS' WHERE id=7")
        self.assertEqual(self.hierarchy.resolve_source(7),owner)
        self.assertIs(self.connection.row_factory,factory)
    def test_move_preserves_identity_and_refuses_merge(self):
        first=self.hierarchy.ensure_context(1,1,'First','SRS','1.0')
        second=self.hierarchy.ensure_context(1,1,'Second','CRF','2.0')
        self.hierarchy.move('version',first['document_version_id'],second['document_type_id'])
        self.assertEqual(self.hierarchy.context(first['document_version_id'])['document_type_id'],second['document_type_id'])
        other=self.hierarchy.ensure_context(1,1,'First','SOP','2.0')
        with self.assertRaises(HierarchyConflict): self.hierarchy.move('version',other['document_version_id'],second['document_type_id'])
        self.assertEqual(self.hierarchy.context(other['document_version_id'])['document_type_id'],other['document_type_id'])


if __name__=='__main__': unittest.main()
