"""TC-independent bounded replay, with per-request authorization and DNS pinning."""
import base64
import json
import re
import threading
import time
import zlib
from urllib.parse import urlsplit
import urllib3
from .assertions import evaluate,resolve
from .capture import resolve_target
from .collections import reviewed_document
from .evidence import DiscoveryError

VARIABLE=re.compile(r'\{\{([A-Za-z_][A-Za-z0-9_-]{0,63})\}\}')
MAX_RESPONSE=1024*1024

class TransportError(Exception):
    pass


def substitute(value,variables):
    if isinstance(value,dict):return {k:substitute(v,variables) for k,v in value.items()}
    if isinstance(value,list):return [substitute(v,variables) for v in value]
    if not isinstance(value,str):return value
    def replace(match):
        name=match.group(1)
        if name not in variables:raise DiscoveryError('Required variable is missing: '+name)
        result=variables[name]
        if result is None:raise DiscoveryError('Required variable has no value: '+name)
        if isinstance(result,(dict,list)):raise DiscoveryError('Use scalar request variables.')
        if isinstance(result,str) and VARIABLE.search(result):raise DiscoveryError('Required runtime variable is unresolved: '+name)
        return str(result)
    exact=VARIABLE.fullmatch(value)
    if exact:
        replace(exact)  # validate scalar presence and unresolved references
        return variables[exact.group(1)]
    return VARIABLE.sub(replace,value)


def sanitize(value,secrets=(),key='',depth=0):
    from Core.secret_masking import is_sensitive_key
    from .samples import PRIVATE
    from Core.ai_platform.sanitize import scrub
    if depth>20:return '[TRUNCATED]'
    if is_sensitive_key(key) or PRIVATE.search(key):return '[REDACTED]'
    if not isinstance(value,(dict,list)) and value is not None and len(str(value))>=4 and str(value) in secrets:return '[REDACTED]'
    if isinstance(value,dict):return {str(k):sanitize(v,secrets,str(k),depth+1) for k,v in list(value.items())[:200]}
    if isinstance(value,list):return [sanitize(v,secrets,key,depth+1) for v in value[:200]]
    if isinstance(value,str):
        if re.fullmatch(r'[A-Za-z0-9_=-]{40,}',value) or re.fullmatch(r'[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}',value):return '[OPAQUE VALUE REDACTED]'
        if value.startswith(('http://','https://')):
            from .evidence import safe_url
            from urllib.parse import parse_qsl,urlencode
            try:
                query=parse_qsl(urlsplit(value).query,keep_blank_values=True,max_num_fields=60)
                value=safe_url(value).split('?',1)[0]
                if query:value+='?'+urlencode([(name,sanitize(field,secrets,name,depth+1)) for name,field in query])
            except DiscoveryError:value='[INVALID URL OMITTED]'
        return scrub(value,secrets=secrets,limit=8000)
    return value


def send(request,timeout,cancel,allow_local=False):
    """Never use proxy env vars or resolve the hostname again after policy check."""
    url=request['url'];parts=urlsplit(url)
    if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise DiscoveryError('Replay target is not an authorized HTTP(S) URL.')
    host,address=resolve_target(url,allow_local=allow_local)
    headers={str(key):str(value) for key,value in request.get('headers',{}).items()}
    if any('\r' in key+value or '\n' in key+value for key,value in headers.items()):raise DiscoveryError('Resolved header values contain prohibited newlines.')
    headers['Host']=host+(':'+str(parts.port) if parts.port else '')
    if not any(name.lower()=='accept-encoding' for name in headers):headers['Accept-Encoding']='identity'
    data=request.get('body');started=time.monotonic()
    if request.get('body_mode')=='urlencoded':
        from urllib.parse import urlencode
        data=urlencode(data).encode()
        if not any(name.lower()=='content-type' for name in headers):headers['Content-Type']='application/x-www-form-urlencoded'
    elif isinstance(data,(dict,list)):
        data=json.dumps(data).encode()
        if not any(name.lower()=='content-type' for name in headers):headers['Content-Type']='application/json'
    elif data is not None:data=str(data).encode()
    if data and len(data)>1024*1024:raise DiscoveryError('Request body exceeds 1 MiB.')
    options={'host':address,'port':parts.port or (443 if parts.scheme=='https' else 80),'maxsize':1,'block':True}
    if parts.scheme=='https':
        pool=urllib3.HTTPSConnectionPool(**options,server_hostname=host,assert_hostname=host,cert_reqs='CERT_REQUIRED')
    else:pool=urllib3.HTTPConnectionPool(**options)
    response=None
    try:
        if cancel.is_set():raise DiscoveryError('Execution cancelled.')
        response=pool.urlopen(request['method'],(parts.path or '/')+('?' +parts.query if parts.query else ''),body=data,headers=headers,
                              redirect=False,retries=False,preload_content=False,
                              timeout=urllib3.Timeout(total=timeout,connect=min(5,timeout),read=min(5,timeout)))
        chunks=[];size=0;wire_size=0
        encoding=response.headers.get('Content-Encoding','identity').lower()
        if encoding not in {'identity','gzip','deflate'}:raise TransportError('Unsupported compressed response; assertions were not evaluated.')
        decoder=zlib.decompressobj(16+zlib.MAX_WBITS if encoding=='gzip' else zlib.MAX_WBITS) if encoding!='identity' else None
        while True:
            if cancel.is_set():raise DiscoveryError('Execution cancelled.')
            if time.monotonic()-started>timeout:raise TransportError('Response exceeded request timeout.')
            chunk=response.read(16384,decode_content=False)
            if not chunk:break
            wire_size+=len(chunk)
            if wire_size>MAX_RESPONSE:raise TransportError('Wire response exceeded 1 MiB.')
            if decoder:chunk=decoder.decompress(chunk,MAX_RESPONSE-size+1)
            size+=len(chunk)
            if size>MAX_RESPONSE:raise TransportError('Response exceeded the 1 MiB execution limit; assertions were not evaluated on truncated data.')
            chunks.append(chunk)
        if decoder and (not decoder.eof or decoder.unused_data):raise TransportError('Compressed response was truncated or contained unsupported trailing streams.')
        raw=b''.join(chunks)
        try:body=json.loads(raw)
        except (ValueError,UnicodeError,RecursionError):body=None
        return {'status':response.status,'headers':dict(response.headers),'json':body,
                'duration_ms':round((time.monotonic()-started)*1000,2),'bytes':len(raw),'wire_bytes':wire_size,
                'redirect_not_followed':300<=response.status<400}
    finally:
        if response is not None:response.close()
        pool.close()


def prepare(document,configuration,require_authorization=True):
    document=reviewed_document(document)
    configuration=dict(configuration or {})
    selected=configuration.get('selected_request_ids')
    if selected is not None:
        if not isinstance(selected,list) or not selected or len(selected)!=len(set(selected)) or not set(selected)<={item['id'] for item in document['requests']}:raise DiscoveryError('Select valid, distinct APIs from this collection.')
        document['requests']=[item for item in document['requests'] if item['id'] in selected]
        if any(not set(item.get('depends_on',[]))<=set(selected) for item in document['requests']):raise DiscoveryError('Selected APIs require earlier dependent APIs; include their dependencies explicitly.')
    if require_authorization and configuration.get('authorized') is not True:raise DiscoveryError('Authorize the selected environment and request scope before execution.')
    if require_authorization and any(item.get('requires_configuration') for item in document['requests']):raise DiscoveryError('REQUIRES CONFIGURATION: complete and explicitly review omitted request data before replay.')
    allowed=configuration.get('allowed_origins',[])
    if not isinstance(allowed,list) or not 1<=len(allowed)<=10:raise DiscoveryError('Select between 1 and 10 explicitly authorized target origins.')
    origins=set()
    for origin in allowed:
        parts=urlsplit(origin)
        if parts.scheme not in {'https','http'} or not parts.hostname or parts.username or parts.password or parts.path not in {'','/'} or parts.query or parts.fragment:
            raise DiscoveryError('Allowed origins must contain only scheme, host and optional port.')
        origins.add((parts.scheme,parts.hostname.lower(),parts.port or (443 if parts.scheme=='https' else 80)))
    timeout=int(configuration.get('timeout_seconds',20));delay=float(configuration.get('delay_seconds',0))
    retries=int(configuration.get('retry_count',0))
    if not 0<=retries<=2:raise DiscoveryError('Retries must be between 0 and 2.')
    if require_authorization and retries and configuration.get('confirm_retries') is not True:raise DiscoveryError('Explicitly authorize repeated requests before enabling retries.')
    if not 1<=timeout<=60 or not 0<=delay<=5:raise DiscoveryError('Timeout must be 1–60s and delay 0–5s.')
    if require_authorization and any(item['method'] not in {'GET','HEAD','OPTIONS'} for item in document['requests']) and configuration.get('confirm_state_changes') is not True:
        raise DiscoveryError('Explicit state-changing request confirmation is required.')
    variables=dict(document.get('variables',{}));variables.update(configuration.get('variables',{}))
    if len(json.dumps(variables))>1024*1024:raise DiscoveryError('Runtime variables exceed 1 MiB.')
    return document,configuration,origins,timeout,delay,retries,variables


def preview(document,configuration):
    from Core.secret_masking import is_sensitive_key
    document,configuration,origins,timeout,delay,retries,variables=prepare(document,configuration,False)
    produced=set();rows=[]
    for item in document['requests']:
        text=json.dumps({key:item.get(key) for key in ('url','headers','body')})
        required=set(VARIABLE.findall(text))
        auth=item.get('auth',{})
        required.update(value for key,value in auth.items() if key!='type')
        missing=sorted(name for name in required if name not in produced and (name not in variables or variables[name] is None or isinstance(variables[name],str) and VARIABLE.search(variables[name])))
        row={'id':item['id'],'method':item['method'],'url_template':item['url'],'missing_variables':missing,
             'dependencies':item.get('depends_on',[]),'runtime_extractions':sorted(required&produced),
             'enabled_assertions':sum(a.get('enabled',True) for a in item.get('assertions',[])),
             'status':'BLOCKED' if missing else 'READY','reason':'Missing variables' if missing else ''}
        if not missing:
            values=dict(variables);values.update({name:'runtime_value' for name in produced if name not in values})
            url=substitute(item['url'],values);parts=urlsplit(url)
            origin=(parts.scheme,parts.hostname.lower() if parts.hostname else '',parts.port or (443 if parts.scheme=='https' else 80))
            if origin not in origins:row.update(status='BLOCKED',reason='Target origin has not been authorized for this replay.')
            secrets={str(value) for name,value in variables.items() if name in configuration.get('secure_variable_names',[]) or is_sensitive_key(name)}
            if required&produced:
                row['request_preview']=sanitize({key:item.get(key) for key in ('method','url','headers','body')},secrets)
                row['preview_notice']='Extracted values are deferred until preceding requests execute.'
            else:row['request_preview']=sanitize(substitute({key:item.get(key) for key in ('method','url','headers','body')},variables),secrets)
        if item.get('requires_configuration'):row.update(status='BLOCKED',reason='REQUIRES CONFIGURATION: omitted request data needs review.')
        rows.append(row);produced.update(rule['variable'] for rule in item.get('extract',[]))
    return {'status':'BLOCKED' if any(row['status']=='BLOCKED' for row in rows) else 'READY','requests':rows,
            'timeout_seconds':timeout,'retry_count':retries,'delay_seconds':delay,'executed':False,
            'notice':'Configuration preview only, not a PASS result. DNS/network policy and actual assertions are rechecked during execution.'}


def execute(document,configuration,cancel=None,transport=send,progress=None):
    document,configuration,origins,timeout,delay,retries,variables=prepare(document,configuration)
    cancel=cancel or threading.Event();progress=progress or (lambda result:None)
    from Core.secret_masking import is_sensitive_key
    secret_names=set(configuration.get('secure_variable_names',[]))
    for item in document['requests']:
        secret_names.update(value for key,value in item.get('auth',{}).items() if key!='type')
        for key,value in item.get('headers',{}).items():
            if is_sensitive_key(key):secret_names.update(VARIABLE.findall(value))
    sensitive={str(v) for k,v in variables.items() if (is_sensitive_key(k) or k in secret_names) and v not in (None,'')}
    def collect_secrets(value,key='',sensitive_parent=False):
        sensitive_value=sensitive_parent or is_sensitive_key(key)
        if isinstance(value,dict):
            for name,child in value.items():collect_secrets(child,str(name),sensitive_value)
        elif isinstance(value,list):
            for child in value:collect_secrets(child,key,sensitive_value)
        elif sensitive_value and value not in (None,''):
            sensitive.add(str(value))
            if isinstance(value,str) and value.startswith(('Bearer ','Basic ')):sensitive.add(value.split(' ',1)[1])
    outcomes={};results=[];started=time.monotonic();deadline=started+min(600,int(configuration.get('duration_seconds',300)))
    stop=False;retained_response_bytes=0
    for item in document['requests']:
        result={'id':item['id'],'name':item.get('name') or item['id'],'method':item['method'],'assertions':[]}
        if cancel.is_set():result['status']='CANCELLED'
        elif stop:result['status']='SKIPPED'
        elif time.monotonic()>=deadline:result.update(status='BLOCKED',reason='Execution duration limit reached.')
        elif any(outcomes.get(identity)!='PASS' for identity in item.get('depends_on',[])):
            result.update(status='BLOCKED',reason='Required prior API did not pass its assertions.')
        else:
            try:
                request={key:substitute(item.get(key),variables) for key in ('method','url','headers','body','body_mode')}
                if not isinstance(request['url'],str):raise DiscoveryError('Target URL must resolve to text.')
                request['headers']=request.get('headers') or {}
                parts=urlsplit(request['url']);origin=(parts.scheme,parts.hostname.lower() if parts.hostname else '',parts.port or (443 if parts.scheme=='https' else 80))
                if origin not in origins:raise DiscoveryError('API target is not independently authorized for this replay.')
                auth=item.get('auth',{})
                if auth.get('type','none')!='none' and any(name.lower()=='authorization' for name in request['headers']):raise DiscoveryError('Choose either an Authorization header or an authentication control, not both.')
                if auth.get('type')=='bearer':
                    value=variables.get(auth.get('variable'))
                    if not value:raise DiscoveryError('Bearer variable is missing.')
                    sensitive.add(str(value));request['headers']['Authorization']='Bearer '+str(value)
                elif auth.get('type')=='basic':
                    if parts.scheme!='https' and not configuration.get('allow_local_target'):raise DiscoveryError('Basic authentication requires HTTPS.')
                    user=variables.get(auth.get('username_variable'));password=variables.get(auth.get('password_variable'))
                    if user is None or password is None:raise DiscoveryError('Basic authentication variables are missing.')
                    sensitive.update((str(user),str(password)));value=base64.b64encode((str(user)+':'+str(password)).encode()).decode()
                    sensitive.add(value);request['headers']['Authorization']='Basic '+value
                collect_secrets(request['headers']);collect_secrets(request.get('body'))
                from urllib.parse import parse_qsl
                for name,value in parse_qsl(parts.query):collect_secrets(value,name)
                if sensitive and parts.scheme!='https' and not configuration.get('allow_local_target'):
                    raise DiscoveryError('Secret-bearing requests require HTTPS.')
                result['request']={key:request.get(key) for key in ('method','url','headers','body')}
                attempts=0
                while True:
                    if cancel.is_set():raise DiscoveryError('Execution cancelled.')
                    attempts+=1
                    result['attempts']=attempts
                    try:
                        if configuration.get('target_check'):configuration['target_check'](request['url'])
                        response=transport(request,min(timeout,max(1,deadline-time.monotonic())),cancel,allow_local=configuration.get('allow_local_target',False))
                        break
                    except DiscoveryError:raise
                    except Exception:
                        # Never automatically repeat a mutation or an assertion
                        # failure. Even GET retries require explicit approval.
                        if attempts>retries or request['method'] not in {'GET','HEAD','OPTIONS'} or time.monotonic()>=deadline:raise
                        cancel.wait(min(1,.2*attempts))
                result['attempts']=attempts
                collect_secrets(response.get('headers',{}));collect_secrets(response.get('json'))
                result.update(evaluate(item.get('assertions',[]),response,variables,request=request));result['response']=response
                for rule in item.get('extract',[]):
                    found,value=resolve(response['json'],rule['path'])
                    if not found:
                        result.update(status='BLOCKED',reason='Required extraction field is missing.');break
                    if isinstance(value,(dict,list)) or len(str(value))>8000:
                        result.update(status='BLOCKED',reason='Extraction must produce a bounded scalar variable.');break
                    variables[rule['variable']]=value
                    if is_sensitive_key(rule['variable']) or is_sensitive_key(rule['path']) or rule['variable'] in secret_names:sensitive.add(str(value))
                result['request']={key:request.get(key) for key in ('method','url','headers','body')}
            except DiscoveryError as error:result.update(status='CANCELLED' if cancel.is_set() else 'BLOCKED',reason=str(error))
            except TransportError as error:result.update(status='ERROR',reason=str(error),technical_error='RESPONSE_LIMIT_OR_FORMAT')
            except Exception as error:
                cause=getattr(error,'reason',error)
                category='TLS_ERROR' if isinstance(cause,urllib3.exceptions.SSLError) else 'TIMEOUT' if isinstance(cause,urllib3.exceptions.TimeoutError) else 'NETWORK_OR_RESPONSE_ERROR'
                result.update(status='ERROR',reason={'TLS_ERROR':'TLS certificate validation failed.','TIMEOUT':'HTTP transport timed out.'}.get(category,'HTTP or response processing failed; inspect endpoint availability and configuration.'),technical_error=category)
        clean=sanitize(result,sensitive)
        retained_size=len(json.dumps(clean.get('response',{})).encode())
        if retained_response_bytes+retained_size>2*1024*1024:
            clean['response']={key:value for key,value in clean.get('response',{}).items() if key not in {'json','text_preview'}}
            clean['evidence_notice']='Execution response-evidence budget reached; body omitted. Assertions were evaluated on the complete bounded response before sanitization.'
        else:retained_response_bytes+=retained_size
        outcomes[item['id']]=result['status'];results.append(clean);progress(results[-1])
        if configuration.get('stop_on_failure') and result['status'] in {'FAIL','ERROR','BLOCKED'}:stop=True
        if delay and not cancel.is_set():cancel.wait(delay)
    counts={status:sum(result['status']==status for result in results) for status in ('PASS','FAIL','ERROR','BLOCKED','SKIPPED','CANCELLED','NOT VALIDATED')}
    status='CANCELLED' if cancel.is_set() else 'FAIL' if counts['FAIL'] else 'ERROR' if counts['ERROR'] else 'BLOCKED' if counts['BLOCKED'] else 'NOT VALIDATED' if counts['NOT VALIDATED'] else 'PASS'
    return {'status':status,'results':results,'counts':counts,'duration_ms':round((time.monotonic()-started)*1000,2),
            'executed_by_name':sanitize(str(configuration.get('executed_by_name','')),sensitive),
            'environment':sanitize(str(configuration.get('environment',''))[:100],sensitive), 'collection_id':document.get('collection_id'),'version':document.get('version')}
