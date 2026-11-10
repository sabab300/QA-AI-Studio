"""Review-only HAR/Postman/native import. Never execute imported scripts."""
import json
import re
from urllib.parse import urlsplit,parse_qsl,urlunsplit,urlencode
from .collections import reviewed_document,REFERENCE
from .evidence import DiscoveryError,parse_har,normalize_entry,MAX_UPLOAD
from .samples import parse_sample,sanitize_sample


def imported(document,format):
    document=reviewed_document(document)
    # A file is an authorized copy, not authority to attach to another user's
    # private capture history. Keep explicit version provenance instead.
    if document.get('collection_id'):
        document['provenance']={'format':format,'collection_id':document['collection_id'],'version':document.get('version',1)}
    if document.pop('evidence_session_id',None):
        document['compatibility']=sorted(set(document.get('compatibility',[])+['Original capture history is retained by its owner; this import is a reviewed copy.']))
    return reviewed_document(document)


def parse(data,kind):
    if len(data)>MAX_UPLOAD:raise DiscoveryError('Import exceeds 10 MiB.')
    try:value=json.loads(data)
    except (ValueError,UnicodeError,RecursionError):raise DiscoveryError('Import is not valid bounded JSON.') from None
    warnings=[]
    if kind=='native':
        if not isinstance(value,dict) or value.get('format')!='qa-ai-studio-api-collection' or value.get('format_version')!=1:
            raise DiscoveryError('Unsupported native collection format/version.')
        return imported(value,'native')
    if kind=='har':
        parse_har(data)  # validate every entry before constructing any preview
        requests=[]
        from Core.secret_masking import is_sensitive_key
        for index,entry in enumerate(value['log']['entries']):
            item=normalize_entry(entry)
            if str(entry.get('_resourceType','')).lower() not in {'xhr','fetch'} and 'json' not in item['content_type'] and item['method'] not in {'POST','PUT','PATCH','DELETE'}:continue
            if len(requests)>=100:raise DiscoveryError('Import at most 100 API occurrences per collection; split the HAR.')
            raw=entry['request'];parts=urlsplit(raw['url'])
            query=[]
            for name,field in parse_qsl(parts.query,keep_blank_values=True,max_num_fields=60):
                query.append((name,sanitize_sample({name:field})[name]))
            url=item['observed_url'].split('?',1)[0]+('?' +urlencode(query,safe='{}') if query else '')
            request={'id':'api_'+str(index+1),'name':item['method']+' '+item['endpoint'],'method':item['method'],
                     'url':url,'headers':{},'assertions':[],'evidence_ids':[]}
            from .browser_samples import headers as safe_headers
            request['headers']=safe_headers({str(header.get('name','')):str(header.get('value','')) for header in raw.get('headers',[])},True)
            for header in raw.get('headers',[]):
                name=str(header.get('name',''))
                if name.lower() in {'accept','content-type'} and not is_sensitive_key(name):request['headers'][name]=str(header.get('value',''))[:200]
            post=raw.get('postData',{})
            if post:
                try:
                    text=post.get('text')
                    if text is None and post.get('params'):text=urlencode([(p.get('name',''),p.get('value','')) for p in post['params']])
                    body,mode=parse_sample(text or '',post.get('mimeType',''))
                    request.update(body=body,body_mode=mode)
                except DiscoveryError:
                    request['requires_configuration']=True
                    warnings.append('A request body exceeded supported capture limits or encoding; configure it manually.')
            requests.append(request)
        warnings.append('Sensitive values were replaced by references. Review request samples and configure authentication; no assertions were inferred.')
        return reviewed_document({'requests':requests,'variables':{},'compatibility':warnings})
    if kind!='postman' or not isinstance(value,dict) or not isinstance(value.get('info'),dict) or not isinstance(value.get('item'),list):
        raise DiscoveryError('Expected a Postman Collection v2.1 object.')
    schema=str(value['info'].get('schema',''))
    if not schema.endswith('/v2.1.0/collection.json'):raise DiscoveryError('Only Postman Collection v2.1 is supported.')
    count=0;raw_requests=[]
    def bound(items,depth=0):
        nonlocal count
        if depth>8:raise DiscoveryError('Postman nesting exceeds eight folders.')
        if not isinstance(items,list):raise DiscoveryError('Malformed Postman folder.')
        for item in items:
            if not isinstance(item,dict):raise DiscoveryError('Malformed Postman item.')
            if 'request' in item:
                count+=1
                if not isinstance(item['request'],dict):raise DiscoveryError('String-only requests must first be converted to Postman request objects.')
                raw_requests.append(item['request'])
            elif 'item' in item:bound(item['item'],depth+1)
            else:raise DiscoveryError('Unsupported Postman item structure.')
            if count>100:raise DiscoveryError('Import at most 100 requests per collection.')
    bound(value['item'])
    # Reuse the established Postman request/folder parser without constructing
    # its TC-owned repository, database or embedding services.
    from Core.api_collection_repository import ApiCollectionRepository
    parser=ApiCollectionRepository.__new__(ApiCollectionRepository)
    endpoints,_=parser._flatten_items(value['item'],{})
    from Core.secret_masking import is_sensitive_key
    def scrub_fields(data):
        return sanitize_sample(data)
    variables={}
    for variable in value.get('variable',[]):
        key=str(variable.get('key',''))
        variables[key]='{{'+key+'}}' if is_sensitive_key(key) else variable.get('value','')
    requests=[]
    for index,item in enumerate(endpoints):
        if len({h['key'].lower() for h in item['headers']})!=len(item['headers']):raise DiscoveryError('Repeated Postman header names require manual review before import.')
        url=item['url_raw'];parts=urlsplit(url)
        pairs=parse_qsl(parts.query,keep_blank_values=True)
        if any(is_sensitive_key(k) for k,v in pairs):
            url=urlunsplit((parts.scheme,parts.netloc,parts.path,urlencode([(k,'{{'+k+'}}' if is_sensitive_key(k) else v) for k,v in pairs],safe='{}'),''))
            warnings.append('Sensitive URL parameters replaced by variable references.')
        headers=scrub_fields({h['key']:h['value'] for h in item['headers']})
        body=None
        if item['body_mode']=='raw':
            try:body=scrub_fields(json.loads(item['body_raw']))
            except (ValueError,TypeError):
                warnings.append('Non-JSON raw body was not imported; configure it manually.')
        elif item['body_mode']:
            if item['body_mode']=='urlencoded':
                raw_body=raw_requests[index].get('body',{}).get('urlencoded',[])
                pairs=[(p.get('key',''),p.get('value','')) for p in raw_body if not p.get('disabled')]
                if len({k for k,v in pairs})!=len(pairs):raise DiscoveryError('Repeated form fields require manual configuration.')
                body=sanitize_sample(dict(pairs))
            else:warnings.append('Multipart/file bodies require manual configuration; binary files are not imported.')
        assertions=[]
        # Only exact known safe export statements are translated; never run JS.
        script=item.get('tests_script','')
        status=re.fullmatch(r'pm\.test\("HTTP status", function\(\)\{pm\.response\.to\.have\.status\((\d{3})\);\}\);',script.strip())
        if status:assertions=[{'id':'http_status','type':'status_equals','expected':int(status.group(1)),'enabled':True}]
        elif script:warnings.append('Custom Postman tests were not executed or translated.')
        if item['auth_type'] or value.get('auth'):warnings.append('Postman authentication requires review and secure environment references; captured credentials are not reused.')
        request={'id':'api_'+str(index+1),'name':item['name'],'folder':item['folder_path'],'method':item['method'],'url':url,'headers':headers,'body':body,'assertions':assertions}
        if item['body_mode']=='urlencoded':request['body_mode']='urlencoded'
        requests.append(request)
    native=value.get('x-qa-ai-studio')
    if native:
        # Native metadata preserves declarative assertions/dependencies only
        # after normal schema/secret validation, never arbitrary Postman code.
        reviewed=reviewed_document(native)
        if len(reviewed['requests'])!=len(requests):raise DiscoveryError('Native/Postman request count conflict.')
        for index,(left,right) in enumerate(zip(reviewed['requests'],requests)):
            if (left['method'],left['url'])!=(right['method'],right['url']):raise DiscoveryError('Native/Postman request definition conflict.')
            if {k.lower():v for k,v in left.get('headers',{}).items()}!={k.lower():v for k,v in right.get('headers',{}).items()}:
                raise DiscoveryError('Native/Postman headers disagree. Resolve stale metadata before importing.')
            body=raw_requests[index].get('body') or {}
            if left.get('body') is not None:
                if left.get('body_mode')=='urlencoded':
                    pairs=[(str(p.get('key','')),str(p.get('value',''))) for p in body.get('urlencoded',[]) if not p.get('disabled')]
                    expected=[(str(k),str(v)) for k,v in left['body'].items()]
                    matches=body.get('mode')=='urlencoded' and pairs==expected
                else:
                    expected=json.dumps(left['body']) if not isinstance(left['body'],str) else left['body']
                    matches=body.get('mode')=='raw' and body.get('raw')==expected
                if not matches:raise DiscoveryError('Native/Postman request bodies disagree. Resolve stale metadata before importing.')
            elif body:raise DiscoveryError('Native/Postman body ownership conflict.')
            auth=left.get('auth',{});external=raw_requests[index].get('auth') or {}
            if auth.get('type') in {'bearer','basic'}:
                kind=auth['type'];actual={field.get('key'):field.get('value') for field in external.get(kind,[])}
                expected={'token':'{{'+auth.get('variable','')+'}}'} if kind=='bearer' else {'username':'{{'+auth.get('username_variable','')+'}}','password':'{{'+auth.get('password_variable','')+'}}'}
                if external.get('type')!=kind or actual!=expected:raise DiscoveryError('Native/Postman authentication references disagree.')
            elif external and external.get('type')!='noauth':raise DiscoveryError('Native/Postman authentication ownership conflict.')
        if warnings:reviewed['compatibility']=sorted(set(reviewed.get('compatibility',[])+warnings))
        return imported(reviewed,'postman')
    return reviewed_document({'name':value['info'].get('name','Imported API collection'),'requests':requests,'variables':variables,'compatibility':sorted(set(warnings))})


def postman(document):
    document=reviewed_document(document);items=[];warnings=[]
    for item in document['requests']:
        request={'name':item.get('name',item['id']),'request':{'method':item['method'],'url':item['url'],
            'header':[{'key':k,'value':v} for k,v in item.get('headers',{}).items()]}}
        auth=item.get('auth',{})
        if auth.get('type')=='bearer':
            request['request']['auth']={'type':'bearer','bearer':[{'key':'token','value':'{{'+auth.get('variable','')+'}}','type':'string'}]}
        elif auth.get('type')=='basic':
            request['request']['auth']={'type':'basic','basic':[{'key':'username','value':'{{'+auth.get('username_variable','')+'}}','type':'string'},{'key':'password','value':'{{'+auth.get('password_variable','')+'}}','type':'string'}]}
        if item.get('body') is not None:
            if item.get('body_mode')=='urlencoded':request['request']['body']={'mode':'urlencoded','urlencoded':[{'key':k,'value':str(v),'type':'text'} for k,v in item['body'].items()]}
            else:request['request']['body']={'mode':'raw','raw':json.dumps(item['body']) if not isinstance(item['body'],str) else item['body']}
        enabled=[a for a in item.get('assertions',[]) if a.get('enabled',True)]
        if len(enabled)==1 and enabled[0]['type']=='status_equals':
            expected=enabled[0]['expected']
            if isinstance(expected,int):request['event']=[{'listen':'test','script':{'type':'text/javascript','exec':['pm.test("HTTP status", function(){pm.response.to.have.status('+str(expected)+');});']}}]
        if enabled and not request.get('event'):warnings.append(item['id']+': assertions retained in native metadata, not translated to executable Postman tests.')
        if item.get('extract') or item.get('depends_on'):warnings.append(item['id']+': workflow dependencies require QA AI Studio; no Postman script was generated.')
        folder=item.get('folder','')
        current=items
        for name in folder.split(' > ') if folder else []:
            existing=next((entry for entry in current if entry.get('name')==name and 'item' in entry),None)
            if existing is None:existing={'name':name,'item':[]};current.append(existing)
            current=existing['item']
        current.append(request)
    return {'info':{'name':document.get('name','API Discovery'),'schema':'https://schema.getpostman.com/json/collection/v2.1.0/collection.json'},
            'item':items,'variable':[{'key':k,'value':v} for k,v in document.get('variables',{}).items()],
            'x-qa-ai-studio':document,'x-compatibility-report':warnings}
