#!/usr/bin/env python3
"""Reusable local subject concordance, CPU vector index, and standalone explorer."""
from __future__ import annotations
import argparse, bisect, csv, hashlib, io, json, os, re, sqlite3, tempfile, threading, time
from datetime import datetime, timezone
from concurrent.futures import ProcessPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

MODEL = 'BAAI/bge-small-en-v1.5'
HERE = Path(__file__).resolve().parent

def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))

def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()

def explorer_html(payload):
    template=(HERE/'subject-explorer.template.html').read_text(encoding='utf-8')
    library=(HERE/'vendor/marked.umd.js').read_text(encoding='utf-8').replace('</script', '<\\/script')
    return template.replace('__MARKDOWN_LIBRARY__',library).replace('__INDEX_DATA__',payload)

def safe_source(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or path.suffix.lower() != '.md':
        raise ValueError(f'Expected Markdown inside corpus: {relative}')
    return path

def paragraphs(text):
    """Physical Markdown line numbers survive paragraph extraction unchanged."""
    lines = text.splitlines()
    result, block = [], []
    page, section, start = None, '', 1
    converted = any(re.fullmatch(r'## Page \d+', line.strip()) for line in lines)
    active = not converted
    def flush():
        if block:
            result.append(dict(text='\n'.join(block), start=start, end=start+len(block)-1,
                               page=page, section=section))
            block.clear()
    for number, line in enumerate(lines, 1):
        match = re.fullmatch(r'## Page (\d+)', line.strip())
        if match:
            flush(); page = int(match[1]); active = True; continue
        if not active:
            continue
        if not line.strip() or re.fullmatch(r'\s*[-*_]{3,}\s*', line):
            flush(); continue
        if line.startswith('#'):
            flush(); section = line.lstrip('# ').strip('*_')
        if not block:
            start = number
        block.append(line)
    flush()
    return result

def normalize(text):
    """Normalized matching surface plus a map back to original character offsets."""
    chars, offsets = [], []
    # Join PDF line-end hyphenation and remove formatting markers, retaining provenance.
    skip = set()
    for m in re.finditer(r'(?<=\w)[-\u00ad]\s*\n\s*(?=\w)', text):
        skip.update(range(m.start(), m.end()))
    for i, char in enumerate(text):
        if i in skip or char in '*_`\u00ad':
            continue
        char = {'ﬁ':'fi','ﬂ':'fl','–':'-','‑':'-','−':'-'}.get(char, char)
        for c in char:
            chars.append(c); offsets.append(i)
    return ''.join(chars), offsets

def patterns(terms):
    result = []
    ids = set()
    for term in terms:
        if term['id'] in ids:
            raise ValueError(f'Duplicate term id: {term["id"]}')
        ids.add(term['id'])
        for kind, aliases in [('name', [term['name']]), ('alias', term.get('aliases', [])),
                              ('related term', term.get('related_terms', []))]:
            for alias in aliases:
                flags = 0 if len(alias) <= 5 and any(c.isupper() for c in alias) else re.I
                pattern = re.escape(alias).replace(r'\ ', r'\s+').replace(r'\-', r'[-\s]?')
                result.append((re.compile(r'(?<!\w)'+pattern+r'(?!\w)', flags), term, kind))
    return result

def extract(document, compiled, radius=3):
    blocks = paragraphs(document['text'])
    for index, block in enumerate(blocks):
        normalized, offsets = normalize(block['text'])
        candidates = []
        for pattern, term, kind in compiled:
            for match in pattern.finditer(normalized):
                candidates.append((match.start(), match.end(), term, kind))
        # Longest overlapping entity wins; e.g. noradrenaline is not adrenaline.
        occupied = set()
        selected = []
        for begin, end, term, kind in sorted(candidates, key=lambda m: (-(m[1]-m[0]), m[0])):
            if occupied.intersection(range(begin, end)):
                continue
            occupied.update(range(begin, end)); selected.append((begin, end, term, kind))
        for begin, end, term, kind in sorted(selected, key=lambda m:m[0]):
            a, b = offsets[begin], offsets[end-1]+1
            before = blocks[max(0,index-radius):index]
            after = blocks[index+1:index+radius+1]
            yield dict(document_id=document['id'], term_id=term['id'], term=term['name'],
                       category=term['category'], origin=term.get('origin','unspecified'),
                       matched_text=block['text'][a:b], match_kind=kind,
                       line=block['start']+block['text'][:a].count('\n'),
                       end_line=block['start']+block['text'][:b].count('\n'),
                       page=block['page'], section=block['section'],
                       before=before, paragraph=block, after=after)

def embedder(cache):
    os.environ.setdefault('HF_HUB_DISABLE_TELEMETRY','1')
    os.environ.setdefault('HF_HUB_DISABLE_XET','1')
    from fastembed import TextEmbedding
    return TextEmbedding(MODEL, cache_dir=str(cache), threads=2,
                         providers=['CPUExecutionProvider'], local_files_only=True)

def vectorize(texts, model, progress=True):
    """Token-window long contexts rather than silently discarding trailing paragraphs."""
    import numpy as np
    from tokenizers import Tokenizer
    tokenizer = Tokenizer.from_str(model.model.tokenizer.to_str())
    tokenizer.no_truncation(); tokenizer.no_padding()
    for i, text in enumerate(texts):
        tokens = tokenizer.encode(text, add_special_tokens=False).ids
        windows = [tokenizer.decode(tokens[j:j+440]) for j in range(0,max(1,len(tokens)),400)]
        vector = np.mean(list(model.embed(windows, batch_size=16)), axis=0)
        vector /= max(float(np.linalg.norm(vector)),1e-12)
        yield vector.astype('<f4').tobytes()
        if progress and (i+1) % 50 == 0:
            print(f'Embedded {i+1}/{len(texts)} contexts', flush=True)

def init_embedding_worker(cache):
    global worker_model
    worker_model = embedder(Path(cache))

def embed_chunk(texts):
    return list(vectorize(texts, worker_model, progress=False))

def parallel_vectors(texts, cache, workers):
    """Keep input ordering and identical pooling; each process uses two CPU threads."""
    chunks = [texts[i:i+16] for i in range(0, len(texts), 16)]
    done = 0
    with ProcessPoolExecutor(max_workers=workers, initializer=init_embedding_worker,
                             initargs=(str(cache),)) as pool:
        for results in pool.map(embed_chunk, chunks):
            yield from results
            done += len(results)
            print(f'Embedded {done}/{len(texts)} contexts', flush=True)

def build(args):
    root = args.corpus.resolve()
    out = args.output.resolve(); out.mkdir(parents=True, exist_ok=True)
    vocabulary = read_json(args.vocabulary)
    terms = vocabulary.get('terms', vocabulary.get('chemicals', []))
    if not terms:
        raise ValueError('Vocabulary needs a nonempty terms (or chemicals) array')
    label_map = read_json(args.labels).get('documents', {}) if args.labels else {}
    catalog = read_json(args.catalog)
    docs = []
    for paper in catalog['papers']:
        relative = paper['markdown']
        content = safe_source(root, relative).read_text(encoding='utf-8')
        labels = label_map.get(relative, {}).get('labels', [paper.get('area','unclassified')])
        if not labels:
            raise ValueError(f'Empty paper labels: {relative}')
        docs.append(dict(id=digest(relative)[:16], title=paper['title'],
                         authors=paper.get('authors',''), year=paper.get('year',''),
                         field=paper.get('area','unclassified'), type=paper.get('type','unspecified').split(';')[0].strip(),
                         catalog_details=paper.get('type','unspecified'),
                         labels=sorted(set(labels)), path=relative, sha256=digest(content), text=content))
    if len({d['id'] for d in docs}) != len(docs):
        raise ValueError('Duplicate document paths')
    compiled = patterns(terms)
    mentions = []
    contexts = {}
    for doc in docs:
        for row in extract(doc, compiled, args.context):
            row['id'] = len(mentions)
            context = '\n\n'.join(p['text'] for p in row['before']+[row['paragraph']]+row['after'])
            embedding_text = doc['title']+'\n'+row['term']+'\n'+context
            key = digest(embedding_text)
            row['vector_id'] = key
            contexts.setdefault(key, embedding_text)
            mentions.append(row)
        print(f"Extracted: {doc['title'][:85]}", flush=True)
    print(f'{len(docs)} documents; {len(mentions)} mentions; {len(contexts)} unique vector contexts',flush=True)
    vectors = {}
    existing = out/'subject-index.sqlite'
    if existing.exists():
        with sqlite3.connect(existing) as previous:
            try:
                if previous.execute("SELECT value FROM metadata WHERE key='model'").fetchone()[0] == MODEL:
                    vectors = {k:v for k,v in previous.execute('SELECT id, embedding FROM vectors') if k in contexts}
            except (sqlite3.Error, TypeError):
                pass
    # Durable intermediate cache: interrupted large builds can resume embeddings.
    checkpoint = out / '.subject-index-vectors'
    checkpoint.mkdir(exist_ok=True)
    with sqlite3.connect(checkpoint / 'embeddings.sqlite') as cached:
        cached.execute('CREATE TABLE IF NOT EXISTS embeddings(model TEXT,id TEXT,embedding BLOB,PRIMARY KEY(model,id))')
        for key, vector in cached.execute('SELECT id,embedding FROM embeddings WHERE model=?', (MODEL,)):
            if key in contexts and key not in vectors:
                vectors[key] = vector
        pending = [key for key in contexts if key not in vectors]
        print(f'{len(vectors)} cached vectors; {len(pending)} new contexts to embed', flush=True)
        if pending:
            texts = [contexts[k] for k in pending]
            workers = getattr(args, 'embedding_workers', 1)
            delay = getattr(args, 'embedding_delay', 0)
            if delay:
                workers = 1
                print(f'Embedding throttle: {delay:g} seconds between contexts; one worker', flush=True)
            generated = (parallel_vectors(texts, args.cache.resolve(), workers) if workers > 1
                         else vectorize(texts, embedder(args.cache.resolve())))
            for i, (key, vector) in enumerate(zip(pending, generated)):
                vectors[key] = vector
                cached.execute('INSERT OR REPLACE INTO embeddings VALUES (?,?,?)', (MODEL,key,vector))
                if (i+1) % 16 == 0:
                    cached.commit()
                if delay and i + 1 < len(pending):
                    cached.commit()
                    time.sleep(delay)
    meta = dict(title=args.title, built=datetime.now(timezone.utc).isoformat(), model=MODEL,
                dimensions=384, context_radius=args.context, vocabulary_sha256=digest(json.dumps(vocabulary)),
                term_count=len(terms), note='Vocabulary matches, not evidence of causal effects. Labels describe papers; origin is a vocabulary grouping.')
    # Build in staging; leave an existing usable index intact if embedding/extraction fails.
    with tempfile.TemporaryDirectory(prefix='.subject-index-',dir=out) as temp:
        stage = Path(temp)
        with sqlite3.connect(stage/'subject-index.sqlite') as db:
            db.executescript('''
              PRAGMA foreign_keys=ON;
              CREATE TABLE metadata(key TEXT PRIMARY KEY,value TEXT);
              CREATE TABLE documents(id TEXT PRIMARY KEY,title TEXT,authors TEXT,year TEXT,field TEXT,type TEXT,catalog_details TEXT,path TEXT UNIQUE,sha256 TEXT,text TEXT);
              CREATE TABLE document_labels(document_id TEXT REFERENCES documents(id),label TEXT,PRIMARY KEY(document_id,label));
              CREATE TABLE terms(id TEXT PRIMARY KEY,name TEXT,category TEXT,origin TEXT,definition_json TEXT);
              CREATE TABLE vectors(id TEXT PRIMARY KEY,embedding BLOB,input_text TEXT);
              CREATE TABLE mentions(id INTEGER PRIMARY KEY,document_id TEXT REFERENCES documents(id),term_id TEXT REFERENCES terms(id),line INTEGER,end_line INTEGER,page INTEGER,match_kind TEXT,matched_text TEXT,section TEXT,vector_id TEXT REFERENCES vectors(id),context_json TEXT);
              CREATE INDEX mentions_document ON mentions(document_id,line);
              CREATE INDEX mentions_term ON mentions(term_id);
              CREATE VIEW reference_index AS SELECT m.*,d.title,d.authors,d.year,d.field,d.type,d.catalog_details,d.path,t.name AS term,t.category,t.origin,(SELECT group_concat(label,' | ') FROM document_labels WHERE document_id=d.id) AS labels FROM mentions m JOIN documents d ON d.id=m.document_id JOIN terms t ON t.id=m.term_id;
            ''')
            db.executemany('INSERT INTO metadata VALUES (?,?)',[(k,str(v)) for k,v in meta.items()])
            for doc in docs:
                db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?,?,?)',tuple(doc[k] for k in ['id','title','authors','year','field','type','catalog_details','path','sha256','text']))
                db.executemany('INSERT INTO document_labels VALUES (?,?)',[(doc['id'],label) for label in doc['labels']])
            db.executemany('INSERT INTO terms VALUES (?,?,?,?,?)',[(t['id'],t['name'],t['category'],t.get('origin','unspecified'),json.dumps(t)) for t in terms])
            db.executemany('INSERT INTO vectors VALUES (?,?,?)',[(k,v,contexts[k]) for k,v in vectors.items()])
            for m in mentions:
                db.execute('INSERT INTO mentions VALUES (?,?,?,?,?,?,?,?,?,?,?)',tuple(m[k] for k in ['id','document_id','term_id','line','end_line','page','match_kind','matched_text','section','vector_id'])+(json.dumps({k:m[k] for k in ['before','paragraph','after']},ensure_ascii=False),))
        (stage/'document-index.json').write_text(json.dumps({'documents':[{k:v for k,v in d.items() if k!='text'} for d in docs]},indent=2,ensure_ascii=False),encoding='utf-8')
        by_id = {d['id']:d for d in docs}
        fields = ['term','category','origin','matched_text','match_kind','title','labels','field','type','catalog_details','authors','year','path','line','end_line','page','section','before','paragraph','after']
        with (stage/'references.csv').open('w',newline='',encoding='utf-8') as stream:
            writer = csv.DictWriter(stream,fieldnames=fields); writer.writeheader()
            for m in mentions:
                row = {**by_id[m['document_id']],**m}
                row['labels'] = ' | '.join(row['labels'])
                for field in ['before','after']:
                    row[field] = '\n\n'.join(p['text'] for p in row[field])
                row['paragraph'] = row['paragraph']['text']
                # Spreadsheet-safe CSV. Exact source remains in SQLite and HTML.
                writer.writerow({k:("'"+str(row[k]) if str(row[k]).startswith(('=','+','-','@')) else row[k]) for k in fields})
        # Paths are relative to HTML, including when output is outside corpus.
        webdocs = [{**d,'link':os.path.relpath(root/d['path'],out)} for d in docs]
        payload = json.dumps(dict(metadata=meta,documents=webdocs,mentions=mentions),ensure_ascii=False).replace('<','\\u003c')
        (stage/'explorer.html').write_text(explorer_html(payload),encoding='utf-8')
        for path in stage.iterdir():
            os.replace(path,out/path.name)
    print(f'Ready: {out / "explorer.html"}',flush=True)

def search(database, cache, query, limit=100, model=None):
    import numpy as np
    model = model or embedder(cache)
    q = next(model.query_embed(query)).astype('float32')
    q /= max(float(np.linalg.norm(q)),1e-12)
    with sqlite3.connect(database) as db:
        rows = db.execute('SELECT id,embedding FROM vectors').fetchall()
        if not rows:
            return []
        matrix = np.stack([np.frombuffer(v,dtype='<f4') for _,v in rows])
        scores = dict(zip([k for k,_ in rows],(matrix@q).tolist()))
        results = [dict(id=i,score=scores[k]) for i,k in db.execute('SELECT id,vector_id FROM mentions')]
    return sorted(results,key=lambda r:-r['score'])[:limit]

def serve(args):
    from annotations import AnnotationStore
    output = args.output.resolve()
    notes = AnnotationStore(output)
    model = None
    lock = threading.Lock()
    class Handler(BaseHTTPRequestHandler):
        def allowed(self):
            allowed=(f'http://127.0.0.1:{args.port}',f'http://localhost:{args.port}')
            if self.headers.get('Host') not in (f'127.0.0.1:{args.port}',f'localhost:{args.port}'):
                self.send_error(403,'Loopback host required'); return False
            if self.headers.get('Origin') and self.headers['Origin'] not in allowed:
                self.send_error(403,'Same-origin requests required'); return False
            if self.headers.get('Sec-Fetch-Site')=='cross-site':
                self.send_error(403,'Cross-site requests denied'); return False
            return True

        def json_response(self, value, status=200):
            data=json.dumps(value,ensure_ascii=False).encode()
            self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Cache-Control','no-store');self.send_header('Content-Length',str(len(data)))
            self.end_headers();self.wfile.write(data)

        def do_POST(self):
            if not self.allowed(): return
            if urlparse(self.path).path!='/api/annotations': self.send_error(404);return
            if self.headers.get_content_type()!='application/json': self.send_error(415);return
            try:
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=2_000_000: raise ValueError('Annotation body must be under 2 MB')
                note=json.loads(self.rfile.read(length))
                self.json_response(notes.save(note))
            except (ValueError,KeyError,TypeError,AttributeError) as error:
                self.json_response({'error':str(error)},400)

        def do_GET(self):
            nonlocal model
            parsed = urlparse(self.path)
            try:
                if not self.allowed(): return
                if parsed.path == '/api/annotations':
                    self.json_response(notes.list()); return
                elif parsed.path == '/api/search':
                    query = parse_qs(parsed.query).get('q',[''])[0].strip()
                    if not query or len(query)>1000:
                        self.send_error(400,'Query must be 1–1000 characters'); return
                    with lock:
                        if model is None: model=embedder(args.cache.resolve())
                        result = search(output/'subject-index.sqlite',args.cache,query,10000,model)
                    data = json.dumps(result).encode(); content_type = 'application/json'
                elif parsed.path in ('/','/explorer.html'):
                    data = (output/'explorer.html').read_bytes(); content_type = 'text/html; charset=utf-8'
                else:
                    self.send_error(404); return
                self.send_response(200); self.send_header('Content-Type',content_type)
                self.send_header('Content-Length',str(len(data)))
                self.send_header('X-Content-Type-Options','nosniff'); self.end_headers(); self.wfile.write(data)
            except Exception as error:
                self.send_error(500,str(error))
    print(f'Explorer: http://127.0.0.1:{args.port} (Ctrl+C to stop)',flush=True)
    ThreadingHTTPServer(('127.0.0.1',args.port),Handler).serve_forever()

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command',required=True)
    build_parser = commands.add_parser('build')
    build_parser.add_argument('--corpus',type=Path,default=HERE)
    build_parser.add_argument('--catalog',type=Path,default=HERE/'catalog.json')
    build_parser.add_argument('--vocabulary',type=Path,default=HERE/'chemical-lexicon.json')
    build_parser.add_argument('--labels',type=Path,default=HERE/'document-labels.json')
    build_parser.add_argument('--context',type=int,default=3)
    build_parser.add_argument('--title',default='Neurological chemicals')
    build_parser.add_argument('--embedding-workers',type=int,default=1,
                              help='Local embedding processes, each using two CPU threads')
    build_parser.add_argument('--embedding-delay',type=float,default=0,
                              help='Seconds between contexts; a positive delay forces one worker')
    for name in ('serve','search','render'):
        sub = commands.add_parser(name)
        if name=='serve': sub.add_argument('--port',type=int,default=8765)
        elif name=='search': sub.add_argument('query'); sub.add_argument('--limit',type=int,default=20)
    for sub in commands.choices.values():
        sub.add_argument('--output',type=Path,default=HERE)
        sub.add_argument('--cache',type=Path,default=HERE/'.models')
    args = parser.parse_args()
    if args.command=='build':
        if args.context<0: parser.error('--context cannot be negative')
        if args.embedding_workers<1: parser.error('--embedding-workers must be positive')
        if not 0 <= args.embedding_delay < float('inf'): parser.error('--embedding-delay must be finite and nonnegative')
        build(args)
    elif args.command=='serve': serve(args)
    elif args.command=='render':
        target=args.output/'explorer.html'
        existing=target.read_text(encoding='utf-8')
        match=re.search(r'<script id="index-data" type="application/json">(.*?)</script>',existing,re.S)
        if not match: raise ValueError('Existing explorer has no embedded index; run build first')
        json.loads(match.group(1))
        rendered=explorer_html(match.group(1))
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',dir=args.output,delete=False,suffix='.html') as stream:
            stream.write(rendered); temporary=stream.name
        os.replace(temporary,target)
        print(f'Rendered: {target}')
    else: print(json.dumps(search(args.output/'subject-index.sqlite',args.cache,args.query,args.limit),indent=2))

if __name__=='__main__':
    main()
