import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
import uuid
from unittest.mock import patch
from core import Store
from database import Database
from server import make_server


class APITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        path=Path(self.tmp.name)/'api.sqlite';database=Database(path,schema='tempest_test_api_'+uuid.uuid4().hex)
        self.store=Store(path,lambda event,state:[1]*23,'test',database=database)
        self.server=make_server('127.0.0.1',0,'bootstrap-test-secret',self.store,secure_cookie=False)
        self.thread=threading.Thread(target=self.server.serve_forever);self.thread.start()
        self.base=f'http://127.0.0.1:{self.server.server_port}'
        self.admin=self.server.access.issue_key('admin',kind='admin')['token']
        self.a=self.server.access.create_account('A')['id'];self.b=self.server.access.create_account('B')['id']
        self.key=self.server.access.issue_key('A',account_id=self.a)['token']
        self.other=self.server.access.issue_key('B',account_id=self.b)['token']

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join()
        if self.store.database.postgres:
            with self.store.db() as db:db.execute('DROP SCHEMA '+self.store.database.schema+' CASCADE')
        self.tmp.cleanup()

    def call(self,path,payload=None,token=None,headers=None):
        headers=dict(headers or {})
        if token:headers['Authorization']='Bearer '+token
        data=None
        if payload is not None:headers['Content-Type']='application/json';data=json.dumps(payload).encode()
        request=urllib.request.Request(self.base+path,data=data,headers=headers)
        try:
            with urllib.request.urlopen(request) as response:return response.status,json.load(response),response.headers
        except urllib.error.HTTPError as response:return response.code,json.load(response),response.headers

    def test_random_events_admin_only(self):
        self.assertEqual(self.call('/admin/events/random',token=self.key)[0],403)
        status,value,_=self.call('/admin/events/random',token=self.admin)
        self.assertEqual(status,200)
        self.assertTrue(0<len(value['event'])<=1200)
        self.assertGreater(value['pool_size'],0)

    def test_source_isolates_ownership_relationships_and_limits(self):
        tokens = {}
        instances = {}
        for source in ('discord', 'denizens', 'default'):
            identity = {'user_id':'alice', 'source':source}
            status,key,_ = self.call('/admin/keys',dict(identity,name=source,account_id=self.a),token=self.admin)
            self.assertEqual(status,200,key)
            self.assertEqual((key['user_id'],key['source']),('alice',source))
            tokens[source] = key['token']
            status,instance,_ = self.call('/admin/instances',dict(identity,account_id=self.a,baseline=[0]*23),token=self.admin)
            self.assertEqual(status,200,instance)
            instances[source] = instance['id']
            self.assertEqual(self.call('/personalities/'+instance['id'],token=key['token'])[0],200)
        self.assertEqual(self.call('/personalities/'+instances['discord'],token=tokens['denizens'])[0],404)
        path='/personalities/'+instances['discord']
        events=[{'text':'A visitor helps them','user_id':'bob','source':source} for source in ('discord','denizens')]
        status,value,_=self.call(path+'/events/users',{'events':events,'idempotency_key':'sources'},token=tokens['discord'])
        self.assertEqual(status,200,value)
        self.assertEqual({(u['source'],u['user_id']) for u in value['users']},{('discord','bob'),('denizens','bob')})
        for source in ('discord','denizens'):
            status,value,_=self.call(path+'/status/users?user_id=bob&source='+source,token=tokens['discord'])
            self.assertEqual(status,200,value)
            self.assertEqual(len(value['users']),1)
            self.assertEqual(value['users'][0]['source'],source)
            self.assertNotIn('tempest-source:',value['users'][0]['text'])
        self.assertEqual(self.call(path+'/status/users?user_id=bob',token=tokens['discord'])[1]['users'],[])
        self.call('/admin/limits',{'account_id':self.a,'user_id':'alice','source':'discord','text':7},token=self.admin)
        a=self.call('/admin/limits?account_id='+self.a+'&user_id=alice&source=discord',token=self.admin)[1]
        b=self.call('/admin/limits?account_id='+self.a+'&user_id=alice&source=denizens',token=self.admin)[1]
        self.assertEqual(a['limits']['text'],7)
        self.assertNotEqual(b['limits']['text'],7)
        self.assertEqual(a['source'],'discord')
        usage=self.call('/admin/usage?account_id='+self.a+'&user_id=alice&source=discord',token=self.admin)[1]
        self.assertEqual(usage['used']['text'],1)
        self.assertEqual(usage['source'],'discord')
        replay=self.call(path+'/events/users',{'events':events,'idempotency_key':'sources'},token=tokens['discord'])[1]
        self.assertEqual(replay['turn'],1)
        self.assertEqual(replay['users'][0]['source'],'discord')
        untouched=self.call('/personalities/'+instances['denizens']+'/status/users',token=tokens['denizens'])[1]
        self.assertEqual(untouched['users'],[])
        for source in ('',None,42,'x'*129):
            self.assertEqual(self.call(path+'/events',{'events':[{'text':'hello','user_id':'bob','source':source}],'idempotency_key':'bad'},token=tokens['discord'])[0],400)

    def test_source_copy_description_and_legacy_identity(self):
        self.server.api.generator=lambda context:{'status':'complete','configuration':{'name':'Created','baseline':[0]*23}}
        template=self.server.catalog.save_template({'name':'Public','baseline':[0]*23,'visibility':'public'},admin=True)
        copied=[];created=[]
        for source in ('one','two'):
            body={'account_id':self.a,'user_id':'same','source':source,'idempotency_key':'same-key'}
            route='/templates/'+template['id']+'/copy'
            status,value,_=self.call(route,body,token=self.admin)
            self.assertEqual(status,200,value)
            copied.append(value['id'])
            self.assertEqual(self.call(route,body,token=self.admin)[1]['id'],value['id'])
            owner=self.call('/admin/instances/'+value['id'],token=self.admin)[1]
            self.assertEqual((owner['source'],owner['user_id']),(source,'same'))
            status,value,_=self.call('/characters/describe',dict(body,description='A calm person'),token=self.admin)
            self.assertEqual(status,200,value)
            created.append(value['instance']['id'])
        self.assertNotEqual(copied[0],copied[1])
        self.assertNotEqual(created[0],created[1])
        legacy=self.store.register({'baseline':[0]*23},self.a,'legacy')['id']
        status,key,_=self.call('/admin/keys',{'name':'Legacy','account_id':self.a,'user_id':'legacy','source':'default'},token=self.admin)
        self.assertEqual(status,200,key)
        self.assertEqual(self.call('/personalities/'+legacy,token=key['token'])[0],200)
        from user_identity import UserIdentities
        reopened=UserIdentities(self.store.database)
        internal=reopened.encode('same','one')
        self.assertEqual(reopened.output({'user_id':internal}),{'user_id':'same','source':'one'})

    def test_unlimited_expiration_preserves_existing_keys(self):
        base={'name':'test','kind':'account','account_id':self.a}
        status,existing,_=self.call('/admin/keys',base,token=self.admin)
        self.assertEqual(status,200)
        self.assertIsInstance(existing['expires_at'],int)
        for kind in ('account','admin'):
            payload={'name':'unlimited','kind':kind,'expires_days':-1}
            if kind=='account':payload['account_id']=self.a
            status,key,_=self.call('/admin/keys',payload,token=self.admin)
            self.assertEqual(status,200)
            self.assertIsNone(key['expires_at'])
            with patch('access.time.time',return_value=existing['expires_at']+86400):
                self.assertEqual(self.server.access.authenticate_key(key['token'])['kind'],kind)
            self.assertEqual(self.call('/admin/keys/'+key['id']+'/revoke',{},token=self.admin)[0],200)
            self.assertEqual(self.call('/personalities',token=key['token'])[0],401)
        self.assertEqual(self.call('/personalities',token=existing['token'])[0],200)
        listed=self.call('/admin/keys',token=self.admin)[1]['keys']
        self.assertEqual(next(k for k in listed if k['id']==existing['id'])['expires_at'],existing['expires_at'])
        for value in (0,-2,3651,True,1.5,None,'-1'):
            self.assertEqual(self.call('/admin/keys',dict(base,expires_days=value),token=self.admin)[0],400)

    def test_setup_sessions_csrf_and_no_legacy_bypass(self):
        self.assertEqual(self.call('/schema',token='bootstrap-test-secret')[0],401)
        status,data,headers=self.call('/auth/setup',{'username':'owner','password':'a-long-test-password','bootstrap_token':'bootstrap-test-secret'})
        self.assertEqual(status,200);cookie=headers['Set-Cookie'].split(';')[0]
        self.assertIn('HttpOnly',headers['Set-Cookie']);csrf=data['csrf']
        self.assertEqual(self.call('/auth/setup',{'username':'hijack','password':'a-long-test-password','bootstrap_token':'bootstrap-test-secret'})[0],409)
        self.assertEqual(self.call('/admin/accounts',{'name':'Blocked'},headers={'Cookie':cookie})[0],403)
        self.assertEqual(self.call('/admin/accounts',{'name':'Allowed'},headers={'Cookie':cookie,'X-CSRF-Token':csrf})[0],200)
        self.assertEqual(self.call('/admin/accounts',{'name':'Blocked'},headers={'Cookie':cookie,'X-CSRF-Token':csrf,'Origin':'https://evil.example'})[0],403)
        self.assertEqual(self.call('/auth/me',token=self.admin)[0],401)
        self.assertEqual(self.call('/auth/logout',{},headers={'Cookie':cookie,'X-CSRF-Token':csrf})[0],200)
        self.assertEqual(self.call('/auth/me',headers={'Cookie':cookie})[0],401)

    def test_account_isolation_templates_and_numeric_filter(self):
        template=self.server.catalog.save_template({'name':'Private','baseline':[0]*23,'visibility':'restricted','grants':[self.a]},admin=True)
        path='/templates/'+template['id']+'/copy'
        self.assertEqual(self.call(path,{'idempotency_key':'c'},token=self.other)[0],404)
        status,instance,_=self.call(path,{'idempotency_key':'c','account_id':self.b},token=self.key)
        self.assertEqual(status,200);identifier=instance['id']
        self.assertEqual(self.store.owner(identifier)['account_id'],self.a)
        self.assertEqual(self.call('/personalities/'+identifier,token=self.other)[0],404)
        self.assertEqual(self.call('/schema',token=self.key)[0],403)
        self.assertEqual(self.call('/admin/accounts',token=self.key)[0],403)
        status,result,_=self.call('/personalities/'+identifier+'/events',{'events':['hello'],'idempotency_key':'turn'},token=self.key)
        self.assertEqual(status,200)
        self.assertEqual(set(result),{'id','name','turn','state_text','delta_text'})
        self.assertNotIn('baseline',instance)
        history=self.call('/personalities/'+identifier+'/history',token=self.key)[1]['turns'][0]
        self.assertEqual(set(history),set(result))

    def test_user_binding_quota_retry_revocation(self):
        user=self.server.access.issue_key('user',account_id=self.a,user_id='alice')
        otheruser=self.server.access.issue_key('user',account_id=self.a,user_id='bob')
        instance=self.store.register({'baseline':[0]*23},self.a,'alice')['id']
        self.server.access.update_limits(self.a,limits={'text':1})
        route='/personalities/'+instance+'/events';payload={'events':['hi'],'idempotency_key':'one'}
        self.assertEqual(self.call(route,payload,token=otheruser['token'])[0],404)
        self.assertEqual(self.call(route,payload,token=user['token'])[0],200)
        self.assertEqual(self.call(route,payload,token=user['token'])[0],200)
        self.assertEqual(self.server.access.usage(self.a)['used']['text'],1)
        self.assertEqual(self.call(route,dict(payload,idempotency_key='two'),token=user['token'])[0],429)
        self.server.access.revoke_key(user['id'])
        self.assertEqual(self.call('/personalities/'+instance,token=user['token'])[0],401)

    def test_copy_replay_retains_snapshot_checks_payload_and_current_grant(self):
        template=self.server.catalog.save_template({'name':'Original','baseline':[0]*23,'visibility':'restricted','grants':[self.a]},admin=True)
        route='/templates/'+template['id']+'/copy';payload={'idempotency_key':'copy-one'}
        status,original,_=self.call(route,payload,token=self.key)
        self.assertEqual(status,200)
        self.server.catalog.save_template({'name':'Changed','baseline':[1]*23},identifier=template['id'],admin=True)
        self.store.configure(original['id'],{'name':'Instance changed','settings':{'pressureEnabled':True},'expected_turn':0})
        status,replayed,_=self.call(route,payload,token=self.key)
        self.assertEqual(status,200);self.assertEqual(replayed,original)
        self.assertEqual(self.call(route,dict(payload,account_id=self.b),token=self.key)[0],409)
        self.assertEqual(self.store.get(original['id'])['name'],'Instance changed')
        self.server.catalog.save_template({'grants':[]},identifier=template['id'],admin=True)
        self.assertEqual(self.call(route,payload,token=self.key)[0],404)

    def test_description_scope_and_completed_instance(self):
        self.server.api.generator=lambda context:{'status':'complete','configuration':{'name':'Created','baseline':[0]*23}}
        body={'description':'A calm character','idempotency_key':'first'}
        self.assertEqual(self.call('/characters/describe',body,token=self.key)[0],403)
        token=self.server.access.issue_key('creator',account_id=self.a,scopes=['create_description'])['token']
        status,result,_=self.call('/characters/describe',body,token=token)
        self.assertEqual(status,200);self.assertIn('instance',result)
        self.assertNotIn('current',result['instance'])
        again=self.call('/characters/describe',body,token=token)[1]
        self.assertEqual(result['instance']['id'],again['instance']['id'])
        self.assertEqual(self.server.access.usage(self.a)['used']['create_description'],1)

    def test_relationship_actor_is_metadata_not_authorization(self):
        instance=self.store.register({'baseline':[0]*23},self.a)['id']
        payload={'events':[{'text':'A visitor helps them','user_id':'external-XYZ'}],'idempotency_key':'one'}
        status,result,_=self.call('/personalities/'+instance+'/events/users',payload,token=self.key)
        self.assertEqual(status,200);self.assertEqual(result['users'][0]['user_id'],'external-XYZ')
        self.assertNotIn('delta',result['users'][0]);self.assertIn('text',result['users'][0])
        status,view,_=self.call('/personalities/'+instance+'/status/users',token=self.key)
        self.assertEqual(status,200);self.assertNotIn('event_delta',view['users'][0])
        self.assertEqual(self.call('/personalities/'+instance+'/status/users',token=self.other)[0],404)
        raw=self.call('/personalities/'+instance+'/status/users',token=self.admin)[1]
        self.assertEqual(raw['users'][0]['delta'],[.25]*23)
        self.assertEqual(self.server.access.usage(self.a)['used']['text'],1)


if __name__=='__main__':unittest.main()
