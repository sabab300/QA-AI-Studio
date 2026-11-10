"""Opt-in CDP samples with bounded Chromium buffers, never response.body().

CDP evicts oversized resources before Python asks for their body. Failed or
evicted bodies are explicitly incomplete and never become expected results.
"""
import base64
import json
from datetime import datetime,timezone
from urllib.parse import parse_qsl,urlsplit,urlencode
from .evidence import DiscoveryError,normalize_entry,safe_url,shape
from .samples import MAX_SAMPLE,parse_sample,sanitize_sample,PRIVATE


def headers(values,request=False):
    result={}
    for name,value in list(values.items())[:60]:
        name=str(name)
        if len(name)>100 or name.lower().startswith('sec-') or name.lower() in {'host','content-length','connection','transfer-encoding','proxy-authorization','accept-encoding'}:continue
        if request and PRIVATE.search(name):
            result[name]='{{'+''.join(c if c.isalnum() else '_' for c in name.lower())+'}}'
        elif not PRIVATE.search(name):
            try:
                value=str(value)[:2000]
                if value.startswith(('http://','https://')):value=safe_url(value)
                result[name]=sanitize_sample({name:value})[name]
            except DiscoveryError:continue
    return result


class BrowserSamples:
    def __init__(self,capture):
        self.capture=capture;self.sessions=[];self.used=0;self.order=0
        self.budget=2*1024*1024;self.peak_pending=0

    def attach(self,page):
        if len(self.sessions)>=4:
            self.capture._error=DiscoveryError('Recording reached its four-page limit. Committed evidence remains available.');self.capture.stop_event.set();return
        client=page.context.new_cdp_session(page);self.sessions.append(client)
        pending={}
        client.send('Network.enable',{'maxTotalBufferSize':4*MAX_SAMPLE,'maxResourceBufferSize':MAX_SAMPLE,'maxPostDataSize':MAX_SAMPLE})
        def started(event):
            key=event['requestId']
            if event.get('redirectResponse') and key in pending:
                item=pending.pop(key);item['response']=event['redirectResponse'];self.persist(item,None,'Redirect body not sampled.')
            if event.get('type') not in {'XHR','Fetch'} or self.capture.paused.is_set() or self.capture.stop_event.is_set():return
            if len(pending)>=64:
                self.capture._error=DiscoveryError('Capture reached its 64 in-flight API limit. Committed evidence remains available.');self.capture.stop_event.set();return
            self.order+=1
            raw=event['request']
            # Copy only bounded request fields; do not retain full CDP events.
            pending[key]={'request':{k:raw[k] for k in ('method','url','hasPostData') if k in raw},
                'timestamp':event.get('wallTime'),'start':event.get('timestamp',0),'order':self.order,'page':safe_url(page.url),
                'action':self.capture.current_action}
            pending[key]['request']['headers']=headers(raw.get('headers',{}),True)
            pending[key]['header_omitted']=any(len(str(value))>2000 for value in raw.get('headers',{}).values())
            post=raw.get('postData')
            if isinstance(post,str) and len(post)<=MAX_SAMPLE:pending[key]['request']['postData']=post
            self.peak_pending=max(self.peak_pending,len(pending))
        def received(event):
            if event['requestId'] in pending:
                response=event['response'];pending[event['requestId']]['response']={k:response[k] for k in ('status','headers','mimeType') if k in response}
        def finished(event):
            item=pending.pop(event['requestId'],None)
            if not item:return
            item['end']=event.get('timestamp',item['start'])
            response=item.get('response',{});mime=str(response.get('mimeType','')).lower()
            text=None;notice='Binary/non-JSON response body not sampled.'
            if 'json' in mime or mime=='application/x-www-form-urlencoded':
                if self.used>=self.budget:notice='Session sample budget reached; response omitted.'
                else:
                    try:
                        data=client.send('Network.getResponseBody',{'requestId':event['requestId']})
                        body=data.get('body','')
                        if len(body)>MAX_SAMPLE*(4/3)+8:raise DiscoveryError('Oversized body')
                        payload=base64.b64decode(body,validate=True) if data.get('base64Encoded') else body.encode('utf-8')
                        if len(payload)>MAX_SAMPLE:raise DiscoveryError('Oversized body')
                        text=payload.decode('utf-8');notice=''
                    except Exception:notice='Response exceeds 64 KiB, was evicted, or could not be safely read; incomplete sample omitted.'
            elif int(response.get('status',0)) in {204,304}:notice='Empty response.'
            self.persist(item,text,notice)
        def failed(event):
            item=pending.pop(event['requestId'],None)
            if item:
                item['network_error']=True;self.persist(item,None,'Network failure; no complete response body.')
        client.on('Network.requestWillBeSent',started);client.on('Network.responseReceived',received)
        client.on('Network.loadingFinished',finished);client.on('Network.loadingFailed',failed)

    def persist(self,item,text,notice):
        capture=self.capture
        if capture.paused.is_set() or capture.stop_event.is_set():return
        try:
            request=item['request'];response=item.get('response',{})
            entry={'request':{'method':request['method'],'url':request['url'],'headers':[{'name':k,'value':v} for k,v in request.get('headers',{}).items()]},
                'response':{'status':int(response.get('status',0)),'content':{'mimeType':response.get('mimeType','')}}}
            evidence=normalize_entry(entry,'Browser',item['action'])
            evidence.update(request_order=item['order'],captured_at=datetime.fromtimestamp(item['timestamp'],timezone.utc).isoformat() if item.get('timestamp') else datetime.now(timezone.utc).isoformat(),
                source_page=item['page'],duration_ms=max(0,(item.get('end',item['start'])-item['start'])*1000),
                traffic_classification='First-party' if urlsplit(request['url']).hostname==capture.host else 'Third-party (blocked)',
                request_headers=headers(request.get('headers',{}),True),response_headers=headers(response.get('headers',{})),
                sample_complete=False,sample_notice=notice,network_error=bool(item.get('network_error')),
                blocked_resource_origins=sorted(capture.blocked_resources))
            evidence['requires_configuration']=bool(item.get('header_omitted'))
            try:
                evidence['query_sample']=[{'name':k,'value':sanitize_sample({k:v})[k]} for k,v in parse_qsl(urlsplit(request['url']).query,keep_blank_values=True,max_num_fields=60)]
                evidence['request_url']=evidence['observed_url'].split('?',1)[0]+('?' +urlencode([(pair['name'],pair['value']) for pair in evidence['query_sample']],safe='{}') if evidence['query_sample'] else '')
            except (ValueError,DiscoveryError):
                evidence['requires_configuration']=True;evidence['limitations'].append('Query values omitted: safe sampling limits exceeded.')
            if request.get('hasPostData') or 'postData' in request:
                try:
                    mime=next((v for k,v in request.get('headers',{}).items() if k.lower()=='content-type'),'')
                    body,mode=parse_sample(request.get('postData',''),mime);evidence.update(request_sample=body,request_body_mode=mode)
                except DiscoveryError:
                    evidence['requires_configuration']=True;evidence['limitations'].append('Request body omitted: configure before replay.')
            if text is not None:
                try:
                    if not text.strip():
                        evidence['sample_notice']='Empty response; no JSON body.'
                        raise DiscoveryError('Empty response')
                    sample,mode=parse_sample(text,response.get('mimeType',''))
                    evidence.update(response_sample=sample,response_schema=shape(sample),sample_complete=True,sample_notice='Safe sample captured; sensitive fields replaced with references.')
                except DiscoveryError:
                    if text.strip():evidence['sample_notice']='Invalid JSON/form or structural limit; body omitted.'
            evidence['response_truncated']=bool(text is None and '64 KiB' in notice)
            # Metadata-only defaults must not imply that the sample was read.
            evidence['limitations']=[value for value in evidence['limitations'] if 'Field values omitted' not in value]
            size=len(json.dumps(evidence).encode())
            if self.used+size>self.budget:
                for field in ('request_sample','response_sample','query_sample'):evidence.pop(field,None)
                evidence.update(requires_configuration=True,sample_complete=False,sample_notice='Session sample budget exceeded; samples omitted.')
            else:self.used+=size
            capture.repository.append(capture.identity,capture.owner,evidence)
        except MemoryError:
            capture._error=DiscoveryError('Recording memory allocation failed. Safely committed evidence is recoverable.');capture.stop_event.set()
        except Exception:
            capture._error=DiscoveryError('API sample could not be safely captured. Committed evidence remains available.');capture.stop_event.set()

    def close(self):
        for client in self.sessions:
            try:client.detach()
            except Exception:pass
        self.sessions.clear()
