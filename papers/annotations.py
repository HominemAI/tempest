"""Persistent reader notes, kept separate from the rebuildable research index."""
import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


class AnnotationStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / 'annotations.sqlite'
        with sqlite3.connect(self.path) as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS annotations (id TEXT PRIMARY KEY, document_id TEXT NOT NULL, source_sha256 TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)')
            db.execute('CREATE INDEX IF NOT EXISTS annotations_document ON annotations(document_id,source_sha256)')

    def list(self):
        with sqlite3.connect(self.path) as db:
            return [json.loads(row[0]) for row in db.execute('SELECT payload FROM annotations ORDER BY created_at,id')]

    def save(self, note):
        if not isinstance(note, dict) or not re.fullmatch(r'[\w-]{1,128}', str(note.get('id',''))):
            raise ValueError('Invalid annotation ID')
        for key, maximum in [('document_id',128),('source_sha256',64),('markdown_path',2000),('text',100000),('comment',100000)]:
            if not isinstance(note.get(key),str) or len(note[key])>maximum:
                raise ValueError(f'Invalid {key}')
        with sqlite3.connect(self.directory/'subject-index.sqlite') as db:
            doc=db.execute('SELECT path,sha256,text FROM documents WHERE id=?',(note['document_id'],)).fetchone()
        if not doc or doc[:2]!=(note['markdown_path'],note['source_sha256']):
            raise ValueError('Document missing or changed; annotation source does not match')
        encoded=doc[2].encode('utf-16-le')
        segments=note.get('segments')
        if not isinstance(segments,list) or not 1<=len(segments)<=1000:
            raise ValueError('Expected 1–1000 source segments')
        for part in segments:
            for key in ['passage_start_line','rendered_start','rendered_end','source_start_offset','source_end_offset']:
                if type(part.get(key)) is not int or part[key]<0:
                    raise ValueError(f'Invalid segment {key}')
            start,end=part['source_start_offset'],part['source_end_offset']
            if not 0<=start<end<=len(encoded)//2 or part['rendered_start']>=part['rendered_end']:
                raise ValueError('Invalid segment range')
            try:
                actual=encoded[start*2:end*2].decode('utf-16-le')
                for key,offset in [('source_start',start),('source_end',end)]:
                    prefix=encoded[:offset*2].decode('utf-16-le')
                    expected=dict(line=prefix.count('\n')+1,column=len(prefix.rsplit('\n',1)[-1].encode('utf-16-le'))//2)
                    if part.get(key)!=expected: raise ValueError('Source line/column mismatch')
            except UnicodeDecodeError as error:
                raise ValueError('Source boundary splits a Unicode character') from error
            if actual!=part.get('source_text'):
                raise ValueError('Source text does not match its stored range')
        now=datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.path,timeout=10) as db:
            previous=db.execute('SELECT document_id,created_at FROM annotations WHERE id=?',(note['id'],)).fetchone()
            if previous and previous[0]!=note['document_id']:
                raise ValueError('Annotation ID already belongs to another document')
            record={key:note[key] for key in ['id','document_id','source_sha256','markdown_path','text','comment','segments']}
            record.update(created_at=previous[1] if previous else now,updated_at=now,coordinate_format='1-based lines, 0-based UTF-16 columns, exclusive end')
            db.execute('INSERT INTO annotations VALUES (?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,source_sha256=excluded.source_sha256,updated_at=excluded.updated_at',
                       (record['id'],record['document_id'],record['source_sha256'],json.dumps(record,ensure_ascii=False),record['created_at'],now))
        return record
