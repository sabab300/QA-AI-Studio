"""Declarative, bounded business assertions; never evaluate user code."""
import json
import math
import re
from .evidence import DiscoveryError

TYPES={'status_equals','status_in','response_time_lte','header_exists','header_equals','content_type',
       'exists','equals','not_equals','contains','not_contains','type','not_null','not_empty',
       'minimum','maximum','greater_than','less_than','regex','array_length','schema','variable_equals'}
PATH=re.compile(r'^\$(?:\.[A-Za-z_][A-Za-z0-9_-]*|\[\d{1,5}\])*$')


def resolve(data,path):
    """Reuse the existing API engine's non-executable JSON field resolver."""
    if not isinstance(path,str) or len(path)>256 or not PATH.fullmatch(path):
        raise DiscoveryError('Use a bounded JSONPath such as $.record.id or $.items[0].id.')
    from Core.api_automation_runner import ApiAutomationRunner
    return ApiAutomationRunner._resolve_json_path(data,path)


def validate(assertions):
    if not isinstance(assertions,list) or len(assertions)>100:
        raise DiscoveryError('At most 100 assertions per API are supported.')
    ids=set()
    for item in assertions:
        if not isinstance(item,dict) or item.get('type') not in TYPES:
            raise DiscoveryError('Unsupported assertion type.')
        if set(item)-{'id','type','path','expected','enabled','description','severity','source','operator','area'}:
            raise DiscoveryError('Unsupported assertion configuration fields.')
        if item.get('area','response') not in {'response','request','query','request_headers','response_headers','variables'}:raise DiscoveryError('Unsupported assertion area.')
        from Core.secret_masking import is_sensitive_key
        if is_sensitive_key(str(item.get('path',''))) and item['type'] in {'equals','not_equals','contains','not_contains','regex'}:
            raise DiscoveryError('Do not persist secret comparison values; use a runtime variable comparison or presence/type checks.')
        identity=str(item.get('id',''))
        if not identity or len(identity)>64 or identity in ids:
            raise DiscoveryError('Assertions require distinct stable IDs.')
        ids.add(identity)
        if not isinstance(item.get('enabled',True),bool):raise DiscoveryError('Assertion enabled must be boolean.')
        expected=item.get('expected');kind=item['type']
        if kind=='status_equals' and (type(expected) is not int or not 100<=expected<=599):raise DiscoveryError('Expected status must be an HTTP status integer.')
        if kind=='status_in' and (not isinstance(expected,list) or not 1<=len(expected)<=30 or any(type(code) is not int or not 100<=code<=599 for code in expected)):
            raise DiscoveryError('Expected statuses must contain between 1 and 30 valid HTTP status integers.')
        if kind in {'response_time_lte','minimum','maximum','greater_than','less_than'} and (type(expected) not in (int,float) or not math.isfinite(expected) or (kind=='response_time_lte' and expected<0)):
            raise DiscoveryError('Expected numeric threshold is invalid.')
        if kind=='array_length' and (type(expected) is not int or not 0<=expected<=100000):raise DiscoveryError('Array length must be a bounded nonnegative integer.')
        if kind=='type' and expected not in {'null','boolean','integer','number','string','array','object'}:raise DiscoveryError('Unsupported JSON data type.')
        if kind in {'header_exists','header_equals'} and not re.fullmatch(r'[A-Za-z0-9!#$%&\'*+.^_`|~-]{1,100}',str(item.get('path',''))):raise DiscoveryError('Invalid response header name.')
        if kind=='content_type' and (not isinstance(expected,str) or len(expected)>100 or '/' not in expected):raise DiscoveryError('Expected content type is invalid.')
        if len(json.dumps(item))>16000:raise DiscoveryError('Assertion exceeds its size limit.')
        if item['type'] not in {'status_equals','status_in','response_time_lte','header_exists','header_equals','content_type'}:
            resolve({},item.get('path','$'))
        if item['type']=='regex':
            pattern=item.get('expected')
            if not isinstance(pattern,str) or len(pattern)>256:raise DiscoveryError('Regex is limited to 256 characters.')
            try:
                import regex
                regex.compile(pattern)
            except Exception:
                raise DiscoveryError('Regex support unavailable or invalid pattern.') from None
        if item['type']=='schema':
            schema=item.get('expected')
            if not isinstance(schema,dict) or len(json.dumps(schema))>8000:raise DiscoveryError('Use a bounded inline JSON schema.')
            # No external/local $ref fetching through schema validation.
            def references(value,depth=0):
                if depth>20:raise DiscoveryError('Schema nesting exceeds 20 levels.')
                if isinstance(value,dict):
                    supported={'$schema','type','properties','required','items','additionalProperties','minProperties','maxProperties','minItems','maxItems','minLength','maxLength','minimum','maximum','exclusiveMinimum','exclusiveMaximum','enum','const','description','title'}
                    if set(value)-supported:raise DiscoveryError('Use a bounded structural schema; references, regex, format checks and combinators are not supported here.')
                    properties=value.get('properties',{})
                    if not isinstance(properties,dict):raise DiscoveryError('Schema properties must be an object.')
                    for name,child in properties.items():
                        if is_sensitive_key(str(name)) and isinstance(child,dict) and {'const','enum'}&set(child):
                            raise DiscoveryError('Do not persist secret literal values in schema definitions.')
                        references(child,depth+1)
                    if isinstance(value.get('items'),dict):references(value['items'],depth+1)
                    if isinstance(value.get('additionalProperties'),dict):references(value['additionalProperties'],depth+1)
            references(schema)
            try:
                from jsonschema import Draft202012Validator
                Draft202012Validator.check_schema(schema)
            except Exception:raise DiscoveryError('Invalid or unsupported JSON schema.') from None
    return assertions


def evaluate(assertions,response,variables=None,request=None):
    validate(assertions);variables=variables or {};out=[]
    body=response.get('json');headers={str(k).lower():str(v) for k,v in response.get('headers',{}).items()}
    for item in assertions:
        if not item.get('enabled',True):continue
        kind=item['type'];expected=item.get('expected');actual=None;found=True;passed=False
        area=item.get('area','response');target=body;selected_headers=headers
        if area=='request':
            target=(request or {}).get('body')
            if isinstance(target,str):
                try:target=json.loads(target)
                except ValueError:pass
        elif area=='query':
            from urllib.parse import urlsplit,parse_qsl
            target=dict(parse_qsl(urlsplit((request or {}).get('url','')).query,keep_blank_values=True,max_num_fields=100))
        elif area=='variables':target=variables
        elif area=='request_headers':
            selected_headers={str(k).lower():str(v) for k,v in (request or {}).get('headers',{}).items()};target=selected_headers
        elif area=='response_headers':target=headers
        try:
            if kind in {'status_equals','status_in'}:
                actual=response.get('status');passed=actual==expected if kind=='status_equals' else isinstance(expected,list) and actual in expected
            elif kind=='response_time_lte':
                actual=response.get('duration_ms');passed=actual is not None and float(actual)<=float(expected)
            elif kind in {'header_exists','header_equals','content_type'}:
                name='content-type' if kind=='content_type' else str(item.get('path','')).lower()
                found=name in selected_headers;actual=selected_headers.get(name)
                passed=found if kind=='header_exists' else found and (actual.split(';')[0].strip()==expected if kind=='content_type' else actual==str(expected))
            else:
                found,actual=resolve(target,item.get('path','$'))
                if kind=='exists':passed=found
                elif kind=='equals':passed=found and type(actual)==type(expected) and actual==expected
                elif kind=='not_equals':passed=found and (type(actual)!=type(expected) or actual!=expected)
                elif kind=='contains':passed=found and isinstance(actual,(str,list,dict)) and expected in actual
                elif kind=='not_contains':passed=found and isinstance(actual,(str,list,dict)) and expected not in actual
                elif kind=='not_null':passed=found and actual is not None
                elif kind=='not_empty':passed=found and actual is not None and actual not in ('',[],{})
                elif kind=='type':passed=found and {'null':actual is None,'boolean':isinstance(actual,bool),'integer':type(actual) is int,'number':type(actual) in (int,float),'string':isinstance(actual,str),'array':isinstance(actual,list),'object':isinstance(actual,dict)}.get(expected,False)
                elif kind=='minimum':passed=found and type(actual) in (int,float) and actual>=float(expected)
                elif kind=='maximum':passed=found and type(actual) in (int,float) and actual<=float(expected)
                elif kind=='greater_than':passed=found and type(actual) in (int,float) and actual>float(expected)
                elif kind=='less_than':passed=found and type(actual) in (int,float) and actual<float(expected)
                elif kind=='array_length':passed=found and isinstance(actual,list) and len(actual)==int(expected)
                elif kind=='variable_equals':
                    passed=found and str(expected) in variables and actual==variables[str(expected)]
                elif kind=='regex':
                    import regex
                    passed=found and isinstance(actual,str) and len(actual)<=16000 and bool(regex.search(expected,actual,timeout=.025))
                elif kind=='schema':
                    from jsonschema import Draft202012Validator
                    pending=[(actual,0)];nodes=0;bounded=True
                    while pending:
                        value,depth=pending.pop();nodes+=1
                        if nodes>5000 or depth>32:bounded=False;break
                        if isinstance(value,dict):pending.extend((child,depth+1) for child in value.values())
                        elif isinstance(value,list):pending.extend((child,depth+1) for child in value)
                    passed=found and bounded and Draft202012Validator(expected).is_valid(actual)
        except (TypeError,ValueError,KeyError,TimeoutError):passed=False
        out.append({'id':item['id'],'type':kind,'area':area,'description':str(item.get('description',''))[:300],
                    'status':'PASS' if passed else 'FAIL','expected':expected,'actual':actual,
                    'found':found,'reason':'' if passed else 'Configured assertion was not satisfied.'})
    return {'status':('FAIL' if any(x['status']=='FAIL' for x in out) else 'PASS') if out else 'NOT VALIDATED','assertions':out}
