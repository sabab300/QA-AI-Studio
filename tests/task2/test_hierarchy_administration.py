"""Real canonical admin transactions/files with injected vector failure."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from copy import deepcopy
from types import SimpleNamespace

from test_version_ownership import fixture
from Shared.document_hierarchy import DocumentHierarchy, HierarchyConflict
from Shared.hierarchy_administration import HierarchyAdministration


class Vectors:
    def __init__(self):
        self.data={};self.calls=0;self.fail_after=None;self.fail_delete=False
        self.collection=SimpleNamespace(get=lambda **kwargs:{'ids':list(self.data),'metadatas':list(self.data.values())})
    def snapshot(self,ids):return {'ids':ids,'metadatas':[deepcopy(self.data.get(key,{})) for key in ids]}
    def update_metadata(self,ids,updates):
        self.calls+=1
        for key in ids:self.data.setdefault(key,{}).update(updates)
        if self.calls==self.fail_after:raise RuntimeError('injected vector update failure')
    def restore_metadata(self,snapshot):
        for key,value in zip(snapshot['ids'],snapshot['metadatas']):self.data[key]=deepcopy(value)
    def restore_snapshot(self,snapshot):self.restore_metadata(snapshot)
    def delete_with_snapshot(self,ids):
        snapshot=self.snapshot(ids)
        for key in ids:self.data.pop(key,None)
        if self.fail_delete:raise RuntimeError('injected vector delete failure')
        return snapshot


class AdminTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='task2-admin-');self.root=Path(self.temp.name)
        self.db=fixture.TempDatabase(self.root/'metadata.db');self.central=fixture.CentralRepository(self.root/'repository')
        conn=self.db.get_connection()
        for sql in fixture.ALL_TABLES:conn.execute(sql)
        for sql in fixture.KNOWLEDGE_ITEMS_MIGRATION:
            try:conn.execute(sql)
            except sqlite3.OperationalError:pass
        conn.execute("INSERT INTO domains(id,name) VALUES(1,'Domain')")
        conn.execute("INSERT INTO modules(id,domain_id,name) VALUES(1,1,'Module'),(2,1,'Destination')")
        self.sources=[]
        for dtype in ('SRS','CRF'):
            folder=self.central.build_source_path(domain='Domain',module='Module',knowledge_name='Knowledge',document_type=dtype,version='1.0',source_type='Files',source_file_id=dtype+'-V1')
            folder.mkdir(parents=True);file=folder/'requirement.txt';file.write_text(dtype)
            cursor=conn.execute('''INSERT INTO knowledge_items(domain,module,knowledge_name,document_type,version,domain_id,module_id,
                knowledge_path,file_name,repository_path,repository_relative_path,source_type,source_file_id,sha256)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',('Domain','Module','Knowledge',dtype,'1.0',1,1,'old',file.name,str(file),self.central.relative(file),'Files',dtype+'-V1',dtype))
            self.sources.append(cursor.lastrowid)
        hierarchy=DocumentHierarchy(conn);hierarchy.migrate_sources()
        self.owner_ids=[row[0] for row in conn.execute('SELECT document_version_id FROM knowledge_items ORDER BY id')]
        self.node_id=conn.execute("SELECT id FROM knowledge_entities WHERE name='Knowledge'").fetchone()[0]
        conn.commit();conn.close()
        self.vectors=Vectors();self.admin=HierarchyAdministration(self.db,self.central,self.vectors,lambda row:[str(row['id'])])
        self.node={'type':'knowledge','name':'Knowledge','context':{'domain':'Domain','module':'Module','knowledge_name':'Knowledge'}}

    def tearDown(self):self.temp.cleanup()
    def paths(self):
        conn=self.db.get_connection();rows=conn.execute('SELECT id,repository_path,repository_relative_path,document_version_id FROM knowledge_items ORDER BY id').fetchall();conn.close();return rows

    def test_move_preserves_node_source_version_ids_and_file_contents(self):
        preview=self.admin.change(self.node,destination={'domain':'Domain','module':'Destination'},validate_only=True)
        self.assertEqual(preview['would_move'],2)
        self.assertTrue(all(Path(row[1]).is_file() for row in self.paths()))
        result=self.admin.change(self.node,destination={'domain':'Domain','module':'Destination'})
        self.assertEqual(result['canonical_node_id'],self.node_id)
        self.assertEqual([row[3] for row in self.paths()],self.owner_ids)
        for row in self.paths():
            self.assertTrue(Path(row[1]).is_file());self.assertIn('M-Destination',row[2]);self.assertIn('DT-',row[2]);self.assertLess(row[2].index('DT-'),row[2].index('V-1.0'))
        conn=self.db.get_connection();self.assertEqual(conn.execute('SELECT module_id FROM knowledge_entities WHERE id=?',(self.node_id,)).fetchone()[0],2)
        self.assertEqual(conn.execute('PRAGMA foreign_key_check').fetchall(),[]);conn.close()

    def test_collision_and_dependency_are_controlled_and_leave_state_unchanged(self):
        before=self.paths()
        node={'type':'document_type','name':'SRS','context':dict(self.node['context'],document_type='SRS')}
        with self.assertRaises(HierarchyConflict):self.admin.change(node,value='CRF')
        self.assertEqual(self.paths(),before)
        with self.assertRaises(HierarchyConflict):self.admin.delete_empty(self.node)
        self.assertEqual(self.paths(),before)

    def test_vector_failure_rolls_back_whole_subtree_and_added_metadata(self):
        before=self.paths();self.vectors.data={str(row[0]):{'original':row[0]} for row in before}
        metadata=deepcopy(self.vectors.data);self.vectors.fail_after=2
        with self.assertRaisesRegex(RuntimeError,'injected'):self.admin.change(self.node,value='Renamed')
        self.assertEqual(self.paths(),before);self.assertEqual(self.vectors.data,metadata)
        self.assertTrue(all(Path(row[1]).is_file() for row in before))
        conn=self.db.get_connection();self.assertEqual(conn.execute('SELECT name FROM knowledge_entities WHERE id=?',(self.node_id,)).fetchone()[0],'Knowledge');conn.close()

    def test_missing_legacy_path_is_conflict_not_file_not_found(self):
        before=self.paths();Path(before[0][1]).unlink()
        with self.assertRaisesRegex(HierarchyConflict,'could not be resolved'):self.admin.change(self.node,value='Renamed')
        self.assertEqual(self.paths(),before)

    def test_delete_empty_canonical_node(self):
        conn=self.db.get_connection();hierarchy=DocumentHierarchy(conn)
        hierarchy.create_node('knowledge',{'domain':'Domain','module':'Module'},'Empty');conn.commit();conn.close()
        self.assertEqual(self.admin.delete_empty({'type':'knowledge','name':'Empty','context':{'domain':'Domain','module':'Module','knowledge_name':'Empty'}}),{'deleted':1})

    def test_post_commit_callback_failure_never_reverts_committed_files(self):
        def fail():raise RuntimeError('cache callback failure')
        self.admin.on_change=fail
        with self.assertRaisesRegex(RuntimeError,'cache callback'):self.admin.change(self.node,value='Renamed')
        for row in self.paths():
            self.assertTrue(Path(row[1]).is_file());self.assertIn('K-Renamed',row[2])
        conn=self.db.get_connection()
        self.assertEqual(conn.execute('SELECT name FROM knowledge_entities WHERE id=?',(self.node_id,)).fetchone()[0],'Renamed')
        conn.close()

    def test_source_move_and_reviewed_metadata_commit_together(self):
        source=self.sources[0]
        context=dict(self.node['context'],document_type='SRS',version='1.0',source_type='Files',source_file_id='SRS-V1')
        node={'type':'source_file','name':'SRS-V1','context':context}
        destination=dict(context,document_type='CRF')
        self.admin.change(node,destination=destination,item_updates={source:{'summary':'Reviewed','tags':['one','two']}})
        conn=self.db.get_connection();row=conn.execute('SELECT document_type,summary,tags,repository_path FROM knowledge_items WHERE id=?',(source,)).fetchone();conn.close()
        self.assertEqual(row[:3],('CRF','Reviewed','["one", "two"]'));self.assertTrue(Path(row[3]).is_file())
        self.assertEqual(self.vectors.data[str(source)]['summary'],'Reviewed')

    def test_running_owner_blocks_changes_without_side_effects(self):
        before=self.paths();conn=self.db.get_connection()
        conn.execute('CREATE TABLE qa_generation_jobs(job_id TEXT,document_version_id INTEGER,status TEXT)')
        conn.execute("INSERT INTO qa_generation_jobs VALUES('fixture',?,'running')",(self.owner_ids[0],));conn.commit();conn.close()
        with self.assertRaisesRegex(HierarchyConflict,'active generation'):self.admin.change(self.node,value='Renamed')
        self.assertEqual(self.paths(),before);self.assertTrue(all(Path(row[1]).is_file() for row in before))

    def test_shared_source_delete_preserves_sibling_source(self):
        from Shared.source_administration import delete_source
        before=self.paths();source=self.sources[0];self.vectors.data={str(source):{'knowledge_id':source}}
        result=delete_source(self.db,self.central,self.vectors,source)
        self.assertEqual(result['knowledge'],1);self.assertEqual(result['vectors_removed'],1)
        self.assertFalse(Path(before[0][1]).exists());self.assertTrue(Path(before[1][1]).is_file())
        self.assertEqual([row[0] for row in self.paths()],[self.sources[1]])

    def test_source_delete_mutation_failure_restores_file_vectors_and_database(self):
        from Shared.source_administration import delete_source
        before=self.paths();source=self.sources[0];self.vectors.data={str(source):{'knowledge_id':source}};self.vectors.fail_delete=True
        with self.assertRaisesRegex(RuntimeError,'vector delete'):delete_source(self.db,self.central,self.vectors,source)
        self.assertEqual(self.paths(),before);self.assertTrue(Path(before[0][1]).is_file());self.assertEqual(self.vectors.data,{str(source):{'knowledge_id':source}})

if __name__=='__main__':unittest.main()
