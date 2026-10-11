"""Real metadata service, disposable database, independent DT versions."""
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'AI-Web'),str(ROOT)]
from Database.schema import ALL_TABLES
from Database.db_manager import DatabaseManager
from Core.metadata_manager import MetadataManager
from Shared.document_hierarchy import DocumentHierarchy


class DB:
    def __init__(self,path): self.path=path;self.connections=[]
    def get_connection(self):
        conn=sqlite3.connect(self.path);conn.execute('PRAGMA foreign_keys=ON');self.connections.append(conn);return conn


class MetadataOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='task2-metadata-')
        self.db=DB(Path(self.temp.name)/'metadata.db')
        initializer=DatabaseManager.__new__(DatabaseManager)
        initializer.db_path=self.db.path
        initializer.initialize_database()
        self.service=MetadataManager.__new__(MetadataManager);self.service.db=self.db
    def tearDown(self):
        for conn in self.db.connections: conn.close()
        self.temp.cleanup()
    def save(self,kind,version='1.0'):
        file=Path(self.temp.name)/(kind+'-'+version+'.txt');file.write_text('Disposable hierarchy fixture',encoding='utf-8')
        return self.service.save_knowledge_item('Domain','Module','Knowledge',version,
            {'file_name':file.name,'repository_path':str(file),'sha256':kind+version,'file_size':file.stat().st_size,'extension':'.txt'},
            {'document_type':kind})
    def test_independent_types_and_fk(self):
        first=self.save('SRS');second=self.save('CRF')
        conn=self.db.get_connection()
        sources=conn.execute('SELECT id,document_version_id,knowledge_path FROM knowledge_items ORDER BY id').fetchall()
        self.assertEqual([r[0] for r in sources],[first,second])
        self.assertNotEqual(sources[0][1],sources[1][1])
        self.assertIn('/SRS/1.0',sources[0][2]);self.assertIn('/CRF/1.0',sources[1][2])
        self.assertFalse(conn.execute('PRAGMA foreign_key_check').fetchall());conn.close()
    def test_source_update_rebinds_without_changing_id(self):
        source=self.save('SRS')
        self.assertTrue(self.service.update_knowledge_item(source,version='2.0'))
        conn=self.db.get_connection()
        row=conn.execute('SELECT id,document_version_id,version FROM knowledge_items WHERE id=?',(source,)).fetchone()
        self.assertEqual(row[0],source);self.assertEqual(row[2],'2.0')
        self.assertEqual(DocumentHierarchy(conn).context(row[1])['document_type'],'SRS')
        conn.close()


if __name__=='__main__': unittest.main()
