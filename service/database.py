"""SQLite locally; PostgreSQL in deployment. Never silently fall back in production."""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import atexit
import base64
import tempfile

class Connection:
    def __init__(self,raw,postgres):self.raw=raw;self.postgres=postgres
    def execute(self,sql,args=()):
        if self.postgres:
            if sql in ('PRAGMA journal_mode=WAL','BEGIN IMMEDIATE'):return self.raw.execute('SELECT 1')
            sql=sql.replace('?', '%s')
        return self.raw.execute(sql,args)

class Database:
    def __init__(self,path):
        self.path=Path(path)
        self.url=os.environ.get('DATABASE_URL','')
        self.postgres=bool(self.url or os.environ.get('PG_HOST'))
        self.ca_file=None
        if os.environ.get('PG_CA_CERT'):
            cert=os.environ['PG_CA_CERT']
            if '-----BEGIN CERTIFICATE-----' not in cert:cert=base64.b64decode(cert,validate=True).decode()
            with tempfile.NamedTemporaryFile(mode='w',suffix='.pem',delete=False) as file:
                file.write(cert);self.ca_file=file.name
            atexit.register(lambda:Path(self.ca_file).unlink(missing_ok=True))
        if os.environ.get('TEMPEST_REQUIRE_POSTGRES')=='true' and not self.postgres:
            raise ValueError('PostgreSQL configuration is required; refusing SQLite fallback')
        if not self.postgres:self.path.parent.mkdir(parents=True,exist_ok=True)

    @contextmanager
    def connect(self):
        if self.postgres:
            import psycopg
            kwargs={'connect_timeout':10}
            if not self.url:
                kwargs.update(host=os.environ['PG_HOST'],port=os.environ.get('PG_PORT','5432'),
                              user=os.environ['PG_USER'],password=os.environ['PG_PASSWORD'],dbname=os.environ['PG_DATABASE'])
            if os.environ.get('PG_SSLMODE'):kwargs['sslmode']=os.environ['PG_SSLMODE']
            if os.environ.get('PG_SSLROOTCERT'):kwargs['sslrootcert']=os.environ['PG_SSLROOTCERT']
            if self.ca_file:kwargs.update(sslrootcert=self.ca_file,sslmode='verify-full')
            with psycopg.connect(self.url,**kwargs) as raw:
                raw.execute('CREATE SCHEMA IF NOT EXISTS tempest')
                raw.execute('SET LOCAL search_path TO tempest')
                yield Connection(raw,True)
        else:
            raw=sqlite3.connect(self.path,timeout=120)
            try:
                with raw:yield Connection(raw,False)
            finally:raw.close()
