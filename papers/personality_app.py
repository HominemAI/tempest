"""Local personality and delta editor. Run: python3 papers/personality_app.py"""
import argparse
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
import sqlite3
import threading
from urllib.parse import urlparse, parse_qs
import uuid

import delta_workflow as workflow
from random_baseline_personalities import validate_profile

ROOT = Path(__file__).resolve().parent
DATA = ROOT / 'personality-data'
LOCK = threading.RLock()
DIMENSIONS = workflow.read(workflow.BASELINES / 'dimensions.json')


def stamp():
    return datetime.now(timezone.utc).isoformat()


def db():
    DATA.mkdir(exist_ok=True)
    connection = sqlite3.connect(DATA / 'personalities.sqlite', timeout=30)
    connection.row_factory = sqlite3.Row
    connection.executescript('''
      CREATE TABLE IF NOT EXISTS records (
        id TEXT PRIMARY KEY, kind TEXT NOT NULL, parent TEXT NOT NULL,
        name TEXT NOT NULL, body TEXT NOT NULL, source TEXT NOT NULL,
        revision INTEGER NOT NULL DEFAULT 1, updated TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS history (
        id TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL,
        name TEXT NOT NULL, saved TEXT NOT NULL, PRIMARY KEY(id,revision));
      CREATE TABLE IF NOT EXISTS imports (path TEXT PRIMARY KEY, signature TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS record_kind_parent ON records(kind,parent);
    ''')
    return connection


def validate_vector(values, bounded=True):
    if not isinstance(values, list) or len(values) != 23 or any(
        type(v) not in (int, float) or not math.isfinite(v) or (bounded and not -10 <= v <= 10)
        for v in values):
        raise ValueError('Supply 23 finite numbers' + (' between -10 and 10.' if bounded else '.'))


def validate(kind, body):
    if kind == 'profile':
        validate_vector(body['state_values'])
        if not isinstance(body.get('description'), str):
            raise ValueError('A description must be text.')
    elif kind == 'delta':
        validate_vector(body['starting_state'])
        validate_vector(body['label']['adjustments'], bounded=False)
        try:
            workflow.emotion.validate(body['label'], workflow.emotion.SCHEMA)
        except (AssertionError, KeyError, TypeError):
            raise ValueError('Delta extras must match the original output schema.') from None
        if not isinstance(body.get('scenario'), str) or not body['scenario'].strip():
            raise ValueError('Event text cannot be empty.')
    else:
        raise ValueError('Unknown record type.')


def serialize(row):
    value = dict(row)
    value['body'] = json.loads(value['body'])
    return value


def add(connection, identifier, kind, parent, name, body, source=''):
    validate(kind, body)
    connection.execute('INSERT OR IGNORE INTO records VALUES (?,?,?,?,?,?,1,?)',
                       (identifier, kind, parent, name, json.dumps(body), source, stamp()))


def refresh():
    """Import new results only; repeated imports never overwrite human edits."""
    with LOCK, db() as connection:
        expected = {p['request_id']: p for p in workflow.read(workflow.BASELINES/'profiles.json')}
        rejected = 0
        for path in sorted((workflow.BASELINES/'downloaded-output').glob('*.jsonl')):
            signature = f'{path.stat().st_mtime_ns}:{path.stat().st_size}'
            previous = connection.execute('SELECT signature FROM imports WHERE path=?',(str(path),)).fetchone()
            if previous and previous[0] == signature:
                continue
            for raw in workflow.jsonl(path):
                cid = raw.get('custom_id')
                try:
                    profile = validate_profile(workflow.unpack_vertex(raw), expected[cid])
                    add(connection, cid, 'profile', '', cid.replace('baseline-', 'Personality '), profile, str(path))
                except (ValueError, KeyError, TypeError, IndexError):
                    rejected += 1
            connection.execute('INSERT OR REPLACE INTO imports VALUES (?,?)',(str(path),signature))
        sources = [workflow.BATCHES/'crowd-envent-v1/labels.jsonl']
        for stage in ('neutral-situations','neutral-stories','baseline-0001-deltas'):
            manifest = workflow.WORK/stage/'manifest.json'
            if manifest.exists():
                sources += [Path(p)/'labels.jsonl' for p in workflow.read(manifest)['jobs']]
        for path in sources:
            if not path.exists():
                continue
            signature = f'{path.stat().st_mtime_ns}:{path.stat().st_size}'
            previous = connection.execute('SELECT signature FROM imports WHERE path=?',(str(path),)).fetchone()
            if previous and previous[0] == signature:
                continue
            for body in workflow.jsonl(path):
                body.setdefault('starting_state', [0.0]*23)
                parent = body.get('baseline_profile_id') or 'neutral'
                add(connection, body['custom_id'], 'delta', parent, body['scenario'][:100], body, str(path))
            connection.execute('INSERT OR REPLACE INTO imports VALUES (?,?)',(str(path),signature))
        counts = {r[0]:r[1] for r in connection.execute('SELECT kind,count(*) FROM records GROUP BY kind')}
        return dict(counts=counts, skipped_invalid_profiles_this_refresh=rejected)


def get_record(identifier):
    with db() as connection:
        row = connection.execute('SELECT * FROM records WHERE id=?',(identifier,)).fetchone()
        if not row:
            raise ValueError('Record not found.')
        return serialize(row)


def save_record(request):
    with LOCK, db() as connection:
        identifier = request.get('id')
        if identifier:
            row = connection.execute('SELECT * FROM records WHERE id=?',(identifier,)).fetchone()
            if not row or request.get('revision') != row['revision']:
                raise ValueError('This record changed. Reload it before saving.')
            kind, parent = row['kind'], row['parent']
        else:
            kind, parent = 'profile', ''
            identifier = 'personality-' + uuid.uuid4().hex[:12]
        body = request['body']
        validate(kind, body)
        if kind == 'delta':
            original = json.loads(row['body'])
            for key in ('custom_id','starting_state','baseline_profile_id','split_group','parent_id','example_id'):
                if body.get(key) != original.get(key):
                    raise ValueError('Delta provenance cannot be changed: ' + key)
        name = str(request.get('name','')).strip()[:180]
        if not name:
            raise ValueError('Give this record a name.')
        if kind == 'profile':
            body['request_id'] = identifier
        if request.get('id'):
            # The canonical original record is updated in place, not forked.
            connection.execute('INSERT INTO history VALUES (?,?,?,?,?)',
                               (identifier,row['revision'],row['body'],row['name'],stamp()))
            connection.execute('UPDATE records SET name=?,body=?,revision=revision+1,updated=? WHERE id=?',
                               (name,json.dumps(body),stamp(),identifier))
        else:
            add(connection,identifier,kind,parent,name,body)
    return get_record(identifier)


def training_edits():
    """Canonical edited deltas, consumed when assembling the next training set."""
    if not (DATA/'personalities.sqlite').exists():
        return {}
    with db() as connection:
        return {r['id']:json.loads(r['body']) for r in connection.execute(
            "SELECT id,body FROM records WHERE kind='delta' AND revision>1")}


def generate(request):
    mode = request['mode']
    values = request.get('state_values')
    guide = (workflow.BASELINES/'prompt.md').read_text()
    cid = 'draft'
    if mode == 'values':
        validate_vector(values)
        payload = dict(request_id=cid, state_values=values)
    elif mode == 'description':
        description = request.get('description','').strip()
        if not description or len(description)>12000:
            raise ValueError('Enter a character description of at most 12,000 characters.')
        guide += ('\nFor this task only, propose the 23 state_values from the supplied character description. '
                  'This is one plausible fictional interpretation, not an inverse measurement. '
                  'Use values in [-10,10] and include all normal output fields. request_id must be draft.')
        payload = dict(request_id=cid, character_description=description)
    else:
        raise ValueError('Unknown generation mode.')
    from vertex_literature_runner import api
    response = api('https://us-central1-aiplatform.googleapis.com/v1/projects/smg-cluster/locations/us-central1/endpoints/openapi/chat/completions',
                   json.dumps(dict(model='openai/gpt-oss-120b-maas',stream=False,
                     messages=[dict(role='system',content=guide),dict(role='user',content=json.dumps(payload))])).encode())
    choice = response['choices'][0]
    if choice.get('finish_reason') != 'stop':
        raise ValueError('Generation did not finish. No changes saved; do not blindly retry.')
    result = json.loads(choice['message']['content'])
    validate_vector(result['state_values'])
    validate_profile(result,dict(request_id=cid,state_values=values if mode=='values' else result['state_values']))
    return result


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def respond(self, value, status=200):
        raw = json.dumps(value,allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type','application/json')
        self.send_header('Cache-Control','no-store')
        self.send_header('Content-Length',str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def permitted(self):
        host = self.headers.get('Host','')
        if host not in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}'):
            return False
        return self.headers.get('Origin') in (None,'http://'+host)

    def do_GET(self):
        if not self.permitted():
            return self.respond({'error':'Local access only.'},403)
        route = urlparse(self.path)
        q = parse_qs(route.query)
        try:
            if route.path == '/':
                raw = (ROOT/'personality-explorer.html').read_bytes()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Length',str(len(raw)))
                self.send_header('X-Content-Type-Options','nosniff')
                self.end_headers()
                return self.wfile.write(raw)
            if route.path == '/api/meta':
                return self.respond(dict(dimensions=DIMENSIONS, model='GCP gpt-oss-120b', counts=counts()))
            if route.path == '/api/record':
                return self.respond(get_record(q['id'][0]))
            if route.path == '/api/history':
                with db() as c:
                    rows = [dict(r) for r in c.execute('SELECT revision,name,saved FROM history WHERE id=? ORDER BY revision DESC',(q['id'][0],))]
                return self.respond(rows)
            if route.path == '/api/list':
                kind = q.get('kind',['profile'])[0]
                search = '%'+q.get('q',[''])[0]+'%'
                offset = max(0,int(q.get('offset',['0'])[0]))
                where, params = 'kind=? AND (name LIKE ? OR body LIKE ? OR id LIKE ?)', [kind,search,search,search]
                if kind == 'delta':
                    where += ' AND parent=?'
                    params.append(q.get('parent',['baseline-0001'])[0])
                with db() as c:
                    total = c.execute('SELECT count(*) FROM records WHERE '+where,params).fetchone()[0]
                    rows = [dict(r) for r in c.execute('SELECT id,name,revision,updated FROM records WHERE '+where+' ORDER BY id LIMIT 60 OFFSET ?',params+[offset])]
                return self.respond(dict(total=total,rows=rows))
            return self.respond({'error':'Not found'},404)
        except (ValueError,KeyError) as exc:
            self.respond({'error':str(exc)},400)

    def do_POST(self):
        if not self.permitted() or self.headers.get('Content-Type') != 'application/json':
            return self.respond({'error':'Same-origin JSON required.'},403)
        try:
            length = int(self.headers.get('Content-Length','0'))
            if not 0 < length <= 1000000:
                raise ValueError('Request too large or empty.')
            request = json.loads(self.rfile.read(length))
            if self.path == '/api/save':
                return self.respond(save_record(request))
            if self.path == '/api/refresh':
                return self.respond(refresh())
            if self.path == '/api/generate':
                return self.respond(generate(request))
            if self.path == '/api/restore':
                with db() as c:
                    row = c.execute('SELECT * FROM history WHERE id=? AND revision=?',(request['id'],request['restore_revision'])).fetchone()
                if not row:
                    raise ValueError('Revision not found.')
                return self.respond(save_record(dict(id=request['id'],revision=request['revision'],name=row['name'],body=json.loads(row['body']))))
            return self.respond({'error':'Not found'},404)
        except (ValueError,KeyError,TypeError,AssertionError) as exc:
            self.respond({'error':str(exc) or 'Invalid document.'},400)
        except Exception:
            self.respond({'error':'Operation failed. No automatic retry was made. Check local credentials or server availability.'},500)


def counts():
    with db() as c:
        return {r[0]:r[1] for r in c.execute('SELECT kind,count(*) FROM records GROUP BY kind')}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8767)
    args = parser.parse_args()
    print(refresh(),flush=True)
    print(f'Personality explorer: http://127.0.0.1:{args.port}',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()
