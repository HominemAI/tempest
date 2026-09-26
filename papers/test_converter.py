"""Run with: python -m unittest discover -s papers -p test_converter.py"""
import json
from pathlib import Path
import tempfile
import unittest

from convert_papers import destination, plan, render_markdown, synchronize


class ConverterTests(unittest.TestCase):
    def test_destination_is_idempotent(self):
        path = Path('/library/topic/paper.pdf')
        self.assertEqual(destination(path), Path('/library/topic/paper/paper.pdf'))
        self.assertEqual(destination(destination(path)), destination(path))

    def test_one_section_per_page_including_empty_page(self):
        output = render_markdown('Example\nPaper', 'paper.pdf', ['First', '', 'Third'])
        self.assertIn('# Example Paper', output)
        self.assertEqual(output.count('\n## Page '), 3)
        self.assertEqual(output.count('\n---\n'), 3)
        self.assertIn('No extractable text', output)
        self.assertIn('[Original PDF](paper.pdf)', output)

    def test_collision_is_rejected_before_moving(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / 'example.pdf'
            source.touch()
            target = destination(source)
            target.parent.mkdir()
            target.touch()
            with self.assertRaises(FileExistsError):
                plan(root)
            self.assertTrue(source.exists())

    def test_ocr_is_disclosed_per_document(self):
        output = render_markdown('Example', 'paper.pdf', ['Native', 'Recognized'], [2])
        self.assertIn('Local OCR was used on PDF pages 2.', output)
        self.assertNotIn('No OCR was used.', output)

    def test_plan_does_not_mutate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            source = root / 'example.pdf'
            source.touch()
            self.assertEqual(plan(root), [(source, destination(source))])
            self.assertFalse(destination(source).parent.exists())

    def test_catalog_and_links_repair_after_interrupted_run(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp).resolve()
            catalog = {'papers': [{'file': 'topic/example.pdf', 'title': 'Example'}]}
            (root / 'catalog.json').write_text(json.dumps(catalog))
            (root / 'README.md').write_text('[Local PDF](topic/example.pdf)')
            target = root / 'topic/example/example.pdf'
            # Source is already nested, while catalog still has the old path.
            result = {'source': str(target), 'target': str(target), 'pdf_sha256': 'abc'}
            synchronize(root, catalog, [result])
            once = (root / 'README.md').read_text()
            self.assertIn('[Markdown](topic/example/example.md)', once)
            self.assertEqual(catalog['papers'][0]['file'], 'topic/example/example.pdf')
            synchronize(root, catalog, [result])
            self.assertEqual((root / 'README.md').read_text(), once)


if __name__ == '__main__':
    unittest.main()
