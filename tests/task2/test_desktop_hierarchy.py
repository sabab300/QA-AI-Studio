"""Desktop backend + real offscreen Qt controls, disposable data only."""
import ast
import importlib.util
from pathlib import Path
import os
import sqlite3
import tempfile
from types import SimpleNamespace,MethodType
import unittest
from unittest.mock import Mock

from test_version_ownership import fixture
ROOT=Path(__file__).resolve().parents[2]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtWidgets import QApplication,QComboBox


class DesktopTests(unittest.TestCase):
    def test_actual_automation_document_scope_controls(self):
        application=QApplication.instance() or QApplication([])
        spec=importlib.util.spec_from_file_location('desktop_document_scope',ROOT/'AI/App/UI/QAAutomation/document_scope.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        from PySide6.QtWidgets import QGridLayout,QWidget,QTableWidget
        with tempfile.TemporaryDirectory(prefix='task2-qt-scope-') as directory:
            db=fixture.TempDatabase(Path(directory)/'metadata.db');conn=db.get_connection()
            conn.execute('CREATE TABLE domains(id INTEGER PRIMARY KEY,name TEXT)');conn.execute('CREATE TABLE modules(id INTEGER PRIMARY KEY,domain_id INTEGER,name TEXT)')
            conn.execute("INSERT INTO domains VALUES(1,'D')");conn.execute("INSERT INTO modules VALUES(1,1,'M')")
            from Shared.document_hierarchy import DocumentHierarchy
            hierarchy=DocumentHierarchy(conn);hierarchy.ensure_schema()
            for dtype,version in [('SRS','1.0'),('SRS','2.0'),('CRF','1.0'),('CRF','1.1')]:hierarchy.ensure_context(1,1,'K',dtype,version)
            conn.commit();conn.close()
            widget=QWidget();layout=QGridLayout(widget)
            page=SimpleNamespace(metadata=SimpleNamespace(db=db),domain=QComboBox(),module=QComboBox(),knowledge_name=QComboBox())
            page.domain.addItem('D');page.module.addItem('M');page.knowledge_name.addItem('K')
            scope=module.DocumentScope(page,layout);scope.document_type.setCurrentText('CRF')
            self.assertEqual([scope.version.itemText(i) for i in range(scope.version.count())],['1.0','1.1'])
            owner=scope.version.currentData();self.assertEqual(scope.filter_cases([{'document_version_id':owner},{'document_version_id':999}]),[{'document_version_id':owner}])
            page.test_cases=[{'document_version_id':owner}];page.table=QTableWidget();page.table.setRowCount(1)
            scope.document_type.setCurrentText('SRS')
            self.assertEqual(page.test_cases,[]);self.assertEqual(page.table.rowCount(),0)
            page.knowledge_name.clear();self.assertFalse(scope.version.isEnabled());self.assertEqual(scope.filter_cases([{'document_version_id':owner}]),[])
            widget.deleteLater();page.table.deleteLater();application.processEvents()

    def test_real_qt_document_type_version_controls(self):
        application=QApplication.instance() or QApplication([])
        path=ROOT/'AI/App/UI/QAEngineering/test_case_generation_page.py'
        tree=ast.parse(path.read_text(encoding='utf8'))
        selected=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.FunctionDef) and node.name in ('_selected_sources','on_knowledge_changed','on_document_type_changed'):
                selected.append(node)
        namespace={};exec(compile(ast.Module(body=selected,type_ignores=[]),str(path),'exec'),namespace)
        controls={key:QComboBox() for key in ('domain','module','knowledge_name','document_type','version')}
        controls['domain'].addItem('Domain');controls['module'].addItem('Module');controls['knowledge_name'].addItem('Knowledge')
        sources=[{'domain':'Domain','module':'Module','knowledge_name':'Knowledge','document_type':dtype,'version':version}
            for dtype,version in [('SRS','1.0'),('SRS','2.0'),('CRF','1.0'),('CRF','1.1')]]
        page=SimpleNamespace(**controls,metadata=SimpleNamespace(list_all=lambda:sources))
        for name,function in namespace.items():
            if callable(function):setattr(page,name,MethodType(function,page))
        page.on_knowledge_changed();page.document_type.setCurrentText('CRF');page.on_document_type_changed()
        self.assertEqual([page.version.itemText(index) for index in range(page.version.count())],['1.0','1.1'])
        page.document_type.setCurrentText('SRS');page.on_document_type_changed()
        self.assertEqual([page.version.itemText(index) for index in range(page.version.count())],['1.0','2.0'])
        for control in controls.values():control.deleteLater()
        application.processEvents()

    def test_desktop_test_case_save_has_canonical_document_owner(self):
        spec=importlib.util.spec_from_file_location('desktop_case_repo',ROOT/'AI/Core/test_case_repository.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='task2-desktop-') as directory:
            db=fixture.TempDatabase(Path(directory)/'metadata.db');conn=db.get_connection()
            for statement in fixture.ALL_TABLES:conn.execute(statement)
            for statement in fixture.KNOWLEDGE_ITEMS_MIGRATION:
                try:conn.execute(statement)
                except sqlite3.OperationalError:pass
            conn.execute("INSERT INTO domains(id,name) VALUES(1,'Domain')")
            conn.execute("INSERT INTO modules(id,domain_id,name) VALUES(1,1,'Module')")
            for dtype in ('SRS','CRF'):
                conn.execute('''INSERT INTO knowledge_items(domain,module,knowledge_name,document_type,version,domain_id,module_id,
                    file_name,repository_path,sha256,source_type,knowledge_path) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)''',
                    ('Domain','Module','Knowledge',dtype,'1.0',1,1,dtype+'.txt','fixture/'+dtype+'.txt',dtype,'Files','fixture'))
            conn.commit();conn.close()
            repository=module.TestCaseRepository.__new__(module.TestCaseRepository)
            repository.db=db;repository.logger=Mock();repository._resolve_ids=lambda domain,module:(1,1);repository.ensure_schema()
            # Canonical repository is the actual Desktop bridge target. Isolate
            # JSON artifacts without substituting its DB/source ownership logic.
            repository._write_artifact=lambda values,sources:Path(directory)/('case-'+str(values['id'])+'.json')
            first=repository.save_generated_cases('Domain','Module','Knowledge','1.0',[fixture.row('SRS')],document_type='SRS')
            second=repository.save_generated_cases('Domain','Module','Knowledge','1.0',[fixture.row('CRF')],document_type='CRF')
            conn=db.get_connection();owners=conn.execute('SELECT document_type,document_version_id FROM test_cases ORDER BY id').fetchall()
            self.assertEqual(len(first+second),2);self.assertNotEqual(owners[0][1],owners[1][1])
            self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[]);conn.close()

if __name__=='__main__':unittest.main()
