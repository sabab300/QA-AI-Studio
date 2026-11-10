"""Delete one persisted source, never its entire knowledge-name folder."""
from pathlib import Path
import sqlite3
import uuid


def source_vector_ids(store,item):
    data=store.collection.get(include=['metadatas']);ids=[]
    for identifier,metadata in zip(data.get('ids',[]),data.get('metadatas',[])):
        metadata=metadata or {}
        if str(metadata.get('knowledge_id') or '')==str(item['id']):ids.append(str(identifier));continue
        if metadata.get('knowledge_id') not in (None,''):continue
        if all(str(metadata.get(key) or '')==str(item.get(key) or '') for key in ('domain','module','knowledge_name','document_type','version','file_name')) and item.get('sha256') and item['sha256'] in str(identifier):
            ids.append(str(identifier))
    return ids


def delete_source(database,central,store,source_id):
    connection=database.get_connection();connection.row_factory=sqlite3.Row
    staged=None;snapshot=None;path=None;summary={'knowledge':1,'versions':0,'queue':0,'tags':0,'api_links_cleared':0,'discovery_links_cleared':0}
    try:
        connection.execute('BEGIN IMMEDIATE')
        row=connection.execute('SELECT * FROM knowledge_items WHERE id=?',(int(source_id),)).fetchone()
        if row is None:raise ValueError('Selected source was not found.')
        item=dict(row);tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ('qa_generation_jobs','automation_runs'):
            columns={row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
            if item.get('document_version_id') and {'status','document_version_id'}<=columns and connection.execute('SELECT 1 FROM '+table+" WHERE document_version_id=? AND lower(status) IN ('queued','running') LIMIT 1",(item['document_version_id'],)).fetchone():
                raise ValueError('An active generation or automation run owns this source hierarchy. Wait for it to finish before deleting the source.')
        for table,column in [('test_case_versions','source_knowledge_id'),('test_case_sources','knowledge_source_id'),('api_collections','linked_knowledge_item_id'),('api_discovery_collections','source_id')]:
            columns={row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
            if column in columns and connection.execute('SELECT 1 FROM '+table+' WHERE '+column+'=? LIMIT 1',(source_id,)).fetchone():
                raise ValueError('Cannot delete source while dependent '+table+' records exist. Delete its dependents first.')
        if item.get('repository_path') or item.get('repository_relative_path'):
            path=central.resolve_managed(item.get('repository_path'),item.get('repository_relative_path') or '')
            try:path.relative_to(central.root.resolve())
            except ValueError:raise ValueError('Source location is outside the managed repository; nothing was deleted.')
            references=connection.execute('SELECT 1 FROM knowledge_items WHERE id<>? AND repository_path=? LIMIT 1',(source_id,item.get('repository_path'))).fetchone()
            if path.is_file() and not references:
                staged=path.with_name('.'+path.name+'.deleting-'+uuid.uuid4().hex);path.replace(staged)
        ids=source_vector_ids(store,item);snapshot=store.snapshot(ids);store.delete_with_snapshot(ids)
        for table,column in [('knowledge_tags','knowledge_id'),('embedding_queue','knowledge_id'),('knowledge_versions','knowledge_item_id')]:
            columns={row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
            if column in columns:
                result=connection.execute('DELETE FROM '+table+' WHERE '+column+'=?',(source_id,))
                summary[{'knowledge_tags':'tags','embedding_queue':'queue','knowledge_versions':'versions'}[table]]=result.rowcount
        connection.execute('DELETE FROM knowledge_items WHERE id=?',(source_id,));connection.commit()
    except Exception as error:
        connection.rollback()
        if snapshot:store.restore_snapshot(snapshot)
        if staged and staged.exists():staged.replace(path)
        if isinstance(error,sqlite3.IntegrityError):raise ValueError('Dependent records block source deletion; nothing was deleted.') from error
        raise
    finally:connection.close()
    removed=False;cleanup_error=''
    if staged:
        try:staged.unlink();removed=True
        except OSError:cleanup_error='Source metadata was deleted; a staged managed file remains for cleanup.'
    folder=path.parent if path else None
    while folder and folder!=central.root and folder.is_relative_to(central.root) and folder.is_dir() and not any(folder.iterdir()):
        parent=folder.parent;folder.rmdir();folder=parent
    return {'deleted':1,'files_removed':int(removed),'vectors_removed':len(snapshot.get('ids',[])) if snapshot else 0,'storage_cleanup_error':cleanup_error,**summary}
