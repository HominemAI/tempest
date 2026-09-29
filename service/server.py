"""Tempest HTTP API. Run one process per SQLite database/model instance."""
import argparse
import hmac
import json
import logging
import os
from pathlib import Path
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

from core import Store, Conflict, ROOT

class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass  # Do not log event text or credentials.

    def respond(self,status,value):
        data=json.dumps(value,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(data)))
        self.send_header('Cache-Control','no-store')
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self): self.handle_api('GET')
    def do_POST(self): self.handle_api('POST')

    def handle_api(self,method):
        route=urlparse(self.path)
        if method=='GET' and route.path=='/healthz':return self.respond(200,{'status':'ready'})
        token=self.headers.get('Authorization','')
        if not hmac.compare_digest(token,'Bearer '+self.server.token):return self.respond(401,{'error':'Authentication required'})
        try:
            parts=route.path.strip('/').split('/');query=parse_qs(route.query)
            store=self.server.store
            payload=None
            if method=='POST':
                if self.headers.get_content_type()!='application/json':return self.respond(415,{'error':'JSON required'})
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=1_000_000:raise ValueError('Request size must be 1–1,000,000 bytes')
                self.connection.settimeout(30)
                payload=json.loads(self.rfile.read(length))
            if method=='GET' and parts==['schema']:return self.respond(200,store.schema())
            if method=='POST' and parts==['personalities']:return self.respond(201,store.register(payload))
            if len(parts)>=2 and parts[0]=='personalities':
                identifier=parts[1]
                if method=='GET' and len(parts)==2:return self.respond(200,store.get(identifier,query.get('format',['both'])[0]))
                if method=='POST' and parts[2:]==['events']:return self.respond(200,store.events(identifier,payload))
                if method=='GET' and parts[2:]==['history']:return self.respond(200,store.history(identifier,int(query.get('after',['0'])[0])))
            self.respond(404,{'error':'Endpoint not found'})
        except Conflict as exc:self.respond(409,{'error':str(exc)})
        except KeyError:self.respond(404,{'error':'Personality not found'})
        except (ValueError,TypeError,AssertionError) as exc:self.respond(400,{'error':str(exc)})
        except (BrokenPipeError,ConnectionResetError):pass
        except Exception:
            logging.exception('Request failed')
            self.respond(500,{'error':'Internal error. Retry with the same idempotency key.'})

def make_server(host,port,token,store):
    server=ThreadingHTTPServer((host,port),Handler)
    server.token=token;server.store=store
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
    store=Store(os.environ.get('TEMPEST_DB','/data/tempest.sqlite'),lambda event,state:predict(model,event,state)['categories'],model.name)
    if config['dimensions']!=store.dimensions:raise ValueError('Checkpoint/index dimension mismatch')
    server=make_server(args.host,args.port,token,store)
    print(f'Tempest API listening on {args.host}:{args.port}',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

if __name__=='__main__':main()
