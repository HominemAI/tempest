import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import personality_app as app


class PersonalityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.patch = patch.object(app,'DATA',Path(self.temp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.temp.cleanup()

    def test_create_edit_and_history(self):
        record = app.save_record(dict(name='Test',body=dict(state_values=[0]*23,description='Neutral')))
        identifier = record['id']
        record['body']['state_values'][0] = 2.5
        edited = app.save_record(record)
        self.assertEqual(edited['id'],identifier)
        self.assertEqual(edited['revision'],2)
        self.assertEqual(edited['body']['state_values'][0],2.5)
        with app.db() as connection:
            old = connection.execute('SELECT body FROM history WHERE id=?',(identifier,)).fetchone()
        self.assertEqual(json.loads(old[0])['state_values'][0],0)
        with self.assertRaises(ValueError):
            app.save_record(record)

    def test_nonfinite_and_caps(self):
        for bad in (float('nan'),float('inf'),11,True):
            with self.assertRaises(ValueError):
                app.validate_vector([bad]+[0]*22)
        app.validate_vector([11]*23,bounded=False)

    def test_generation_both_modes_with_mock_provider(self):
        profile=app.workflow.read(app.workflow.WORK/'selected-baseline.json')
        profile['request_id']='draft'
        response={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(profile)}}]}
        with patch('vertex_literature_runner.api',return_value=response) as call:
            result=app.generate(dict(mode='values',state_values=profile['state_values']))
            self.assertEqual(result,profile)
            body=json.loads(call.call_args.args[1])
            self.assertNotIn('max_tokens',body)
            self.assertEqual(len(body['messages']),2)
            self.assertEqual(app.generate(dict(mode='description',description='A curious person.')),profile)
        response['choices'][0]['finish_reason']='length'
        with patch('vertex_literature_runner.api',return_value=response):
            with self.assertRaises(ValueError):app.generate(dict(mode='values',state_values=profile['state_values']))

    def test_import_does_not_overwrite(self):
        with app.db() as c:
            app.add(c,'x','profile','','Old',dict(state_values=[0]*23,description='Old'))
        original=app.get_record('x')
        original['body']['description']='Edited'
        app.save_record(original)
        with app.db() as c:
            app.add(c,'x','profile','','Old',dict(state_values=[0]*23,description='Old'))
        self.assertEqual(app.get_record('x')['body']['description'],'Edited')

    def test_training_overlay_and_provenance(self):
        source=app.workflow.BATCHES/'crowd-envent-v1/labels.jsonl'
        body=next(app.workflow.jsonl(source))
        body.setdefault('starting_state',[0]*23)
        with app.db() as c:
            app.add(c,body['custom_id'],'delta','neutral','Event',body)
        self.assertEqual(app.training_edits(),{})
        record=app.get_record(body['custom_id'])
        record['body']['label']['adjustments'][0]=0.123
        app.save_record(record)
        self.assertEqual(app.training_edits()[body['custom_id']]['label']['adjustments'][0],0.123)
        record=app.get_record(body['custom_id'])
        record['body']['starting_state'][0]=1
        with self.assertRaises(ValueError):app.save_record(record)


if __name__=='__main__':unittest.main()
