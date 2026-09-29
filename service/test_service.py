import json
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from core import Store, Conflict
from server import make_server

class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.calls=[]
        def predict(event,state):
            self.calls.append(list(state))
            if event=='fail':raise ValueError('Rejected event')
            return [int(event)]*23
        self.store=Store(Path(self.tmp.name)/'state.sqlite',predict,'test')
        self.p=self.store.register({'baseline':[1]*23,'settings':{'pressureEnabled':True}})
    def tearDown(self):self.tmp.cleanup()
    def test_union_and_recovery_once(self):
        r=self.store.events(self.p['id'],{'events':['3','2','-1'],'idempotency_key':'one'})
        self.assertEqual(r['combined_event_effect'],[.75]*23)
        self.assertEqual(r['current'],[1.75]*23)
        self.assertTrue(all(s==[1]*23 for s in self.calls))
        self.assertEqual(r['pressure']['pressure'],[.75]*23)
        self.assertEqual(len(r['state_text']['states']),23)
        self.assertIsInstance(r['delta_text'],str)
        self.assertEqual(r['delta_categories'],[2]*23)
        self.assertIn('0.75 state units',r['net_delta_text'])
        self.assertTrue(r['predictions'][0]['delta_text'])
        r2=self.store.events(self.p['id'],{'events':['0'],'idempotency_key':'two'})
        self.assertEqual(r2['model_input'],[1.5625]*23)
        self.assertEqual(r2['net_delta'],[-.1875]*23)
    def test_retry_conflict_and_persistence(self):
        payload={'events':['1'],'idempotency_key':'one'}
        r=self.store.events(self.p['id'],payload)
        self.assertEqual(self.store.events(self.p['id'],payload),r)
        self.assertEqual(len(self.calls),1)
        with self.assertRaises(Conflict):self.store.events(self.p['id'],dict(payload,events=['2']))
        with self.assertRaises(Conflict):self.store.events(self.p['id'],{'events':['1'],'idempotency_key':'two','expected_turn':0})
        reloaded=Store(self.store.path,self.store.predictor,'test')
        self.assertEqual(reloaded.get(self.p['id'])['current'],r['current'])
        self.assertEqual(len(reloaded.history(self.p['id'])['turns']),1)
    def test_rollback(self):
        with self.assertRaises(ValueError):self.store.events(self.p['id'],{'events':['3','fail'],'idempotency_key':'one'})
        self.assertEqual(self.store.get(self.p['id'])['turn'],0)
        self.assertEqual(self.store.history(self.p['id'])['turns'],[])
    def test_caps_pressure_and_opposites(self):
        p=self.store.register({'baseline':[10]*23,'settings':{'pressureEnabled':True}})
        r=self.store.events(p['id'],{'events':['4'],'idempotency_key':'one'})
        self.assertEqual(r['current'],[10]*23)
        self.assertEqual(r['net_delta'],[0]*23)
        self.assertEqual(r['pressure']['pressure'],[2]*23)
        r=self.store.events(self.p['id'],{'events':['4','4','-4'],'idempotency_key':'one'})
        self.assertEqual(r['combined_event_effect'],[0]*23)
        self.assertEqual(r['delta_text'],'No changes.')
    def test_validation(self):
        for payload in ({'baseline':[True]*23},{'baseline':[float('nan')]*23},{'baseline':[0]*23,'settings':{'longEnabled':1}},{'baseline':[0]*23,'settings':{'bad':1}}):
            with self.assertRaises(ValueError):self.store.register(payload)
    def test_http_auth_and_state(self):
        server=make_server('127.0.0.1',0,'test-token',self.store)
        thread=threading.Thread(target=server.serve_forever);thread.start()
        base=f'http://127.0.0.1:{server.server_port}'
        try:
            with self.assertRaises(urllib.error.HTTPError) as error:urllib.request.urlopen(base+'/schema')
            self.assertEqual(error.exception.code,401)
            req=urllib.request.Request(base+'/personalities/'+self.p['id']+'?format=text',headers={'Authorization':'Bearer test-token'})
            response=json.load(urllib.request.urlopen(req));self.assertIn('state_text',response);self.assertNotIn('current',response)
        finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':unittest.main()
