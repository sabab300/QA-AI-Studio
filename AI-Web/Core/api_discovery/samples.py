"""Opt-in bounded request samples. Credentials become references, never values."""
import json
import re
import math
from urllib.parse import parse_qsl
from .evidence import DiscoveryError

MAX_SAMPLE=65536
PRIVATE=re.compile(r'password|passwd|secret|token|authorization|cookie|session|api.?key|credential|otp|totp|email|phone|mobile|address|passport|national.?id|cnic|birth|dob|ssn|full.?name|first.?name|last.?name|payment|card|cvv|iban|account.?number',re.I)


def sanitize_sample(value):
    from Core.secret_masking import is_sensitive_key
    from Core.ai_platform.sanitize import scrub
    nodes=[0]
    def visit(item,path=(),depth=0):
        nodes[0]+=1
        if depth>8 or nodes[0]>1000:raise DiscoveryError('Request sample exceeded structural limits.')
        if isinstance(item,float) and not math.isfinite(item):raise DiscoveryError('Non-finite JSON values are not valid samples.')
        if isinstance(item,dict):
            if len(item)>60:raise DiscoveryError('Request sample has too many fields.')
            return {str(key):visit(child,path+(str(key),),depth+1) for key,child in item.items()}
        if isinstance(item,list):
            if len(item)>100:raise DiscoveryError('Request sample array exceeds 100 items.')
            return [visit(child,path,depth+1) for child in item]
        if path and any(is_sensitive_key(part) or PRIVATE.search(part) for part in path) and item not in ('',None):
            from .collections import REFERENCE
            if isinstance(item,str) and REFERENCE.fullmatch(item):return item
            name=re.sub('[^A-Za-z0-9_]','_','_'.join(path))[:55]
            if not name or name[0].isdigit():name='field_'+name
            return '{{'+name+'}}'
        if isinstance(item,str):
            if len(item)>8000 or scrub(item,limit=0)!= ' '.join(item.split()):raise DiscoveryError('Request sample contains unsafe free-text credentials or oversized text.')
        return item
    result=visit(value)
    if len(json.dumps(result).encode())>MAX_SAMPLE:raise DiscoveryError('Sanitized request sample exceeds 64 KiB.')
    return result


def parse_sample(text,mime):
    if not isinstance(text,str) or len(text)>MAX_SAMPLE or len(text.encode())>MAX_SAMPLE:
        raise DiscoveryError('Request sample exceeds 64 KiB.')
    mime=str(mime).split(';',1)[0].lower()
    try:
        if 'json' in mime:return sanitize_sample(json.loads(text)),'json'
        if mime=='application/x-www-form-urlencoded':
            pairs=parse_qsl(text,keep_blank_values=True,max_num_fields=60)
            if len({name for name,value in pairs})!=len(pairs):raise DiscoveryError('Repeated form fields require manual configuration.')
            return sanitize_sample(dict(pairs)),'urlencoded'
    except (ValueError,RecursionError):raise DiscoveryError('Request sample is malformed or exceeds safe limits.') from None
    raise DiscoveryError('Only bounded JSON and URL-encoded request samples are supported.')
