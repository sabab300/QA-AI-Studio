"""Exact rollback helpers shared by Desktop/Web Chroma stores."""
class VectorIntegrity:
    def snapshot(self,ids):
        ids=[str(value) for value in ids if value]
        return self.collection.get(ids=ids,include=['documents','embeddings','metadatas']) if ids else {'ids':[],'documents':[],'embeddings':[],'metadatas':[]}
    def update_metadata(self,ids,updates):
        snapshot=self.snapshot(ids)
        if snapshot['ids']:
            self.collection.update(ids=snapshot['ids'],metadatas=[self._prepare_metadata(dict(value or {})|updates) for value in snapshot['metadatas']])
        return snapshot
    def restore_snapshot(self,snapshot):
        if snapshot and snapshot.get('ids'):
            self.collection.add(ids=snapshot['ids'],documents=snapshot['documents'],embeddings=snapshot['embeddings'],metadatas=snapshot['metadatas'])
        return len(snapshot.get('ids',[])) if snapshot else 0
    def restore_metadata(self,snapshot):
        if snapshot and snapshot.get('ids'):
            self.collection.delete(ids=snapshot['ids']);self.restore_snapshot(snapshot)
        return len(snapshot.get('ids',[])) if snapshot else 0
    def delete_with_snapshot(self,ids):
        snapshot=self.snapshot(ids)
        if snapshot['ids']:self.collection.delete(ids=snapshot['ids'])
        return snapshot
