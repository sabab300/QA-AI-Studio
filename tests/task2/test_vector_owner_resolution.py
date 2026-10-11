"""Fail-closed legacy source vector matching with authoritative stable IDs."""
import unittest
from Shared.vector_ownership import resolve_source_vector_owner

class VectorOwnerTests(unittest.TestCase):
    def setUp(self):
        self.first=dict(id=1,domain='D',module='M',knowledge_name='K',document_type='CRF',version='1.0',file_name='requirement.txt',sha256='abc')
        self.second=dict(self.first,id=2,document_type='SRS')
    def test_stable_id_overrides_stale_label_aliases(self):
        self.assertEqual(resolve_source_vector_owner([self.first,self.second],'abc-chunk',{'knowledge_id':2}),self.second)
    def test_legacy_exact_type_and_hash_are_required(self):
        self.assertEqual(resolve_source_vector_owner([self.first,self.second],'abc-chunk',self.first),self.first)
        self.assertIsNone(resolve_source_vector_owner([self.first,self.second],'wrong-chunk',self.first))
    def test_invalid_explicit_id_never_rebinds_to_another_source(self):
        self.assertIsNone(resolve_source_vector_owner([self.first],'abc-chunk',dict(self.first,knowledge_id=99)))
    def test_ambiguous_legacy_metadata_is_rejected(self):
        self.assertIsNone(resolve_source_vector_owner([self.first,dict(self.first,id=3)],'abc-chunk',self.first))

if __name__=='__main__':unittest.main()
