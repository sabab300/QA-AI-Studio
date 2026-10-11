"""Actual revision/import service, disposable SQLite and managed repository."""
import importlib.util
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('version_fixture',ROOT/'AI-Web/dev-tools-not-for-production/test_qa_engineering_version_sync.py')
fixture=importlib.util.module_from_spec(spec);spec.loader.exec_module(fixture)


class VersionOwnershipTests(unittest.TestCase):
    def test_document_type_isolation_revision_and_rollback(self):
        with tempfile.TemporaryDirectory(prefix='task2-version-owner-') as directory:
            root=Path(directory);db=fixture.TempDatabase(root/'metadata.db')
            conn=db.get_connection()
            for sql in fixture.ALL_TABLES:conn.execute(sql)
            for sql in fixture.KNOWLEDGE_ITEMS_MIGRATION:
                try:conn.execute(sql)
                except sqlite3.OperationalError:pass
            conn.execute("INSERT INTO domains(id,name) VALUES(1,'Domain')")
            conn.execute("INSERT INTO modules(id,domain_id,name) VALUES(1,1,'Module')")
            conn.commit();conn.close()
            repo=fixture.TestCaseRepository.__new__(fixture.TestCaseRepository)
            repo.db=db;repo.logger=Mock();repo._resolve_ids=lambda domain,module:(1,1);repo._migrate_legacy_desktop_rows=lambda cursor:None;repo.ensure_schema()
            central=fixture.CentralRepository(root/'repository');sources=[]
            conn=db.get_connection()
            for dtype in ['SRS','CRF']:
                folder=central.build_source_path(domain='Domain',module='Module',knowledge_name='Knowledge',document_type=dtype,version='1.0',source_type='Files',source_file_id='SD-'+dtype)
                folder.mkdir(parents=True);file=folder/'requirement.txt';file.write_text(dtype)
                cursor=conn.execute('''INSERT INTO knowledge_items(domain,module,knowledge_name,document_type,version,
                    domain_id,module_id,knowledge_path,file_name,repository_path,repository_relative_path,source_type,source_file_id,sha256)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',('Domain','Module','Knowledge',dtype,'1.0',1,1,central.relative(folder),file.name,str(file),central.relative(file),'Files','SD-'+dtype,dtype))
                sources.append(cursor.lastrowid)
            conn.commit();conn.close()
            service=fixture.QaEngineeringVersionService.__new__(fixture.QaEngineeringVersionService)
            service.db=db;service.repository=repo;service.central=central
            created=[service.create_generated_set(source,[fixture.row('Case')]) for source in sources]
            conn=db.get_connection()
            owners=conn.execute('SELECT document_version_id FROM test_case_versions ORDER BY id').fetchall()
            self.assertNotEqual(owners[0][0],owners[1][0])
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM test_cases t JOIN test_case_versions v ON v.id=t.test_case_version_id WHERE t.document_version_id<>v.document_version_id OR t.document_version_id IS NULL').fetchone()[0],0)
            conn.close()
            loaded=service.load_version(created[0]['test_case_version_id'],created[0]['test_case_file_id'])
            number=loaded['test_cases'][0]['tc_number']
            revision=service.create_revision(created[0]['test_case_version_id'],created[0]['test_case_file_id'],[])
            copied=service.load_version(revision['test_case_version_id'],revision['test_case_file_id'])
            self.assertEqual(copied['test_cases'][0]['tc_number'],number)
            self.assertEqual(copied['version']['document_version_id'],owners[0][0])
            workbook=fixture.workbook_bytes([dict(fixture.row('Import'),tc_number='TC001')])
            imported=service.import_set(sources[1],workbook,'import.xlsx')
            imported2=service.import_set(sources[1],workbook,'import.xlsx')
            self.assertNotEqual(imported['test_case_version_id'],imported2['test_case_version_id'])
            direct=repo.save_generated_cases('Domain','Module','Knowledge','1.0',[fixture.row('Direct')],strict=True,
                source_knowledge_ids=[sources[1]],document_type='CRF',force_direct_insert=True)
            conn=db.get_connection()
            self.assertEqual(conn.execute('SELECT document_version_id FROM test_cases WHERE id=?',(direct[0],)).fetchone()[0],owners[1][0])
            conn.close()
            with self.assertRaises(ValueError):repo.save_generated_cases('Domain','Module','Knowledge','1.0',[fixture.row('Wrong owner')],strict=True,
                source_knowledge_ids=sources,document_type='',force_direct_insert=True)
            conn=db.get_connection();before=conn.execute('SELECT COUNT(*) FROM test_case_versions').fetchone()[0];conn.close()
            folders=sorted(central.relative(p) for p in central.root.rglob('*'))
            duplicate=fixture.workbook_bytes([dict(fixture.row('A'),tc_number='TC002'),dict(fixture.row('B'),tc_number='TC002')])
            with self.assertRaises(ValueError):service.import_set(sources[1],duplicate,'duplicate.xlsx')
            conn=db.get_connection()
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM test_case_versions').fetchone()[0],before)
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[]);conn.close()
            self.assertEqual(sorted(central.relative(p) for p in central.root.rglob('*')),folders)

if __name__=='__main__':unittest.main()
