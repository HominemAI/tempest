"""Local validation for the collected literature; no network requests."""
import hashlib
import json
from pathlib import Path
import re
import unittest

from collect_biochemical_papers import ArticleText, ROOT


class CollectionTests(unittest.TestCase):
    def test_catalog_sources_and_hashes(self):
        catalog = json.loads((ROOT / 'catalog.json').read_text())
        self.assertEqual(catalog['count'], len(catalog['papers']))
        identifiers = [p.get('doi') or p['seed_id'] for p in catalog['papers']]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        for paper in catalog['papers']:
            for field, digest_field in [('file', 'pdf_sha256'), ('markdown', 'markdown_sha256'),
                                        ('source_xml', 'source_xml_sha256'), ('source_html', 'source_html_sha256')]:
                if paper.get(field):
                    source = ROOT / paper[field]
                    self.assertTrue(source.resolve().is_relative_to(ROOT.resolve()))
                    self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), paper[digest_field])
            if paper.get('file'):
                self.assertTrue((ROOT / paper['file']).read_bytes().startswith(b'%PDF-'))

    def test_index_catalog_excludes_unavailable_text(self):
        catalog = json.loads((ROOT / 'index-catalog.json').read_text())
        labels = json.loads((ROOT / 'document-labels.json').read_text())['documents']
        self.assertEqual(catalog['count'], len(catalog['papers']))
        for paper in catalog['papers']:
            self.assertGreater(len((ROOT / paper['markdown']).read_text()), 1000)
            self.assertTrue(labels[paper['markdown']]['labels'])

    def test_navigation_links(self):
        for name in ('README.md', 'READING-GUIDE.md', 'ACCESS-GAPS.md'):
            for target in re.findall(r'\]\(([^)]+)\)', (ROOT / name).read_text()):
                if not target.startswith(('https://', 'http://', '#')):
                    self.assertTrue((ROOT / target).exists(), target)

    def test_html_extractor_ignores_page_chrome(self):
        parser = ArticleText()
        parser.feed('<nav>ignore</nav><article><h2>Abstract</h2><p>A <i>real</i> paragraph.</p>'
                    '<button>ignore button</button></article><footer>ignore footer</footer>')
        result = ''.join(parser.parts)
        self.assertIn('A real paragraph.', result)
        self.assertNotIn('ignore', result)


if __name__ == '__main__':
    unittest.main()
