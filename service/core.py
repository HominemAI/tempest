"""Persistent, serialized personality turns. No cloud calls or background ticking."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from database import Database

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'papers'))
from personality_state_mapping import active_mapping, describe, validate_vector

RANGES = {'sensitivity': (1, 0, 4), 'shift': (.1, 0, 1),
          'returnRate': (.05, 0, 1), 'release': (.15, 0, 1), 'longRate': (.005, 0, .1)}

def defaults():
    return {'recovery': [.25]*23, 'multipliers': [1]*23,
            'effects': [-2,-1,-.5,-.25,0,.25,.5,1,2],
            'constitution': {k:[v[0]]*23 for k,v in RANGES.items()},
            'pressureEnabled': False, 'longEnabled': False, 'relationshipsEnabled': False,
            'seed': 42}

def settings(raw):
    if not isinstance(raw,dict): raise ValueError('settings must be an object')
    result=defaults()
    if set(raw)-set(result): raise ValueError('Unknown setting: '+str(set(raw)-set(result)))
    for key,value in raw.items():
        if key=='constitution':
            if not isinstance(value,dict) or set(value)-set(RANGES): raise ValueError('Invalid constitution keys')
            result[key].update(value)
        else: result[key]=value
    for key,n,lo,hi in [('recovery',23,0,1),('multipliers',23,-1,2),('effects',9,-10,10)]:
        numeric_vector(result[key],n,lo,hi,key)
    for key,(_,lo,hi) in RANGES.items(): numeric_vector(result['constitution'][key],23,lo,hi,key)
    for key in ('pressureEnabled','longEnabled','relationshipsEnabled'):
        if type(result[key]) is not bool: raise ValueError(key+' must be boolean')
    if type(result['seed']) is not int or not 0<=result['seed']<2**32: raise ValueError('Invalid seed')
    return result

def numeric_vector(value,n,lo,hi,key):
    if not isinstance(value,list) or len(value)!=n or not all(type(x) in (int,float) and math.isfinite(x) and lo<=x<=hi for x in value):
        raise ValueError(f'{key} requires {n} finite numbers in [{lo},{hi}]')

def pressure_state(baseline):
    return {**{k:list(baseline) for k in ('original','adapted','temporary')},
            **{k:[0]*23 for k in ('pressure','duration','intensity')}}

def engine(payload):
    result=subprocess.run(['node',str(Path(__file__).with_name('engine.cjs'))],input=json.dumps(payload),text=True,capture_output=True,timeout=15,check=True)
    return json.loads(result.stdout)

class Conflict(Exception): pass

class Store:
    def __init__(self,path,predictor,model_id,database=None):
        self.path=Path(path);self.database=database or Database(path)
        self.predictor=predictor;self.model_id=model_id;self.lock=threading.RLock()
        self.mapping=active_mapping();self.mapping_data=json.loads(self.mapping.read_text())
        self.dimensions=[d['id'] for d in self.mapping_data['dimensions']]
        self.links=json.loads((ROOT/'papers/web/relationship-multipliers.json').read_text())
        self.mapping_sha=hashlib.sha256(self.mapping.read_bytes()).hexdigest()
        self.delta_mapping=active_mapping('delta')
        self.delta_data=json.loads(self.delta_mapping.read_text())
        if [d['id'] for d in self.delta_data['dimensions']]!=self.dimensions:raise ValueError('Delta index dimension mismatch')
        self.delta_sha=hashlib.sha256(self.delta_mapping.read_bytes()).hexdigest()
        with self.db() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS personalities(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS turns(id TEXT, turn INTEGER, request_key TEXT, fingerprint TEXT, response TEXT, PRIMARY KEY(id,turn), UNIQUE(id,request_key))')
            db.execute('CREATE TABLE IF NOT EXISTS instance_owners(id TEXT PRIMARY KEY, account_id TEXT, user_id TEXT)')
            db.execute('CREATE TABLE IF NOT EXISTS relationship_states(personality_id TEXT NOT NULL, user_id TEXT NOT NULL, body TEXT NOT NULL, PRIMARY KEY(personality_id,user_id))')
            # Existing records remain system-only; never silently assign them to a customer.
            db.execute('INSERT INTO instance_owners(id,account_id,user_id) SELECT id,NULL,NULL FROM personalities WHERE id NOT IN (SELECT id FROM instance_owners) ON CONFLICT DO NOTHING')

    def db(self): return self.database.connect()

    def delta_description(self,categories,key):
        phrases=[]
        for dimension,category in zip(self.delta_data['dimensions'],categories):
            if category==0:continue
            options=next(a['options'] for a in dimension['anchors'] if a['value']==category)
            digest=hashlib.sha256(f'{key}|{dimension["id"]}|{category}'.encode()).digest()
            phrases.append(options[int.from_bytes(digest[:8],'big')%len(options)]['text'].strip())
        return ' '.join(phrases) or 'No changes.'

    def schema(self):
        return {'dimensions':self.dimensions,'baseline_range':[-10,10],'defaults':defaults(),
                'model':self.model_id,'mapping_sha256':self.mapping_sha,'relationship_version':self.links['version'],
                'max_events_per_turn':32,'event_limit':'20,000 characters and encoder token limit (no truncation)'}

    def register(self,payload,account_id=None,user_id=None,identifier=None):
        if not isinstance(payload,dict) or set(payload)-{'baseline','settings','name'}: raise ValueError('Expected baseline, optional settings and name')
        validate_vector(payload.get('baseline'))
        name=payload.get('name','')
        if not isinstance(name,str) or len(name)>180: raise ValueError('name must be at most 180 characters')
        baseline=list(payload['baseline']);identifier=identifier or str(uuid.uuid4())
        state={'id':identifier,'name':name,'baseline':baseline,'current':list(baseline),
               'settings':settings(payload.get('settings',{})),'pressure':pressure_state(baseline),'turn':0}
        with self.lock,self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            old=db.execute('SELECT body FROM personalities WHERE id=?',(identifier,)).fetchone()
            if old:
                owner=db.execute('SELECT account_id,user_id FROM instance_owners WHERE id=?',(identifier,)).fetchone()
                existing=json.loads(old[0])
                if tuple(owner)!=(account_id,user_id) or existing['baseline']!=baseline or existing['settings']!=state['settings'] or existing['name']!=name:
                    raise Conflict('Registration key already used')
                return self.present(existing,'both')
            inserted=db.execute('INSERT INTO personalities VALUES(?,?) ON CONFLICT(id) DO NOTHING',(identifier,json.dumps(state))).rowcount
            if not inserted:
                existing=self.read(db,identifier)
                owner=db.execute('SELECT account_id,user_id FROM instance_owners WHERE id=?',(identifier,)).fetchone()
                if owner is None or tuple(owner)!=(account_id,user_id) or existing['baseline']!=baseline or existing['settings']!=state['settings'] or existing['name']!=name:
                    raise Conflict('Registration key already used')
                return self.present(existing,'both')
            db.execute('INSERT INTO instance_owners VALUES(?,?,?)',(identifier,account_id,user_id))
        return self.present(state,'both')

    def owner(self,identifier):
        with self.db() as db:
            row=db.execute('SELECT account_id,user_id FROM instance_owners WHERE id=?',(identifier,)).fetchone()
            if row is None:raise KeyError('Personality not found')
            return {'account_id':row[0],'user_id':row[1]}

    def catalog(self,account_id=None,user_id=None,admin=False):
        with self.db() as db:
            where='';args=[]
            if not admin or account_id is not None:
                where=' WHERE o.account_id=?';args.append(account_id)
            if user_id is not None:
                where+=(' AND' if where else ' WHERE')+' o.user_id=?';args.append(user_id)
            rows=db.execute('SELECT p.body,o.account_id,o.user_id FROM personalities p JOIN instance_owners o ON p.id=o.id'+where+' ORDER BY p.id LIMIT 1000',args).fetchall()
            return [{'id':s['id'],'name':s['name'],'turn':s['turn'],'account_id':r[1],'user_id':r[2]} for r in rows for s in [json.loads(r[0])]]

    def configure(self,identifier,payload):
        if not isinstance(payload,dict) or set(payload)-{'settings','name','expected_turn'}:raise ValueError('Expected settings, name, expected_turn')
        with self.lock,self.db() as db:
            db.execute('BEGIN IMMEDIATE');state=self.read(db,identifier,lock=True)
            if type(payload.get('expected_turn')) is not int or payload['expected_turn']!=state['turn']:raise Conflict('expected_turn does not match current turn')
            if 'settings' in payload:state['settings']=settings(payload['settings'])
            if 'name' in payload:
                if not isinstance(payload['name'],str) or len(payload['name'])>180:raise ValueError('Invalid name')
                state['name']=payload['name']
            db.execute('UPDATE personalities SET body=? WHERE id=?',(json.dumps(state),identifier))
            return self.present(state,'both')

    def read(self,db,identifier,lock=False):
        suffix=' FOR UPDATE' if lock and self.database.postgres else ''
        row=db.execute('SELECT body FROM personalities WHERE id=?'+suffix,(identifier,)).fetchone()
        if not row: raise KeyError('Personality not found')
        return json.loads(row[0])

    def present(self,state,format):
        if format not in ('values','text','both'): raise ValueError('format must be values, text, or both')
        result={'id':state['id'],'name':state['name'],'turn':state['turn'],'model':self.model_id,
                'mapping_sha256':self.mapping_sha,'relationship_version':self.links['version']}
        if format in ('values','both'):
            result.update(dimensions=self.dimensions,baseline=state['baseline'],current=state['current'],settings=state['settings'],pressure=state['pressure'])
        if format in ('text','both'):
            language=describe(state['current'],self.mapping,seed=state['settings']['seed'],mode='nearest',richness=True)['teacher_payload']
            result['state_text']=language
        return result

    def get(self,identifier,format='both'):
        with self.lock,self.db() as db: return self.present(self.read(db,identifier),format)

    def history(self,identifier,after=0):
        with self.lock,self.db() as db:
            self.read(db,identifier)
            return {'turns':[json.loads(r[0]) for r in db.execute('SELECT response FROM turns WHERE id=? AND turn>? ORDER BY turn LIMIT 100',(identifier,after))]}

    def relationship_view(self,row):
        # Draft rendering convention: map accumulated adjustment to the nearest
        # delta-index intensity on a full +/-10 scale. This is not model output.
        categories=[(1 if v>0 else -1)*min(4,max(1,int(abs(v)/2.5+.5))) if abs(v)>1e-8 else 0 for v in row['delta']]
        phrase=self.delta_description(categories,'relationship:'+row['user_id'])
        return dict(row,text='For user '+row['user_id']+' — '+phrase,
                    description_policy='Accumulated relationship adjustment; draft nearest intensity on ±10 scale, not an inferred current global state. Not yet fed back into inference.')

    def relationships(self,identifier,user_id=None):
        with self.lock,self.db() as db:
            self.read(db,identifier)
            sql='SELECT body FROM relationship_states WHERE personality_id=?';args=[identifier]
            if user_id is not None:sql+=' AND user_id=?';args.append(user_id)
            return {'users':[self.relationship_view(json.loads(r[0])) for r in db.execute(sql+' ORDER BY user_id LIMIT 1000',args)]}

    def events(self,identifier,payload):
        if not isinstance(payload,dict) or set(payload)-{'events','idempotency_key','expected_turn'}: raise ValueError('Expected events, idempotency_key, and optional expected_turn')
        events=payload.get('events');key=payload.get('idempotency_key')
        if not isinstance(events,list) or not 1<=len(events)<=32:raise ValueError('events requires 1–32 events')
        normalized=[]
        for event in events:
            if isinstance(event,str):text=event;actor=None
            elif isinstance(event,dict) and not set(event)-{'text','user_id'}:
                text=event.get('text');actor=event.get('user_id')
                if not isinstance(actor,str) or not actor.strip() or len(actor)>128:raise ValueError('Event user_id requires 1–128 characters')
            else:raise ValueError('Event must be text or {text,user_id}')
            if not isinstance(text,str) or not text.strip() or len(text)>20000:raise ValueError('Event text requires 1–20,000 characters')
            normalized.append((text,actor))
        if not isinstance(key,str) or not 1<=len(key)<=128: raise ValueError('idempotency_key required (1–128 characters)')
        expected=payload.get('expected_turn')
        if expected is not None and (type(expected) is not int or expected<0): raise ValueError('Invalid expected_turn')
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        # One process, one model; DB transaction also protects against another writer.
        with self.lock,self.db() as db:
            db.execute('BEGIN IMMEDIATE')
            state=self.read(db,identifier,lock=True)
            old=db.execute('SELECT fingerprint,response FROM turns WHERE id=? AND request_key=?',(identifier,key)).fetchone()
            if old:
                if old[0]!=fingerprint: raise Conflict('Idempotency key reused with different content')
                return json.loads(old[1])
            if expected is not None and expected!=state['turn']: raise Conflict('expected_turn does not match current turn')
            cfg=state['settings'];before=list(state['current']);previous=copy.deepcopy(state['pressure'])
            target=previous['temporary'] if cfg['pressureEnabled'] else previous['adapted']
            recovered=[v+(b-v)*rate for v,b,rate in zip(before,target,cfg['recovery'])]
            predictions=[];proposals=[]
            for event,actor in normalized:
                inference_started=time.perf_counter()
                categories=self.predictor(event,list(recovered))
                inference_seconds=time.perf_counter()-inference_started
                if len(categories)!=23 or any(type(c) is not int or not -4<=c<=4 for c in categories): raise ValueError('Invalid model categories')
                effect=[cfg['effects'][c+4]*m for c,m in zip(categories,cfg['multipliers'])]
                predictions.append({'event':event,'user_id':actor,'categories':categories,'effect':effect,'model_inference_seconds':inference_seconds,'delta_text':self.delta_description(categories,key+'|'+str(len(predictions)))});proposals.append(effect)
            combined=engine({'operation':'union','proposals':proposals})['net']
            updated_pressure=engine({'operation':'advance','old':previous,'current':recovered,'delta':combined,
                'config':cfg['constitution'],'enabled':cfg['pressureEnabled'],'longEnabled':cfg['longEnabled'],
                'links':self.links['links'] if cfg['relationshipsEnabled'] else []})
            current=[max(-10,min(10,round(v+d,8))) for v,d in zip(recovered,combined)]
            net=[round(v-b,8) for v,b in zip(current,before)]
            state.update(current=current,pressure=updated_pressure,turn=state['turn']+1)
            response=self.present(state,'both')
            relationship_changes=[]
            for actor in dict.fromkeys(p['user_id'] for p in predictions if p['user_id'] is not None):
                related=[p for p in predictions if p['user_id']==actor]
                effect=engine({'operation':'union','proposals':[p['effect'] for p in related]})['net']
                row=db.execute('SELECT body FROM relationship_states WHERE personality_id=? AND user_id=?',(identifier,actor)).fetchone()
                before_relationship=json.loads(row[0])['delta'] if row else [0]*23
                delta=[max(-10,min(10,round(v+d,8))) for v,d in zip(before_relationship,effect)]
                item={'user_id':actor,'delta':delta,'turn':state['turn']}
                db.execute('INSERT INTO relationship_states VALUES(?,?,?) ON CONFLICT(personality_id,user_id) DO UPDATE SET body=excluded.body',(identifier,actor,json.dumps(item)))
                cats=[max(0,*[p['categories'][i] for p in related])+min(0,*[p['categories'][i] for p in related]) for i in range(23)]
                relationship_changes.append(dict(self.relationship_view(item),event_delta=effect,delta_text=self.delta_description(cats,key+'|'+actor)))
            if relationship_changes:response['users']=relationship_changes
            combined_categories=[max(0,*[p['categories'][i] for p in predictions])+min(0,*[p['categories'][i] for p in predictions]) for i in range(23)]
            response.update(timestamp=datetime.now(timezone.utc).isoformat(),idempotency_key=key,before=before,model_input=recovered,predictions=predictions,
                            combined_event_effect=combined,net_delta=net,pressure_before=previous,
                            delta_text=self.delta_description(combined_categories,key),
                            delta_categories=combined_categories,model_inference_seconds=sum(p['model_inference_seconds'] for p in predictions),delta_index_sha256=self.delta_sha,
                            delta_text_policy='Authored model-category changes, combined by strongest positive plus strongest negative category. Before numerical translation, multipliers, recovery, and caps; not net state changes.',
                            net_delta_text=' '.join(f"{d['name']} {'increased' if n>0 else 'decreased'} by {abs(n):g} state units." for d,n in zip(self.mapping_data['dimensions'],net) if n) or 'No changes.')
            db.execute('UPDATE personalities SET body=? WHERE id=?',(json.dumps(state),identifier))
            db.execute('INSERT INTO turns VALUES(?,?,?,?,?)',(identifier,state['turn'],key,fingerprint,json.dumps(response)))
            return response
