import argparse
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

from pipeline import DIMENSIONS, Regressor, encode, load_records, metrics, neutral, split_groups, train, vector


class PipelineTests(unittest.TestCase):
    def test_validation(self):
        for bad in ([0] * 22, [True] * 23, [float('nan')] * 23, ['0'] * 23):
            with self.assertRaises(ValueError):
                vector(bad)
        with self.assertRaises(ValueError):
            neutral([.1] * 23)
        self.assertEqual(neutral([0] * 23).shape, (23,))

    def test_group_splits(self):
        groups = np.repeat(np.arange(30), 2)
        splits = split_groups(groups, 42)
        self.assertEqual(len(set(np.concatenate(splits))), 60)
        for i, a in enumerate(splits):
            for b in splits[i + 1:]:
                self.assertFalse(set(groups[a]) & set(groups[b]))
        for a, b in zip(splits, split_groups(groups, 42)):
            np.testing.assert_array_equal(a, b)

    def test_teacher_fields_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'labels.jsonl'
            rows = [dict(custom_id=str(i), scenario=f'Event {i}', perspective='narrator',
                         starting_state=[0] * 23, source_annotations={'emotion': 'SECRET'},
                         label={'adjustments': [.1] * 23, 'extras': {'SECRET': 'SECRET'}}) for i in range(20)]
            path.write_text('\n'.join(json.dumps(r) for r in rows))
            records = load_records(path)
            self.assertNotIn('SECRET', str(records))

    def test_nonzero_opt_in_and_family_groups(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'labels.jsonl'
            rows=[dict(custom_id=str(i),scenario=f'Event {i}',perspective='narrator',split_group=f'family-{i//2}',
                       starting_state=[1 if i%2 else 0]*23,label={'adjustments':[.1]*23}) for i in range(24)]
            path.write_text('\n'.join(json.dumps(r) for r in rows))
            with self.assertRaises(ValueError):load_records(path)
            records=load_records(path,allow_nonzero=True)
            self.assertEqual(records[0]['group'],records[1]['group'])
            self.assertEqual(float(records[1]['state'][0]),1)
            # Duplicate text connects different source families, not only states.
            rows[2]['scenario']=rows[0]['scenario']
            path.write_text('\n'.join(json.dumps(r) for r in rows))
            records=load_records(path,allow_nonzero=True)
            self.assertEqual(records[0]['group'],records[3]['group'])

    def test_long_text_is_not_silently_truncated(self):
        class Encoder:
            max_seq_length = 2
            def tokenizer(self, text, truncation):
                return {'input_ids': [1, 2, 3]}
        with self.assertRaises(ValueError):
            encode(Encoder(), ['too long'])

    def test_unbounded_outputs(self):
        model = Regressor(8)
        with torch.no_grad():
            model.layers[-1].weight.zero_()
            model.layers[-1].bias.fill_(-2)
        output = model(torch.zeros(1, 8), torch.zeros(1, 23))
        self.assertTrue(torch.all(output == -2))

    def test_metrics(self):
        y = np.zeros((2, 23))
        result = metrics(y, y)
        self.assertEqual(result['mae'], 0)
        self.assertIsNone(result['sign_accuracy_at_target_magnitude_01'])

    def test_training_and_checkpoint_roundtrip(self):
        # Synthetic vectors test machinery only, not language understanding.
        with tempfile.TemporaryDirectory() as tmp:
            data, out = Path(tmp) / 'data', Path(tmp) / 'model'
            data.mkdir()
            rng = np.random.default_rng(42)
            e = rng.normal(size=(160, 8)).astype(np.float32)
            y = (e[:, :1] * np.linspace(-.2, .2, 23)[None]).astype(np.float32)
            tr, va, te = split_groups(np.arange(160), 42)
            np.savez_compressed(data / 'data.npz', embeddings=e, states=np.zeros((160, 23), dtype=np.float32),
                                targets=y, train=tr, val=va, test=te)
            meta = dict(dimensions=DIMENSIONS, embedding_dim=8, neutral_only=True,
                        data_sha256=hashlib.sha256((data / 'data.npz').read_bytes()).hexdigest())
            (data / 'metadata.json').write_text(json.dumps(meta))
            args = argparse.Namespace(data=str(data), out=str(out), seed=42, threads=1,
                                      epochs=60, patience=10, lr=.003, batch_size=32)
            train(args)
            report = json.loads((out / 'metrics.json').read_text())
            self.assertLess(report['model']['mae'], report['zero_baseline']['mae'])
            model = Regressor(8)
            model.load_state_dict(torch.load(out / 'weights.pt', weights_only=True))
            self.assertTrue(torch.isfinite(model(torch.from_numpy(e), torch.zeros(160, 23))).all())
            # Corruption must invalidate cached artifacts.
            with (data / 'data.npz').open('ab') as f:
                f.write(b'changed')
            with self.assertRaises(ValueError):
                train(args)


if __name__ == '__main__':
    unittest.main()
