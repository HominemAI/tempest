"""Authorization boundary shared by HTTP clients and the admin UI."""
import hashlib
import json
import threading
import time
import uuid
from access import AccessError, Forbidden, Conflict

class Unavailable(AccessError):
    status=503


def text_view(value):
    """Allowlist only; raw state/configuration must never leak through new fields."""
    result={k:value[k] for k in ('id','name','turn','state_text','delta_text') if k in value}
    if 'users' in value:result['users']=[user_text(u) for u in value['users']]
    return result

def user_text(value):
    return {k:value[k] for k in ('user_id','turn','text','delta_text','description_policy') if k in value}


class API:
    def __init__(self,store,access,catalog,generator=None):
        self.store=store;self.access=access;self.catalog=catalog;self.generator=generator
        self.lock=threading.RLock()
        with self.store.database.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS template_copy_requests(id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, configuration TEXT NOT NULL, response TEXT)')

    def copy_template(self,identifier,account_id,user_id,key,payload,admin):
        # Always recheck the current grant, including completed request replays.
        configuration=self.catalog.configuration_for_copy(identifier,account_id,admin=admin)
        instance_id=str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps(['copy',account_id,user_id,identifier,key])))
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True,allow_nan=False).encode()).hexdigest()
        with self.lock:
            with self.store.database.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                db.execute('INSERT INTO template_copy_requests(id,fingerprint,configuration,response) VALUES(?,?,?,NULL) ON CONFLICT(id) DO NOTHING',
                           (instance_id,fingerprint,json.dumps(configuration,allow_nan=False)))
                suffix=' FOR UPDATE' if self.store.database.postgres else ''
                row=db.execute('SELECT fingerprint,configuration,response FROM template_copy_requests WHERE id=?'+suffix,(instance_id,)).fetchone()
                if row[0]!=fingerprint:raise Conflict('Idempotency key reused with different content')
                if row[2] is not None:return json.loads(row[2])
                configuration=json.loads(row[1])
            # The stored snapshot survives source edits while registration is
            # interrupted. Registration itself is idempotent for this owned ID.
            result=self.store.register(configuration,account_id,user_id,instance_id)
            with self.store.database.connect() as db:
                db.execute('UPDATE template_copy_requests SET response=? WHERE id=? AND response IS NULL',
                           (json.dumps(result,allow_nan=False),instance_id))
                return json.loads(db.execute('SELECT response FROM template_copy_requests WHERE id=?',(instance_id,)).fetchone()[0])

    def account(self,identifier):
        if not any(a['id']==identifier for a in self.access.list_accounts()):raise KeyError('Account not found')
        return identifier

    def owned(self,principal,identifier):
        owner=self.store.owner(identifier)
        if principal['kind']!='admin' and (owner['account_id']!=principal['account_id'] or (principal.get('user_id') is not None and owner['user_id']!=principal['user_id'])):
            raise KeyError('Personality not found')
        return owner

    def charged(self,p,kind,operation,payload,work):
        if p['kind']=='admin':return work()
        if kind not in p['scopes']:raise Forbidden('Token does not have the required scope')
        fingerprint=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        args=(p['account_id'],p.get('user_id'),kind,operation,fingerprint)
        with self.lock:
            reservation=self.access.reserve(*args)
            try:result=work()
            except Exception:
                self.access.release(*args,attempt_id=reservation['attempt_id'])
                raise
            self.access.complete(*args,attempt_id=reservation['attempt_id'])
            return result

    def dispatch(self,method,path,query,payload,p):
        admin=p['kind']=='admin';parts=path.strip('/').split('/');q=lambda key:query.get(key,[None])[0]
        if path.startswith('/admin/'):
            if not admin:raise Forbidden('Administrator access required')
            if path=='/admin/accounts':
                if method=='GET':return {'accounts':self.access.list_accounts()}
                return self.access.create_account(payload['name'],payload.get('external_id'))
            if path=='/admin/keys':
                if method=='GET':return {'keys':self.access.list_keys(q('account_id'))}
                days=payload.get('expires_days',90)
                if type(days) is not int or not 1<=days<=3650:raise ValueError('expires_days must be 1–3650')
                return self.access.issue_key(payload['name'],kind=payload.get('kind','account'),account_id=payload.get('account_id'),user_id=payload.get('user_id') or None,scopes=payload.get('scopes'),expires_at=int(time.time())+days*86400)
            if len(parts)==4 and parts[1]=='keys' and parts[3]=='revoke' and method=='POST':
                self.access.revoke_key(parts[2]);return {'revoked':True}
            if path=='/admin/limits':
                if method=='GET':return self.access.describe_limits(q('account_id'),q('user_id'))
                limits={k:payload[k] for k in ('text','create_description') if k in payload}
                return self.access.update_limits(account_id=payload.get('account_id') or None,user_id=payload.get('user_id') or None,limits=limits)
            if path=='/admin/usage' and method=='GET':return self.access.usage(q('account_id'),q('user_id'))
            if path=='/admin/templates':
                if method=='GET':return {'templates':self.catalog.list_templates(admin=True)}
                return self.catalog.save_template(payload,admin=True)
            if len(parts)==3 and parts[1]=='templates':
                if method=='GET':return self.catalog.get_template(parts[2],admin=True,configuration=True)
                return self.catalog.save_template(payload,identifier=parts[2],admin=True)
            if path=='/admin/instances':
                if method=='GET':return {'instances':self.store.catalog(q('account_id'),q('user_id'),admin=True)}
                account_id=payload.get('account_id') or None
                if account_id:self.account(account_id)
                if payload.get('user_id'):
                    if not account_id:raise ValueError('A bound quota user requires an API account')
                    self.access.provision_user(account_id,payload['user_id'])
                key=payload.get('idempotency_key')
                if key is not None and (not isinstance(key,str) or not 1<=len(key)<=128):raise ValueError('Invalid idempotency key')
                identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,'admin-instance:'+key)) if key else None
                return self.store.register({k:payload[k] for k in ('baseline','settings','name') if k in payload},account_id,payload.get('user_id') or None,identifier)
            if len(parts)==3 and parts[1]=='instances':
                if method=='GET':return dict(self.store.get(parts[2]),**self.store.owner(parts[2]))
                return self.store.configure(parts[2],payload)
            raise KeyError('Endpoint not found')
        if path=='/schema' and method=='GET':
            if not admin:raise Forbidden('Configuration is administrator-only')
            return self.store.schema()
        if path=='/templates' and method=='GET':
            if not admin and 'text' not in p['scopes']:raise Forbidden('Text scope required')
            return {'templates':self.catalog.list_templates(p.get('account_id'),admin=admin)}
        if len(parts)==3 and parts[0]=='templates' and parts[2]=='copy' and method=='POST':
            if not admin and 'text' not in p['scopes']:raise Forbidden('Text scope required')
            account_id=self.account(payload['account_id']) if admin else p['account_id']
            user_id=payload.get('user_id') if admin else p.get('user_id')
            if user_id:self.access.provision_user(account_id,user_id)
            key=payload.get('idempotency_key')
            if not isinstance(key,str) or not 1<=len(key)<=128:raise ValueError('idempotency_key required')
            result=self.copy_template(parts[1],account_id,user_id,key,payload,admin)
            return result if admin else text_view(result)
        if path=='/characters/describe' and method=='POST':
            if self.generator is None:raise Unavailable('Description creation provider is not configured')
            account_id=self.account(payload['account_id']) if admin else p['account_id']
            user_id=payload.get('user_id') if admin else p.get('user_id')
            if user_id:self.access.provision_user(account_id,user_id)
            request={k:payload[k] for k in ('description','draft_id','answers','idempotency_key') if k in payload}
            key=request.get('idempotency_key')
            if not isinstance(key,str) or not 1<=len(key)<=128:raise ValueError('idempotency_key required')
            def create():
                result=self.catalog.create_from_description(account_id,user_id,request,self.generator)
                if result['status']=='complete':
                    template_id=result['template']['id']
                    config=self.catalog.configuration_for_copy(template_id,account_id,admin=True)
                    identifier=str(uuid.uuid5(uuid.NAMESPACE_URL,json.dumps(['description',account_id,user_id,result['draft_id']])))
                    instance=self.store.register(config,account_id,user_id,identifier)
                    result=dict(result,instance=instance if admin else text_view(instance))
                return result
            return self.charged(p,'create_description','description:'+key,request,create)
        if path=='/personalities' and method=='GET':
            if not admin and 'text' not in p['scopes']:raise Forbidden('Text scope required')
            rows=self.store.catalog(p.get('account_id'),p.get('user_id'),admin)
            return {'instances':rows if admin else [{k:r[k] for k in ('id','name','turn')} for r in rows]}
        if path=='/personalities' and method=='POST':
            if not admin:raise Forbidden('Use a permitted template or description creation')
            return self.store.register(payload)
        if len(parts)>=2 and parts[0]=='personalities':
            identifier=parts[1];owner=self.owned(p,identifier)
            if not admin and 'text' not in p['scopes']:raise Forbidden('Text scope required')
            if len(parts)==2 and method=='GET':
                result=self.store.get(identifier,'both' if admin else 'text')
                return result if admin else text_view(result)
            if parts[2:]==['history'] and method=='GET':
                result=self.store.history(identifier,int(q('after') or 0))
                return result if admin else {'turns':[text_view(t) for t in result['turns']]}
            if parts[2:] in (['status','users'],['users']) and method=='GET':
                result=self.store.relationships(identifier,q('user_id'))
                return result if admin else {'users':[user_text(u) for u in result['users']]}
            if parts[2:] in (['events'],['events','users']) and method=='POST':
                if not isinstance(payload,dict):raise ValueError('JSON object required')
                key=payload.get('idempotency_key')
                if not isinstance(key,str) or not 1<=len(key)<=128:raise ValueError('idempotency_key required')
                charged_principal=dict(p,user_id=owner['user_id']) if not admin else p
                result=self.charged(charged_principal,'text','event:'+identifier+':'+key,payload,lambda:self.store.events(identifier,payload))
                return result if admin else text_view(result)
        raise KeyError('Endpoint not found')
