"""Execute actual cache/filter methods without loading unrelated AI models."""
import ast
import json
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[2]


def method(relative,name):
    tree=ast.parse((ROOT/relative).read_text(encoding='utf-8'))
    node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name==name)
    node.decorator_list=[]
    env={'json':json}
    exec(compile(ast.Module(body=[node],type_ignores=[]),relative,'exec'),env)
    return env[name]


class RetrievalScopeTests(unittest.TestCase):
    def test_web_cache_document_type_and_identity(self):
        rag=method('AI-Web/Core/rag_engine.py','_cache_key')
        retrieval=method('AI-Web/Core/retrieval_engine.py','_cache_key')
        for function,prefix in ((rag,(None,'question',5,'D','M','K','1.0')),(retrieval,(None,'question','D','M','K','1.0'))):
            first=function(*prefix,document_type='SRS',document_version_id=1)
            second=function(*prefix,document_type='CRF',document_version_id=2)
            self.assertNotEqual(first,second)
            self.assertEqual(first,function(*prefix,document_type='SRS',document_version_id=1))
    def test_desktop_cache_document_type(self):
        cache=method('AI/Core/rag_engine.py','_cache_key')
        self.assertNotEqual(cache(None,'question',5,'D','M','K','1.0',document_type='SRS'),cache(None,'question',5,'D','M','K','1.0',document_type='CRF'))
    def test_filter_includes_document_version_parent_scope(self):
        for file in ('AI-Web/Core/metadata_filter.py','AI/Core/metadata_filter.py'):
            build=method(file,'build')
            result=build(None,domain='D',module='M',knowledge_name='K',version='1.0',document_type='CRF',document_version_id=2)
            self.assertEqual(result['document_type'],'CRF');self.assertEqual(result['document_version_id'],2)


if __name__=='__main__': unittest.main()
