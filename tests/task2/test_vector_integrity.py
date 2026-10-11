"""Real disposable Chroma collections, never production vector storage."""
import importlib.util
import logging
from pathlib import Path
import tempfile
import unittest
import sys
import chromadb
from chromadb.config import Settings
from Shared.vector_integrity import VectorIntegrity

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'AI-Web'),str(ROOT)]

class IntegrityTests(unittest.TestCase):
    def test_exact_metadata_document_embedding_restore_both_stores(self):
        spec=importlib.util.spec_from_file_location('web_vector_integrity',ROOT/'AI-Web/Core/vector_store.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory(prefix='task2-chroma-') as directory:
            client=chromadb.PersistentClient(path=directory,settings=Settings(anonymized_telemetry=False))
            for kind in (module.VectorStore,VectorIntegrity):
                collection=client.create_collection('test-'+kind.__name__.lower())
                collection.add(ids=['source-one'],documents=['fixture content'],embeddings=[[0.1,0.2,0.3]],metadatas=[{'knowledge_id':1,'document_version_id':2,'domain':'Before'}])
                store=kind.__new__(kind);store.collection=collection;store.logger=logging.getLogger('task2-vector-test')
                if kind is VectorIntegrity:store._prepare_metadata=lambda value:value
                original=store.snapshot(['source-one'])
                store.update_metadata(['source-one'],{'domain':'After','new_field':'must disappear'})
                store.restore_metadata(original)
                restored=store.snapshot(['source-one'])
                self.assertEqual(restored['metadatas'],original['metadatas']);self.assertEqual(restored['documents'],original['documents'])
                self.assertEqual(restored['embeddings'].tolist(),original['embeddings'].tolist())
                removed=store.delete_with_snapshot(['source-one']);self.assertEqual(collection.count(),0)
                store.restore_snapshot(removed);self.assertEqual(collection.count(),1)
                client.delete_collection(collection.name)
            # Release SQLite handles before Windows temporary-directory cleanup.
            client._system.stop()

if __name__=='__main__':unittest.main()
