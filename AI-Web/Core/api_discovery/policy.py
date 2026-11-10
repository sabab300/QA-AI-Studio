"""Read-only adapter to the existing enterprise Application Under Test registry."""
import json
import os
from urllib.parse import urlsplit
from .evidence import DiscoveryError


def targets(repository):
    with repository.connection() as connection:
        exists=connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='uiux_targets'").fetchone()
        if not exists:return []
        return [dict(row) for row in connection.execute('SELECT id,name,origin,environment,internal_ok,extra_origins_json FROM uiux_targets WHERE active=1 ORDER BY name LIMIT 200')]


def approved(repository,url,environment,user):
    from Core.uiux.security import normalize_url,origin_of,TargetError
    try:origin=origin_of(normalize_url(url))
    except TargetError:raise DiscoveryError('The selected target is not an approved HTTP(S) origin.') from None
    record=next((row for row in targets(repository) if row['origin']==origin and row['environment']==environment),None)
    if not record:raise DiscoveryError('Target is not approved for this environment. An authorized administrator must register it in Application Under Test first.')
    local=bool(record['internal_ok']) and str(user.get('role_name','')).lower()=='admin' and os.environ.get('QA_API_DISCOVERY_ALLOW_LOCAL_TARGETS')=='1'
    # resolve_target still rejects private/metadata networks. The only special
    # development policy is explicitly registered, Admin-only loopback.
    from .capture import resolve_target
    host,address=resolve_target(url,allow_local=local)
    return dict(record,host=host,address=address,allow_local=local,resource_origins=json.loads(record['extra_origins_json'] or '[]'))
