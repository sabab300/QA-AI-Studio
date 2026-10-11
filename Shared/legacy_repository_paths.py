"""Compatibility entry points resolve typed compact paths, not K/V folders."""
def repository_path(manager,domain,module,knowledge_name,version,document_type=None):
    if not document_type:
        from Database.db_manager import DatabaseManager
        connection=DatabaseManager().get_connection()
        try:
            types={row[0] for row in connection.execute('SELECT DISTINCT document_type FROM knowledge_items WHERE domain=? AND module=? AND knowledge_name=? AND version=?',
                (domain,module,knowledge_name,version)).fetchall() if row[0]}
        finally:connection.close()
        if len(types)>1:raise ValueError('Document Type is required: this version label exists under multiple types.')
        document_type=next(iter(types)) if types else 'Other'
    path=manager.central.hierarchy_root(domain,module,knowledge_name,version,document_type,'Files')
    path.mkdir(parents=True,exist_ok=True)
    return path
