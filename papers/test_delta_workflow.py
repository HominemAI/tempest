import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import delta_workflow as w
from random_baseline_personalities import random_states, validate_profile


class WorkflowTests(unittest.TestCase):
    def test_vertex_delta_collection(self):
        def sample(schema):
            t=schema['type']
            if t=='object':return {k:sample(v) for k,v in schema['properties'].items()}
            if t=='array':return [sample(schema['items']) for _ in range(schema.get('minItems',0))]
            if t in ('number','integer'):return schema.get('minimum',0)
            return ''
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)
            w.save(folder/'submission-config.json',{'gcs_prefix':'gs://dz_batch_data/test'})
            w.save(folder/'events.json',[{'custom_id':'test','scenario':'An event','starting_state':[1]*23}])
            (folder/'input.jsonl').write_text(json.dumps({'custom_id':'test','body':{'messages':[]}})+'\n')
            value={**sample(w.emotion.SCHEMA),'request_id':'test'}
            record={'custom_id':'test','response':{'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}}
            with patch.object(w.vertex,'api',side_effect=[{'items':[{'name':'test/output/results.jsonl'}]},(json.dumps(record)+'\n').encode()]):
                rows=w.collect_vertex(folder,'labels')
            self.assertEqual(len(rows),1)
            self.assertEqual(rows[0]['label']['adjustments'],[0]*23)
            self.assertEqual(w.read(folder/'collection-summary.json')['valid'],1)
            self.assertEqual(list(w.jsonl(folder/'labels.jsonl'))[0]['custom_id'],'test')

    def test_vertex_delta_preparation(self):
        import vertex_literature_batch as prep
        with tempfile.TemporaryDirectory() as tmp,patch.object(w,'WORK',Path(tmp)),patch.object(prep,'RUN',Path(tmp)):
            profile=random_states(1)[0]
            rows=[dict(example_id='sample',scenario='A friend visits.',perspective='narrator',split_group='family')]
            manifest=w.prepare_labels('baseline-0001-deltas',rows,profile,provider='vertex')
            self.assertEqual(manifest['provider'],'vertex')
            folder=Path(manifest['jobs'][0])
            request=list(w.jsonl(folder/'input.jsonl'))[0]
            self.assertEqual(request['url'],'/v1/chat/completions')
            self.assertEqual(request['body']['model'],'openai/gpt-oss-120b-maas')
            self.assertNotIn('response_format',request['body'])
            self.assertNotIn('max_tokens',request['body'])
            text=request['body']['messages'][0]['content']
            self.assertIn(request['custom_id'],text)
            self.assertNotIn('all 23 starting values are **0.0**',text)
            self.assertEqual(w.read(folder/'events.json')[0]['starting_state'],profile['state_values'])
            job=w.read(folder/'job-config.json')
            self.assertTrue(job['inputConfig']['gcsSource']['uris'][0].startswith('gs://dz_batch_data/baseline-deltas-'))

    def test_vertex_queue_waits(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp);(folder/'batch.json').write_text('{}')
            with patch.object(w,'check_stop'),patch.object(w.vertex,'status',return_value={'state':'JOB_STATE_RUNNING'}),patch.object(w.vertex,'submit') as submit:
                self.assertFalse(w.labels_done({'jobs':[str(folder)],'records':1,'provider':'vertex'}))
                submit.assert_not_called()

    def test_serial_queue_submits_only_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp)/'a',Path(tmp)/'b'
            a.mkdir();b.mkdir()
            plan={'jobs':[str(a),str(b)],'records':2}
            with patch.object(w,'check_stop'),patch.object(w.emotion,'submit') as submit:
                self.assertFalse(w.labels_done(plan))
                submit.assert_called_once()
                self.assertEqual(w.emotion.RUN,a)

    def test_serial_queue_waits_for_active_job(self):
        with tempfile.TemporaryDirectory() as tmp:
            a,b=Path(tmp)/'a',Path(tmp)/'b'
            a.mkdir();b.mkdir();(a/'batch.json').write_text('{}')
            with patch.object(w,'check_stop'),patch.object(w.emotion,'status',return_value={'status':'in_progress'}),patch.object(w.emotion,'submit') as submit:
                self.assertFalse(w.labels_done({'jobs':[str(a),str(b)],'records':2}))
                submit.assert_not_called()

    def test_randomizer(self):
        rows=random_states()
        self.assertEqual(len(rows),2000)
        self.assertEqual(len({tuple(r['state_values']) for r in rows}),2000)
        self.assertEqual(rows,random_states())
        self.assertNotEqual(rows,random_states(seed=43))
        self.assertTrue(all(len(r['state_values'])==23 and all(-10<=v<=10 for v in r['state_values']) for r in rows))

    def test_neutral_and_baseline_prompts(self):
        template=w.emotion.PROMPT.read_text()
        profile=random_states(1)[0]
        event=dict(scenario='A friend visits.',perspective='narrator',starting_state=[0]*23)
        body=w.build_body(event,template)
        self.assertIn('all 23 starting values are **0.0**',body['input'])
        event['starting_state']=profile['state_values']
        body=w.build_body(event,template,profile)
        self.assertNotIn('all 23 starting values are **0.0**',body['input'])
        self.assertNotIn('With a zero starting vector',body['input'])
        self.assertIn('relative to the supplied nonzero starting vector',body['input'])
        self.assertNotIn('max_output_tokens',body)

    def test_vertex_response(self):
        value={'request_id':'x'}
        record={'response':{'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]}}
        self.assertEqual(w.unpack_vertex(record),value)
        record['response']['choices'][0]['finish_reason']='length'
        with self.assertRaises(ValueError):w.unpack_vertex(record)

    def test_profile_validation(self):
        expected=random_states(1)[0]
        p={**expected,'description':'Possible configuration.',
           **{k:[] for k in ['likely_feelings','needs','action_tendencies','possible_physical_experiences','tensions','uncertainty']},
           'dimension_readings':[dict(index=i+1,value=v,interpretation='A tendency.') for i,v in enumerate(expected['state_values'])]}
        validate_profile(p,expected)
        p['request_id']='wrong'
        with self.assertRaises(ValueError):validate_profile(p,expected)

    def test_stage_gates(self):
        with patch.object(w,'check_stop'),patch.object(w,'manifest',return_value={}),patch.object(w,'labels_done',return_value=False):
            s={'stage':'wait-neutral-situations'}
            self.assertEqual(w.advance(s),1800)
            self.assertEqual(s['stage'],'wait-neutral-situations')
        with patch.object(w,'check_stop'),patch.object(w,'gcps_done',return_value=False):
            s={'stage':'wait-gcp'}
            self.assertEqual(w.advance(s),1200)
        with patch.object(w,'check_stop'),patch.object(w,'manifest',return_value={}),patch.object(w,'labels_done',return_value=True):
            s={'stage':'wait-baseline-deltas'}
            self.assertEqual(w.advance(s),0)
            self.assertEqual(s['stage'],'train-all-deltas')
        with patch.object(w,'check_stop'),patch.object(w,'train_all') as train:
            s={'stage':'train-all-deltas'}
            self.assertIsNone(w.advance(s))
            train.assert_called_once()
            self.assertEqual(s['stage'],'complete')

    def test_prepare_duplicate_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            # Use isolated work root, no network.
            with patch.object(w,'WORK',Path(tmp)):
                rows=[dict(example_id='x',scenario='An event.',perspective='narrator',split_group='family')]
                m=w.prepare_labels('test',rows)
                self.assertEqual(m,w.prepare_labels('test',rows))
                rows[0]['scenario']='Changed event.'
                with self.assertRaises(ValueError):w.prepare_labels('test',rows)


if __name__=='__main__':unittest.main()
