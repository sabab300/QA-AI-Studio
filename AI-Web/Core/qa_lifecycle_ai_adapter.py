"""Task 4 gateway value adapter. No provider imports, routing or network calls.

Transport remains the caller's responsibility and must be separately approved.
This adapter consumes only the existing versioned fact envelope and produces a
proposal payload for the existing authorized lifecycle API; it cannot approve,
execute, or mutate a lifecycle record.
"""
import json
import re
from Core.qa_lifecycle_repository import LifecycleError


class QaLifecycleAiAdapter:
    CONTRACT_VERSION=1

    @classmethod
    def build_request(cls, context):
        if not isinstance(context,dict) or type(context.get('contract_version')) is not int or context.get('contract_version')!=cls.CONTRACT_VERSION or context.get('automatic_approval_allowed') is not False:
            raise LifecycleError('Unsupported lifecycle recommendation contract.',400)
        identifiers=context.get('requirement_ids')
        if not isinstance(identifiers,list) or len(identifiers)>100 or any(not isinstance(value,str) or not value or len(value)>100 for value in identifiers):
            raise LifecycleError('Recommendation context must contain at most 100 bounded requirement identities.',400)
        if not re.fullmatch(r'[a-f0-9]{64}',str(context.get('content_hash',''))):
            raise LifecycleError('A valid current context signature is required.',400)
        if not identifiers:
            raise LifecycleError('No confirmed requirements are available for recommendation.',409)
        # No documents, credentials, execution payloads or free-text QA data.
        facts={key:context[key] for key in ('contract_version','content_hash','requirement_ids')}
        # Existing Task 4 requirement-matching route; do not invent a new task
        # vocabulary that its settings validator cannot configure.
        return {'task':'impact_analysis','prompt':json.dumps(facts,sort_keys=True),
                'system':'Return one JSON object with requirement_id and rationale. Use only supplied identities. If there is insufficient evidence, return {"abstain":true}. Never approve or execute a change.',
                'data_class':'private','mode':'local','params':{'temperature':0,'max_tokens':512}}

    @classmethod
    def proposal_payload(cls, context, result, provider_reference):
        cls.build_request(context)
        # GenerationResult is the Task 4 public value contract. No repr/errors
        # containing provider response text are logged or propagated.
        if getattr(result,'cloud',None) is not False:
            raise LifecycleError('Only explicitly local recommendation results are permitted.',403)
        text=getattr(result,'text',None)
        if not isinstance(text,str) or len(text)>8000:
            raise LifecycleError('Provider returned an invalid or oversized recommendation.',400)
        try:value=json.loads(text)
        except (ValueError,TypeError):raise LifecycleError('Provider did not return a valid recommendation object.',400) from None
        if not isinstance(value,dict):raise LifecycleError('Provider did not return a recommendation object.',400)
        if value=={'abstain':True}:return None
        if set(value)!={'requirement_id','rationale'} or value['requirement_id'] not in context['requirement_ids']:
            raise LifecycleError('Provider recommendation references unsupported fields or an unknown requirement.',400)
        rationale=value['rationale']
        if not isinstance(rationale,str) or not rationale.strip() or len(rationale)>4000:
            raise LifecycleError('Provider recommendation requires a bounded rationale.',400)
        if not isinstance(provider_reference,str) or not provider_reference.strip() or len(provider_reference)>250:
            raise LifecycleError('A bounded provider traceability reference is required.',400)
        return {'requirement_id':value['requirement_id'],'rationale':rationale,
                'content_hash':context['content_hash'],'provider_reference':provider_reference}
