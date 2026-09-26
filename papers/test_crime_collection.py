"""Offline integrity and provenance checks for the crime literature collection."""
import hashlib
import json
import re
import unittest
from collect_crime_papers import ROOT


class CrimeCollectionTests(unittest.TestCase):
    def setUp(self):
        self.catalog = json.loads((ROOT / 'catalog.json').read_text())

    def test_identifiers_and_provenance(self):
        papers = self.catalog['papers']
        self.assertEqual(self.catalog['count'], len(papers))
        ids = [p.get('doi') or p['seed_id'] for p in papers]
        self.assertEqual(len(ids), len(set(ids)))
        for p in papers:
            self.assertTrue(p['evidence_scope'])
            self.assertTrue(p['screening_note'])
            self.assertTrue((ROOT / p['metadata']).is_file())
            for key in ('markdown', 'source_xml', 'source_html'):
                if p.get(key):
                    path = (ROOT / p[key]).resolve()
                    self.assertTrue(path.is_relative_to(ROOT))
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), p[key + '_sha256'])

    def test_full_text_catalog_and_labels(self):
        full = json.loads((ROOT / 'index-catalog.json').read_text())
        labels = json.loads((ROOT / 'document-labels.json').read_text())['documents']
        self.assertEqual(full['count'], len(full['papers']))
        self.assertEqual(len(full['papers']), self.catalog['counts']['full_text_markdown'])
        for p in full['papers']:
            content = (ROOT / p['markdown']).read_text()
            self.assertGreater(len(content), 5000)
            self.assertNotIn('PARTIAL TEXT ONLY', content)
            self.assertFalse(re.search(r'## Full text\s+PDF\s+', content))
            self.assertIn(p['evidence_scope'], labels[p['markdown']]['labels'])
        self.assertEqual(set(labels), {p['markdown'] for p in full['papers']})

    def test_local_navigation(self):
        for name in ('README.md', 'READING-GUIDE.md', 'ACCESS-GAPS.md'):
            for target in re.findall(r'\]\(([^)]+)\)', (ROOT / name).read_text()):
                if not target.startswith(('https://', 'http://', '#')):
                    self.assertTrue((ROOT / target).is_file(), target)


if __name__ == '__main__':
    unittest.main()
