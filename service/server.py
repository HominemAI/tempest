"""Tempest HTTP API. Run one process per SQLite database/model instance."""
import argparse
import hmac
import json
import logging
import os
import time
from pathlib import Path
import sys
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from core import Store, Conflict, ROOT
from database import Database, DatabaseUnavailable
from access import Access, AccessError, Unauthorized, Forbidden
from catalog import Catalog
from api import API
from creator import configured_generator
import hosted

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass  # Do not log event text or credentials.

    def respond(self,status,value,headers=None):
        if isinstance(value,dict) and 'server_turn_seconds' in value and hasattr(self,'request_started'):
            value['server_http_seconds']=time.perf_counter()-self.request_started
        data=json.dumps(value,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        for k,v in (headers or {}).items():self.send_header(k,v)
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self): self.handle_api('GET')
    def do_POST(self): self.handle_api('POST')

    def resource(self,data,mime):
        self.send_response(200);self.send_header('Content-Type',mime)
        self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.send_header('Referrer-Policy','same-origin')
        self.end_headers();self.wfile.write(data)

    def handle_api(self,method):
        self.request_started=time.perf_counter()
        route=urlparse(self.path)
        if method=='GET' and route.path=='/healthz':return self.respond(200,{'status':'ready'})
        try:
            access=self.server.access
            cookies=SimpleCookie();cookies.load(self.headers.get('Cookie',''))
            session=cookies.get('tempest_session');session=session.value if session else ''
            payload=None
            if method=='POST':
                origin=self.headers.get('Origin')
                expected=self.server.public_origin or ('http://'+self.headers.get('Host',''))
                if origin is not None and origin!=expected:raise Forbidden('Cross-origin request rejected')
                if self.headers.get_content_type()!='application/json':return self.respond(415,{'error':'JSON required'})
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=100_000:raise ValueError('Request size must be 1–100,000 bytes')
                self.connection.settimeout(30)
                payload=json.loads(self.rfile.read(length))
                if not isinstance(payload,dict):raise ValueError('JSON object required')
            if method=='GET' and route.path in ('/assets/theme.js','/assets/tempest.css','/assets/inference.css'):
                filename=ROOT/'papers/web'/route.path.rsplit('/',1)[1]
                return self.resource(filename.read_bytes(),'text/javascript' if filename.suffix=='.js' else 'text/css')
            if method=='GET' and route.path in ('/','/admin','/assets/admin.js','/assets/admin.css'):
                name={'/':'admin.html','/admin':'admin.html','/assets/admin.js':'admin.js','/assets/admin.css':'admin.css'}[route.path]
                data=(Path(__file__).parent/'web'/name).read_bytes()
                return self.resource(data,{'html':'text/html; charset=utf-8','js':'text/javascript','css':'text/css'}[name.rsplit('.',1)[1]])
            if route.path=='/auth/status' and method=='GET':return self.respond(200,access.status())
            if route.path in ('/auth/setup','/auth/login') and method=='POST':
                if route.path.endswith('/setup'):
                    result=access.setup(payload.get('username'),payload.get('password'),payload.get('bootstrap_token'),peer=self.client_address[0])
                else:result=access.login(payload.get('username'),payload.get('password'),peer=self.client_address[0])
                secure='; Secure' if self.server.secure_cookie else ''
                cookie='tempest_session='+result['session_token']+'; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200'+secure
                return self.respond(200,{'kind':'admin','username':result['principal']['username'],'csrf':result['csrf']},{'Set-Cookie':cookie})
            if method=='GET' and route.path in ('/inference','/state-index'):
                try:principal=access.authenticate_session(session)
                except Unauthorized:
                    self.send_response(303);self.send_header('Location','/admin');self.send_header('Cache-Control','no-store');self.end_headers();return
                return self.resource(*hosted.resource(route.path,principal))
            if route.path.startswith('/auth/'):
                principal=access.authenticate_session(session)
            elif self.headers.get('Authorization','').startswith('Bearer '):
                principal=access.authenticate_key(self.headers['Authorization'][7:])
            else:principal=access.authenticate_session(session)
            if method=='POST' and 'session_id' in principal:
                if not hmac.compare_digest(self.headers.get('X-CSRF-Token',''),principal['csrf']):raise Forbidden('Invalid CSRF token')
            if route.path=='/auth/me' and method=='GET':return self.respond(200,{'kind':'admin','username':principal['username'],'csrf':principal['csrf']})
            if route.path=='/auth/logout' and method=='POST':
                access.logout(session)
                return self.respond(200,{'logged_out':True},{'Set-Cookie':'tempest_session=; Max-Age=0; HttpOnly; SameSite=Strict; Path=/'+('; Secure' if self.server.secure_cookie else '')})
            if method=='GET':
                resource=hosted.resource(route.path,principal)
                if resource is not None:return self.resource(*resource)
            result=hosted.dispatch(method,route.path,parse_qs(route.query),principal)
            if result is None:result=self.server.api.dispatch(method,route.path,parse_qs(route.query),payload,principal)
            return self.respond(200,result)
        except AccessError as exc:self.respond(exc.status,{'error':str(exc)})
        except DatabaseUnavailable:self.respond(503,{'error':'Database is temporarily busy or unavailable. Retry with the same idempotency key.'})
        except PermissionError:self.respond(403,{'error':'Access denied'})
        except Conflict as exc:self.respond(409,{'error':str(exc)})
        except KeyError:self.respond(404,{'error':'Personality not found'})
        except (ValueError,TypeError,AssertionError) as exc:self.respond(400,{'error':str(exc)})
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception:
            logging.exception('Request failed')
            self.respond(500,{'error':'Internal error. Retry with the same idempotency key.'})

def make_server(host,port,token,store,secure_cookie=True,public_origin=None,generator=None):
    server=ThreadingHTTPServer((host,port),Handler)
    server.store=store;server.secure_cookie=secure_cookie;server.public_origin=public_origin
    server.access=Access(store.database,token)
    server.catalog=Catalog(store.database,account_exists=lambda identifier:any(a['id']==identifier for a in server.access.list_accounts()))
    server.api=API(store,server.access,server.catalog,generator)
    return server

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--host',default='127.0.0.1');parser.add_argument('--port',type=int,default=8080)
    args=parser.parse_args()
    token=os.environ.get('TEMPEST_API_TOKEN','')
    if len(token)<24:parser.error('Set TEMPEST_API_TOKEN to at least 24 characters')
    model=Path(os.environ.get('TEMPEST_MODEL_DIR',str(ROOT/'papers/emotion_training/artifacts/jev-discrete-v1-512-20260927')))
    sys.path.insert(0,str(ROOT/'papers/emotion_training'))
    from predict_jev_discrete import load_model, predict
    config,_,_=load_model(model)  # Readiness means weights/encoder loaded successfully.
    database=Database(os.environ.get('TEMPEST_DB','/data/tempest.sqlite'))
    server=None
    try:
        database.open(wait=True)
        store=Store(os.environ.get('TEMPEST_DB','/data/tempest.sqlite'),lambda event,state:predict(model,event,state)['categories'],model.name,database=database)
        if config['dimensions']!=store.dimensions:raise ValueError('Checkpoint/index dimension mismatch')
        server=make_server(args.host,args.port,token,store,public_origin=os.environ.get('TEMPEST_PUBLIC_ORIGIN'),generator=configured_generator(store.mapping_data))
        print(f'Tempest API listening on {args.host}:{args.port}',flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass
    finally:
        if server is not None:server.server_close()
        database.close()

if __name__=='__main__':main()
