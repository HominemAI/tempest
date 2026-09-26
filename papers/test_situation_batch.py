import json
from pathlib import Path
import tempfile
import unittest

from situation_batch import build_body, extract, save, validate


class SituationBatchTests(unittest.TestCase):
    def test_schema_validation(self):
        for value in ({'situations': ['x'] * 5}, {'situations': ['a', 'b']},
                      {'situations': ['a', 'b', 'c', 'd', '']},
                      {'situations': ['a', 'b', 'c', 'd', 5]},
                      {'situations': ['a', 'b', 'c', 'd', 'e'], 'adjustments': []}):
            with self.assertRaises(ValueError):
                validate(value)
        self.assertEqual(len(validate({'situations': ['a', 'b', 'c', 'd', 'e']})), 5)

    def test_request(self):
        body = build_body('Generate five situations.', 'A source event.')
        self.assertNotIn('max_output_tokens', body)
        self.assertEqual(set(body['text']['format']['schema']['properties']), {'situations'})
        self.assertEqual(json.loads(body['input'][1]['content']), {'source_scenario': 'A source event.'})

    def test_extraction_and_overwrite_protection(self):
        with tempfile.TemporaryDirectory() as folder:
            run = Path(folder)
            save(run / 'events.json', [{'custom_id': 'situations-1', 'parent_id': 'crowd-envent-1', 'scenario': 'Original source'}])
            texts = ['My train is cancelled.', 'A parcel arrives at my door.', 'I find my lost keys.', 'The lights go out.', 'A stranger returns my wallet.']
            record = {'custom_id': 'situations-1', 'response': {'status_code': 200, 'body': {
                'status': 'completed', 'model': 'test', 'output': [{'type': 'message', 'content': [
                    {'type': 'output_text', 'text': json.dumps({'situations': texts})}]}]}}}
            (run / 'output.jsonl').write_text(json.dumps(record) + '\n')
            self.assertEqual(extract(run)['accepted_situations'], 5)
            self.assertEqual(len(list((run / 'documents').glob('*.md'))), 5)
            rows = [json.loads(line) for line in (run / 'situations.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['parent_id'], 'crowd-envent-1')
            self.assertEqual(rows[0]['starting_state'], [0] * 23)
            self.assertNotIn('adjustments', rows[0])
            self.assertEqual(extract(run)['accepted_situations'], 5)
            (run / rows[0]['document_path']).write_text('User edit')
            with self.assertRaises(ValueError):
                extract(run)


if __name__ == '__main__':
    unittest.main()
