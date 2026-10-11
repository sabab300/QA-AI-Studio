"""Actual Desktop service methods, temporary SQLite, no LLM/network execution."""
import importlib.util
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

ROOT=Path(__file__).resolve().parents[2];sys.path[:0]=[str(ROOT/'AI-Web'),str(ROOT)]

def load(name,path):
    spec=importlib.util.spec_from_file_location(name,ROOT/path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class DesktopAutomationOwnershipTests(unittest.TestCase):
    def test_real_collection_query_excludes_other_document_versions(self):
        module=load('desktop_api_owner','AI/Core/api_collection_repository.py')
        with tempfile.TemporaryDirectory(prefix='task2-api-owner-') as directory:
            path=Path(directory)/'metadata.db';conn=sqlite3.connect(path)
            conn.executescript("CREATE TABLE api_collections(id INTEGER PRIMARY KEY,domain TEXT,module TEXT,knowledge_name TEXT,status TEXT,document_version_id INTEGER);CREATE TABLE api_endpoints(id INTEGER PRIMARY KEY,collection_id INTEGER,status TEXT);INSERT INTO api_collections VALUES(1,'D','M','K','Active',10),(2,'D','M','K','Active',20);INSERT INTO api_endpoints VALUES(1,1,'Active'),(2,2,'Active');")
            conn.close();repository=module.ApiCollectionRepository.__new__(module.ApiCollectionRepository)
            repository.db=SimpleNamespace(get_connection=lambda:sqlite3.connect(path))
            self.assertEqual([row['id'] for row in repository.get_endpoints_for_scope('D','M','K',document_version_id=10)],[1])
            self.assertEqual([row['id'] for row in repository.get_endpoints_for_scope('D','M','K',document_version_id=20)],[2])

    def test_automation_generation_uses_persisted_case_owner_for_rag(self):
        module=load('desktop_execution_owner','AI/Core/test_execution_manager.py')
        manager=module.TestExecutionManager.__new__(module.TestExecutionManager)
        manager.repository=Mock();manager.repository.get_test_case.return_value={'id':1,'version':'2.0','document_type':'CRF','document_version_id':42}
        manager._build_api_requirement=Mock(return_value='fixture requirement')
        manager.automation_generator=Mock();manager.automation_generator.generate.return_value={'success':False,'error':'fixture stopped before LLM execution'}
        with self.assertRaisesRegex(RuntimeError,'fixture stopped'):
            manager.generate_automation(1,'API','D','M','K',version='1.0')
        arguments=manager.automation_generator.generate.call_args.kwargs
        self.assertEqual((arguments['document_type'],arguments['version'],arguments['document_version_id']),('CRF','2.0',42))

if __name__=='__main__':unittest.main()
