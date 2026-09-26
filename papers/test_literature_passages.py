import random
import unittest

from sample_literature_passages import candidates, choose, paragraphs


class SamplingTests(unittest.TestCase):
    def test_boundaries_and_headings(self):
        lines = ['Front matter', '', 'It is a truth universally acknowledged, a story begins.', '',
                 'CHAPTER II', '', 'Another paragraph contains enough words to count.', '',
                 '*** END OF THE PROJECT GUTENBERG EBOOK TEST ***', 'License text']
        blocks = paragraphs(lines, 1342)
        self.assertEqual(blocks[0]['start_line'], 3)
        self.assertFalse(blocks[1]['eligible'])
        self.assertEqual(len(blocks), 3)
        # Cannot combine paragraphs across a heading to meet the minimum.
        self.assertEqual(candidates(blocks, 20, 100, 3), [])

    def test_reproducible_no_overlap(self):
        pool = [dict(start_line=i, end_line=i + 2) for i in range(1, 100)]
        a = choose(pool, 10, random.Random(42))
        self.assertEqual(a, choose(pool, 10, random.Random(42)))
        for i, x in enumerate(a):
            for y in a[i + 1:]:
                self.assertTrue(x['end_line'] < y['start_line'] or y['end_line'] < x['start_line'])

    def test_insufficient_candidates(self):
        with self.assertRaises(ValueError):
            choose([], 20, random.Random(42))


if __name__ == '__main__':
    unittest.main()
