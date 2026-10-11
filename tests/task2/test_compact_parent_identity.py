from pathlib import Path
import tempfile
import unittest
from Shared.central_repository import CentralRepository


class CompactParentTests(unittest.TestCase):
    def test_long_names_and_sanitized_names_never_merge(self):
        with tempfile.TemporaryDirectory(prefix='task2-parent-') as directory:
            central=CentralRepository(directory)
            common={'module':'Module','knowledge_name':'Knowledge','version':'1.0','document_type':'CRF','source_type':'Files'}
            first=central.hierarchy_root(domain='Hierarchy Verify alpha',**common)
            second=central.hierarchy_root(domain='Hierarchy Verify beta',**common)
            self.assertNotEqual(first,second)
            self.assertEqual(first,central.hierarchy_root(domain='Hierarchy Verify alpha',**common))
            self.assertNotEqual(central.hierarchy_root(domain='A:B',**common),central.hierarchy_root(domain='A?B',**common))
            self.assertTrue(all(len(part)<=14 for part in first.relative_to(central.root).parts))

if __name__=='__main__':unittest.main()
