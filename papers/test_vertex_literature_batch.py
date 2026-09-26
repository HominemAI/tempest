import unittest

from vertex_literature_batch import pair_passages, validate_output


class BatchTests(unittest.TestCase):
    def test_exclusive_reproducible_pairs(self):
        rows = [dict(id=str(i), document_path=f'book/{i}.txt') for i in range(600)]
        pairs = pair_passages(rows, 42)
        self.assertEqual(len(pairs), 300)
        self.assertEqual(len({r['id'] for pair in pairs for r in pair}), 600)
        self.assertTrue(all(len(pair) == 2 for pair in pairs))
        self.assertEqual(pairs, pair_passages(rows, 42))
        self.assertNotEqual(pairs, pair_passages(rows, 43))
        with self.assertRaises(ValueError):
            pair_passages(rows[:-1], 42)
        with self.assertRaises(ValueError):
            pair_passages(rows[:-1] + [rows[0]], 42)

    def test_output_validation(self):
        valid = dict(request_id='test', situations=[f'Situation {i}' for i in range(10)])
        self.assertEqual(len(validate_output(valid, 'test')), 10)
        for bad in (dict(valid, request_id='wrong'), dict(valid, situations=['a'] * 10),
                    dict(valid, situations=['a']), dict(valid, adjustments=[])):
            with self.assertRaises(ValueError):
                validate_output(bad, 'test')


if __name__ == '__main__':
    unittest.main()
