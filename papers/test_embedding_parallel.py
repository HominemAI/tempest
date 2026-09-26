"""Optional offline integration test; requires FastEmbed and cached model weights."""
import unittest
import numpy as np
from subject_index import HERE, embedder, vectorize, parallel_vectors


class ParallelEmbeddingTests(unittest.TestCase):
    def test_same_vectors_and_order_as_serial(self):
        texts = [f'Document {i}: serotonin, dopamine, and context-dependent behavior.' for i in range(17)]
        expected = list(vectorize(texts, embedder(HERE / '.models'), progress=False))
        actual = list(parallel_vectors(texts, HERE / '.models', 2))
        self.assertEqual(len(actual), len(expected))
        for left, right in zip(expected, actual):
            np.testing.assert_allclose(np.frombuffer(left, dtype='<f4'),
                                       np.frombuffer(right, dtype='<f4'), rtol=1e-5, atol=1e-6)


if __name__ == '__main__':
    unittest.main()
