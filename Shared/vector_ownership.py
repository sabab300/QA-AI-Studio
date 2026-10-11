"""Resolve asset embeddings through persisted ownership, not content hashes."""
import sqlite3
from Shared.document_hierarchy import DocumentHierarchy, HierarchyConflict
from Shared.hierarchy_values import canonical_source_type


def resolve_source_vector_owner(sources, vector_id, metadata):
    """Stable source ID first; legacy vectors require exact labels AND hash."""
    sources=list(sources.values()) if isinstance(sources,dict) else list(sources)
    metadata=metadata or {}
    identifier=metadata.get('knowledge_id')
    if identifier not in (None,''):
        matches=[source for source in sources if str(source['id'])==str(identifier)]
    else:
        keys=('domain','module','knowledge_name','document_type','version','file_name')
        matches=[source for source in sources if source.get('sha256') and source['sha256'] in str(vector_id)
            and all(str(metadata.get(key) or '')==str(source.get(key) or '') for key in keys)]
    return matches[0] if len(matches)==1 else None


def api_collection_owner(database,collection_id):
    connection=database.get_connection();connection.row_factory=sqlite3.Row
    try:
        row=connection.execute('SELECT * FROM api_collections WHERE id=?',(int(collection_id),)).fetchone()
        if row is None:return None
        collection=dict(row);owners=set()
        if collection.get('linked_knowledge_item_id'):owners.add(int(collection['linked_knowledge_item_id']))
        if collection.get('test_case_version_id'):
            version=connection.execute('SELECT source_knowledge_id FROM test_case_versions WHERE id=?',(collection['test_case_version_id'],)).fetchone()
            if not version:raise HierarchyConflict('API collection references an absent Test Cases version.')
            owners.add(int(version[0]))
        if not owners:return None
        if len(owners)!=1:raise HierarchyConflict('API collection source and Test Cases version owners conflict.')
        source_id=next(iter(owners))
        source=connection.execute('SELECT * FROM knowledge_items WHERE id=?',(source_id,)).fetchone()
        if source is None:raise HierarchyConflict('API collection source owner is absent.')
        source=dict(source)
        if source.get('document_version_id') is None:raise HierarchyConflict('API collection source requires canonical ownership migration.')
        factory=connection.row_factory;connection.row_factory=None
        try:scope=DocumentHierarchy(connection).context(source['document_version_id'])
        finally:connection.row_factory=factory
        return {key:scope[key] for key in ('domain','module','knowledge_name','document_type','version','knowledge_entity_id','document_type_id','document_version_id')}|{
            'knowledge_id':source_id,'source_type':canonical_source_type(source.get('source_type')),
            'asset_type':'API Collection','asset_collection_id':int(collection_id),
            'asset_test_case_version_id':collection.get('test_case_version_id') or 0}
    finally:connection.close()
