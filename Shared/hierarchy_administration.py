"""Stable hierarchy edits with one SQLite transaction and staged file moves.

No active runtime paths are embedded here. Desktop/Web inject their existing
database, CentralRepository and vector store; no test-case identities change.
"""
from pathlib import Path
import json
import sqlite3

from Shared.document_hierarchy import DocumentHierarchy, HierarchyConflict
from Shared.hierarchy_values import canonical_source_type


FIELDS=('domain','module','knowledge_name','document_type','version','source_type','source_file_id')
KINDS=('domain','module','knowledge','document_type','version','source_type','source_file')


class HierarchyAdministration:
    def __init__(self, database, central, vectors=None, vector_ids=None, on_change=None):
        self.database=database;self.central=central
        self.vectors=vectors;self.vector_ids=vector_ids
        self.on_change=on_change

    @staticmethod
    def _rows(connection, sql, parameters=()):
        cursor=connection.execute(sql,parameters)
        return [dict(zip((column[0] for column in cursor.description),row)) for row in cursor.fetchall()]

    def _identity(self, connection, kind, context):
        index=KINDS.index(kind)
        tables=('domains','modules','knowledge_entities','knowledge_document_types','document_versions')
        if index>4:return None
        rows=self._rows(connection,'''SELECT d.id AS domain_id,m.id AS module_id,k.id AS knowledge_id,
            t.id AS document_type_id,v.id AS version_id,d.name AS domain,m.name AS module,
            k.name AS knowledge_name,t.name AS document_type,v.version
            FROM domains d LEFT JOIN modules m ON m.domain_id=d.id
            LEFT JOIN knowledge_entities k ON k.module_id=m.id
            LEFT JOIN knowledge_document_types t ON t.knowledge_id=k.id
            LEFT JOIN document_versions v ON v.document_type_id=t.id''')
        identifiers={row[KINDS[index]+'_id'] for row in rows
            if all(str(row.get(field) or '')==str(context.get(field) or '') for field in FIELDS[:index+1])}
        identifiers.discard(None)
        if len(identifiers)!=1:raise HierarchyConflict('Selected hierarchy node is absent or ambiguous.')
        return tables[index],next(iter(identifiers))

    def change(self, node, *, value=None, destination=None, validate_only=False, item_updates=None):
        kind=node.get('type')
        if kind not in KINDS:raise HierarchyConflict('Unsupported hierarchy level.')
        index=KINDS.index(kind);field=FIELDS[index]
        context=dict(node.get('context') or {})
        context[field]=context.get(field) or node.get('name')
        if not all(context.get(key) for key in FIELDS[:index+1]):
            raise HierarchyConflict('Complete hierarchy identity is required.')
        if destination is not None and index==0:raise HierarchyConflict('Domain has no parent to move under.')
        if value is not None and not str(value).strip():raise HierarchyConflict('A non-empty hierarchy value is required.')
        new_context=dict(context)
        if destination is not None:
            for key in FIELDS[:index]:
                if not destination.get(key):raise HierarchyConflict('Move destination is incomplete.')
                new_context[key]=destination[key]
        if value is not None:new_context[field]=str(value).strip()
        connection=self.database.get_connection();connection.row_factory=None
        moves=[];planned_moves=[];created=[];snapshots=[]
        try:
            connection.execute('BEGIN IMMEDIATE')
            hierarchy=DocumentHierarchy(connection);hierarchy.ensure_schema()
            tables={row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            sources=self._rows(connection,'SELECT * FROM knowledge_items ORDER BY id')
            for row in sources:row['source_type']=canonical_source_type(row.get('source_type'))
            if context.get('source_type'):context['source_type']=canonical_source_type(context['source_type'])
            if new_context.get('source_type'):new_context['source_type']=canonical_source_type(new_context['source_type'])
            selected=[row for row in sources if all(str(row.get(key) or '')==str(context.get(key) or '') for key in FIELDS[:index+1])]
            owner_ids={row.get('document_version_id') for row in selected if row.get('document_version_id')}
            for table in ('qa_generation_jobs','automation_runs'):
                columns={row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
                if owner_ids and {'document_version_id','status'}<=columns:
                    marks=','.join('?' for _ in owner_ids)
                    if connection.execute('SELECT 1 FROM '+table+' WHERE document_version_id IN ('+marks+") AND lower(status) IN ('queued','running') LIMIT 1",tuple(owner_ids)).fetchone():
                        raise HierarchyConflict('An active generation or automation run owns this hierarchy. Wait for it to finish before changing the hierarchy.')
            item_updates=item_updates or {}
            if set(item_updates)-{row['id'] for row in selected}:raise HierarchyConflict('Edited source is outside the selected hierarchy.')
            for row in selected:
                row['document_version_id']=hierarchy.resolve_source(row['id'])['document_version_id']
            identity=self._identity(connection,kind,context)
            if identity:
                table,node_id=identity
                column='version' if kind=='version' else 'name'
                if destination is not None:
                    parent_kind=KINDS[index-1]
                    parent=self._identity(connection,parent_kind,new_context)
                    parent_column=('','domain_id','module_id','knowledge_id','document_type_id')[index]
                    connection.execute('UPDATE '+table+' SET '+parent_column+'=? WHERE id=?',(parent[1],node_id))
                if value is not None:connection.execute('UPDATE '+table+' SET '+column+'=? WHERE id=?',(new_context[field],node_id))
            elif not selected:
                raise HierarchyConflict('Selected source group is absent.')
            else:
                collision=[row for row in sources if row['id'] not in {item['id'] for item in selected}
                    and all(str(row.get(key) or '')==str(new_context.get(key) or '') for key in FIELDS[:index+1])]
                if collision:raise HierarchyConflict('Destination contains this group; merge is not supported.')

            plans=[]
            for row in selected:
                target_owner=self._identity(connection,'version',new_context)[1] if index>4 and destination is not None else row['document_version_id']
                updated=hierarchy.context(target_owner)
                updated['source_type']=new_context.get('source_type',row.get('source_type'))
                updated['source_file_id']=new_context.get('source_file_id',row.get('source_file_id'))
                # Persisted paths, never a reconstructed old business hierarchy.
                raw=row.get('repository_relative_path') or row.get('repository_path')
                old=self.central.resolve_managed(row.get('repository_path'),row.get('repository_relative_path') or '') if raw else None
                if old:
                    try:old.relative_to(self.central.root.resolve())
                    except ValueError:raise HierarchyConflict('Source location is outside the managed repository.')
                    if not old.is_file():raise HierarchyConflict('Managed source location could not be resolved. The hierarchy was not changed.')
                    source_id=updated['source_file_id'] or self.central.create_source_file_id(old.name)
                    updated['source_file_id']=source_id
                    folder=self.central.build_source_path(**{key:updated[key] for key in ('domain','module','knowledge_name','version','document_type','source_type')},source_file_id=source_id)
                    target=folder/old.name
                    if old.parent.name.startswith('SD-'):
                        old_object,new_object=old.parent,folder
                    else:
                        dependent=connection.execute('SELECT 1 FROM test_case_versions WHERE source_knowledge_id=? LIMIT 1',(row['id'],)).fetchone() if 'test_case_versions' in tables else None
                        if dependent:raise HierarchyConflict('Legacy source with dependent workbooks requires explicit path adoption before hierarchy editing.')
                        old_object,new_object=old,target
                    if old_object!=new_object:
                        if new_object.exists():raise HierarchyConflict('Destination managed object already exists; merge is not supported.')
                        if any(a==old_object and b!=new_object for a,b in planned_moves):raise HierarchyConflict('Shared source container has conflicting destinations.')
                        if (old_object,new_object) not in planned_moves:planned_moves.append((old_object,new_object))
                    updated['repository_path']=str(target)
                    updated['repository_relative_path']=self.central.relative(target)
                else:
                    # Only metadata-only/external sources may have no file object.
                    if str(row.get('source_type') or '').upper() in ('FILE','FILES','MANUAL_UPLOAD','SMART_UPLOAD','FOLDER'):
                        raise HierarchyConflict('Managed file source has no persisted location. No changes were applied.')
                plans.append((row,updated))

            source_ids=[row['id'] for row in selected]
            marks=','.join('?' for _ in source_ids) or 'NULL'
            counts={'versions':len({row['document_version_id'] for row in selected}),
                'document_types':len({row['document_type'] for row in selected}),
                'source_types':len({row['source_type'] for row in selected}),
                'source_files':len({row['source_file_id'] for row in selected}),
                'source_documents':len(selected)}
            for label,table,column in [('tc_folders','test_case_versions','source_knowledge_id'),('playwright_artifacts','playwright_automation_assets','source_knowledge_id')]:
                exists=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(table,)).fetchone()
                counts[label]=connection.execute('SELECT COUNT(*) FROM '+table+' WHERE '+column+' IN ('+marks+')',source_ids).fetchone()[0] if exists else 0
            versions=[row[0] for row in connection.execute('SELECT id FROM test_case_versions WHERE source_knowledge_id IN ('+marks+')',source_ids)] if 'test_case_versions' in tables else []
            version_marks=','.join('?' for _ in versions) or 'NULL'
            for label,table in [('xlsx_files','test_case_files'),('api_artifacts','api_collections'),('sql_artifacts','sql_scripts')]:
                columns={row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
                counts[label]=connection.execute('SELECT COUNT(*) FROM '+table+' WHERE test_case_version_id IN ('+version_marks+')',versions).fetchone()[0] if 'test_case_version_id' in columns else 0
            counts['automation_artifacts']=sum(counts[key] for key in ('playwright_artifacts','api_artifacts','sql_artifacts'))
            result={'updated':len(selected),'moved':len(selected),'would_move':len(selected),'conflicts':[],
                    'impact_count':len(selected),'test_case_ids_changed':False,'descendant_counts':counts,
                    'source':{'type':kind,'label':field.replace('_',' ').title(),'name':context[field]},
                    'current_parent':{'type':KINDS[index-1] if index else None,'label':FIELDS[index-1].replace('_',' ').title() if index else '', 'name':context.get(FIELDS[index-1]) if index else None},
                    'target_parent':{'type':KINDS[index-1] if index else None,'label':FIELDS[index-1].replace('_',' ').title() if index else '', 'name':new_context.get(FIELDS[index-1]) if index else None},
                    'canonical_node_id':identity[1] if identity else None,'validated':True}
            if validate_only:
                connection.rollback();return result
            for old,new in planned_moves:
                current=new.parent
                while not current.exists():created.append(current);current=current.parent
                new.parent.mkdir(parents=True,exist_ok=True)
                old.replace(new);moves.append((old,new))
            self._rewrite_paths(connection,moves)
            for old,updated in plans:
                extra=item_updates.get(old['id'],{})
                if set(extra)-{'summary','tags'}:raise HierarchyConflict('Unsupported source metadata field.')
                updated.update(extra)
                if self.vectors is not None and self.vector_ids is not None:
                    ids=self.vector_ids(old)
                    snapshot=self.vectors.snapshot(ids);snapshots.append(snapshot)
                    self.vectors.update_metadata(ids,{key:updated[key] for key in FIELDS[:5]} | {'knowledge_entity_id':updated['knowledge_entity_id'],'document_type_id':updated['document_type_id'],'document_version_id':updated['document_version_id'],'source_type':updated['source_type'],'source_file_id':updated['source_file_id'] or ''} | {key:(', '.join(value) if key=='tags' and isinstance(value,list) else value) for key,value in extra.items()})
                columns=[key for key in (*FIELDS,'domain_id','module_id','document_version_id','repository_path','repository_relative_path') if key in updated]
                connection.execute('UPDATE knowledge_items SET '+','.join(key+'=?' for key in columns)+',knowledge_path=? WHERE id=?',
                    [updated[key] for key in columns]+['/'.join(updated[key] for key in FIELDS[:5]),old['id']])
                if extra:
                    connection.execute('UPDATE knowledge_items SET '+','.join(key+'=?' for key in extra)+' WHERE id=?',
                        [json.dumps(value) if key=='tags' and isinstance(value,list) else value for key,value in extra.items()]+[old['id']])
                if 'test_case_versions' in tables:
                    connection.execute('UPDATE test_case_versions SET document_version_id=?,source_file_id=? WHERE source_knowledge_id=?',
                        (updated['document_version_id'],updated['source_file_id'],old['id']))
                    case_columns={row[1] for row in connection.execute('PRAGMA table_info(test_cases)')}
                    if 'test_case_version_id' in case_columns:
                        connection.execute('UPDATE test_cases SET document_version_id=? WHERE test_case_version_id IN (SELECT id FROM test_case_versions WHERE source_knowledge_id=?)',(updated['document_version_id'],old['id']))
            # Stable ownership propagates label aliases, never ownership IDs.
            self._sync_aliases(connection,hierarchy)
            if connection.execute('PRAGMA foreign_key_check').fetchall():raise HierarchyConflict('Hierarchy change would violate a foreign-key dependency.')
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback();self._restore(moves,created,snapshots)
            raise HierarchyConflict('Destination contains this hierarchy value or a dependency blocks the change; no merge was applied.') from error
        except Exception:
            connection.rollback();self._restore(moves,created,snapshots);raise
        finally:connection.close()
        # A post-commit cache callback must never enter the persistence rollback
        # path: the database has already committed the filesystem/vector change.
        if self.on_change:self.on_change()
        return result

    def _restore(self,moves,created,snapshots):
        failures=[]
        for snapshot in reversed(snapshots):
            try:self.vectors.restore_metadata(snapshot)
            except Exception as error:failures.append(error)
        for old,new in reversed(moves):
            try:new.replace(old)
            except Exception as error:failures.append(error)
        for folder in sorted(set(created),key=lambda path:len(path.parts),reverse=True):
            if folder.is_dir() and not any(folder.iterdir()):folder.rmdir()
        if failures:raise RuntimeError('Hierarchy rollback needs recovery; staged objects were retained.') from failures[0]

    def _rewrite_paths(self,connection,moves):
        path_columns={'repository_path','original_path','folder_path','workbook_path','reviewed_workbook_path','artifact_path','sql_script_path','script_path','screenshot_path','evidence_path','report_path'}
        tables=connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'").fetchall()
        for table, in tables:
            for column in (row[1] for row in connection.execute('PRAGMA table_info('+table+')') if row[1] in path_columns):
                for row_id,raw in connection.execute('SELECT rowid,'+column+' FROM '+table+' WHERE '+column+" IS NOT NULL AND "+column+"<>''").fetchall():
                    path=Path(raw)
                    for old,new in moves:
                        if path==old or old in path.parents:
                            connection.execute('UPDATE '+table+' SET '+column+'=? WHERE rowid=?',(str(new/path.relative_to(old)),row_id));break

    def _sync_aliases(self,connection,hierarchy):
        for table in ('knowledge_versions','test_cases','api_collections','qa_generation_jobs','automation_runs'):
            columns={row[1] for row in connection.execute('PRAGMA table_info('+table+')')}
            if 'document_version_id' not in columns:continue
            for owner_id, in connection.execute('SELECT DISTINCT document_version_id FROM '+table+' WHERE document_version_id IS NOT NULL').fetchall():
                owner=hierarchy.context(owner_id)
                fields=[field for field in (*FIELDS[:5],'domain_id','module_id') if field in columns]
                if fields:connection.execute('UPDATE '+table+' SET '+','.join(field+'=?' for field in fields)+' WHERE document_version_id=?',[owner[field] for field in fields]+[owner_id])
                if table=='qa_generation_jobs':
                    for job_id,raw in connection.execute('SELECT job_id,payload_json FROM qa_generation_jobs WHERE document_version_id=?',(owner_id,)).fetchall():
                        data=json.loads(raw or '{}');data.update({field:owner[field] for field in FIELDS[:5]})
                        connection.execute('UPDATE qa_generation_jobs SET payload_json=? WHERE job_id=?',(json.dumps(data),job_id))

    def delete_empty(self,node):
        kind=node.get('type');context=dict(node.get('context') or {})
        if kind not in KINDS[:5]:raise HierarchyConflict('Delete leaf sources through the existing source service.')
        context[FIELDS[KINDS.index(kind)]]=context.get(FIELDS[KINDS.index(kind)]) or node.get('name')
        connection=self.database.get_connection();connection.row_factory=None
        try:
            connection.execute('BEGIN IMMEDIATE');identity=self._identity(connection,kind,context)
            table,node_id=identity
            dependencies={'domains':('modules','domain_id'),'modules':('knowledge_entities','module_id'),
                'knowledge_entities':('knowledge_document_types','knowledge_id'),'knowledge_document_types':('document_versions','document_type_id')}
            if table in dependencies:
                child,parent=dependencies[table]
                if connection.execute('SELECT 1 FROM '+child+' WHERE '+parent+'=? LIMIT 1',(node_id,)).fetchone():
                    raise HierarchyConflict('Cannot delete this hierarchy node while child '+child+' records exist. Delete its children first.')
            connection.execute('DELETE FROM '+table+' WHERE id=?',(node_id,));connection.commit();return {'deleted':1}
        except sqlite3.IntegrityError as error:
            connection.rollback();raise HierarchyConflict('Dependent sources or QA records block this deletion.') from error
        except Exception:connection.rollback();raise
        finally:connection.close()
