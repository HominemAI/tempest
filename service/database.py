"""SQLite locally; PostgreSQL in deployment. Never silently fall back in production."""
from contextlib import contextmanager
import os
from pathlib import Path
import sqlite3
import atexit
import base64
import tempfile
import re
import time
import threading

class TimedCursor:
    def __init__(self, cursor, metrics):self.cursor=cursor;self.metrics=metrics
    def _fetch(self,method,*args):
        started=time.perf_counter()
        try:return getattr(self.cursor,method)(*args)
        finally:self.metrics['fetch_seconds']+=time.perf_counter()-started
    def fetchone(self):return self._fetch('fetchone')
    def fetchall(self):return self._fetch('fetchall')
    def fetchmany(self,size=None):return self._fetch('fetchmany') if size is None else self._fetch('fetchmany',size)
    def __iter__(self):return self
    def __next__(self):
        started=time.perf_counter()
        try:return next(self.cursor)
        finally:self.metrics['fetch_seconds']+=time.perf_counter()-started
    def __getattr__(self,name):return getattr(self.cursor,name)

class Connection:
    def __init__(self,raw,postgres):
        self.raw=raw;self.postgres=postgres
        self.metrics={'pool_wait_seconds':0.0,'setup_seconds':0.0,'execute_seconds':0.0,'fetch_seconds':0.0,'query_count':0}
    def execute(self,sql,args=()):
        started=time.perf_counter()
        if self.postgres:
            if sql in ('PRAGMA journal_mode=WAL','BEGIN IMMEDIATE'):sql='SELECT 1';args=()
            else:sql=sql.replace('?', '%s')
        try:return TimedCursor(self.raw.execute(sql,args),self.metrics)
        finally:
            self.metrics['execute_seconds']+=time.perf_counter()-started
            self.metrics['query_count']+=1
    def timing(self):
        return {key:round(value*1000,3) if key.endswith('_seconds') else value for key,value in self.metrics.items()}

class Database:
    def __init__(self,path,schema='tempest'):
        if not isinstance(schema,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,62}',schema):raise ValueError('Invalid database schema')
        self.schema=schema
        self.path=Path(path)
        self.url=os.environ.get('DATABASE_URL','')
        self.postgres=bool(self.url or os.environ.get('PG_HOST'))
        self.pool=None
        self._pool_lock=threading.Lock()
        self.pool_timeout=self._number('TEMPEST_DB_POOL_TIMEOUT_SECONDS',5,0.1,120)
        self.pool_min=self._integer('TEMPEST_DB_POOL_MIN',1,0,64)
        self.pool_max=self._integer('TEMPEST_DB_POOL_MAX',8,1,128)
        self.pool_max_waiting=self._integer('TEMPEST_DB_POOL_MAX_WAITING',64,1,4096)
        self.pool_max_lifetime=self._number('TEMPEST_DB_POOL_MAX_LIFETIME_SECONDS',1800,60,86400)
        self.pool_max_idle=self._number('TEMPEST_DB_POOL_MAX_IDLE_SECONDS',300,10,86400)
        self.statement_timeout_ms=self._integer('TEMPEST_DB_STATEMENT_TIMEOUT_MS',15000,100,300000)
        self.idle_transaction_timeout_ms=self._integer('TEMPEST_DB_IDLE_TRANSACTION_TIMEOUT_MS',60000,1000,3600000)
        if self.pool_min>self.pool_max:raise ValueError('TEMPEST_DB_POOL_MIN cannot exceed TEMPEST_DB_POOL_MAX')
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

    @staticmethod
    def _number(name,default,minimum,maximum):
        try:value=float(os.environ.get(name,default))
        except (TypeError,ValueError) as exc:raise ValueError(f'{name} must be numeric') from exc
        if not minimum<=value<=maximum:raise ValueError(f'{name} must be in [{minimum},{maximum}]')
        return value

    @staticmethod
    def _integer(name,default,minimum,maximum):
        try:value=int(os.environ.get(name,default))
        except (TypeError,ValueError) as exc:raise ValueError(f'{name} must be an integer') from exc
        if not minimum<=value<=maximum:raise ValueError(f'{name} must be in [{minimum},{maximum}]')
        return value

    def _postgres_kwargs(self):
        kwargs={'connect_timeout':5,
                'options':f'-c statement_timeout={self.statement_timeout_ms} -c idle_in_transaction_session_timeout={self.idle_transaction_timeout_ms}'}
        if not self.url:
            kwargs.update(host=os.environ['PG_HOST'],port=os.environ.get('PG_PORT','5432'),
                          user=os.environ['PG_USER'],password=os.environ['PG_PASSWORD'],dbname=os.environ['PG_DATABASE'])
        if os.environ.get('PG_SSLMODE'):kwargs['sslmode']=os.environ['PG_SSLMODE']
        if os.environ.get('PG_SSLROOTCERT'):kwargs['sslrootcert']=os.environ['PG_SSLROOTCERT']
        if self.ca_file:kwargs.update(sslrootcert=self.ca_file,sslmode='verify-full')
        return kwargs

    def open(self,wait=True):
        if not self.postgres:return
        with self._pool_lock:
            if self.pool is not None:return
            from psycopg_pool import ConnectionPool
            pool=ConnectionPool(
                conninfo=self.url,kwargs=self._postgres_kwargs(),min_size=self.pool_min,max_size=self.pool_max,
                max_waiting=self.pool_max_waiting,timeout=self.pool_timeout,max_lifetime=self.pool_max_lifetime,
                max_idle=self.pool_max_idle,reconnect_timeout=30,check=ConnectionPool.check_connection,
                name='tempest-postgres',open=False)
            try:
                pool.open(wait=wait,timeout=max(10,self.pool_timeout))
                self.pool=pool
            except BaseException:
                pool.close()
                raise

    def close(self):
        with self._pool_lock:
            if self.pool is not None:
                self.pool.close(timeout=5)
                self.pool=None

    def pool_stats(self):
        return self.pool.get_stats() if self.pool is not None else {}

    @contextmanager
    def connect(self):
        if self.postgres:
            try:
                if self.pool is None:self.open()
                started=time.perf_counter()
                with self.pool.connection(timeout=self.pool_timeout) as raw:
                    connection=Connection(raw,True)
                    connection.metrics['pool_wait_seconds']=time.perf_counter()-started
                    setup_started=time.perf_counter()
                    raw.execute('CREATE SCHEMA IF NOT EXISTS '+self.schema)
                    raw.execute('SET LOCAL search_path TO '+self.schema)
                    connection.metrics['setup_seconds']=time.perf_counter()-setup_started
                    yield connection
            except Exception as exc:
                from psycopg_pool import PoolTimeout,TooManyRequests,PoolClosed
                if isinstance(exc,(PoolTimeout,TooManyRequests,PoolClosed)):
                    raise DatabaseUnavailable('Database pool is busy or unavailable') from exc
                raise
        else:
            started=time.perf_counter()
            raw=sqlite3.connect(self.path,timeout=120)
            try:
                connection=Connection(raw,False)
                connection.metrics['setup_seconds']=time.perf_counter()-started
                yield connection
                raw.commit()
            except BaseException:
                raw.rollback()
                raise
            finally:raw.close()

class DatabaseUnavailable(RuntimeError):pass
