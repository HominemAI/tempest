"""Administrator browser sessions, scoped API accounts, and atomic daily quotas.

No password or raw API/session credential is stored. All mutations serialize on
one database lock row (BEGIN IMMEDIATE locally), including first-admin setup.
Quota reservations count admitted work; complete on success, release on failure.
An interrupted process conservatively retains its reservation.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import hmac
import json
import secrets
import time
import uuid

KINDS = ('text', 'create_description')
DEFAULT_LIMITS = {'text': 1000, 'create_description': 10}
SESSION_SECONDS = 12 * 60 * 60
FAILURE_WINDOW = 15 * 60
MAX_FAILURES = 5
PASSWORD_ROUNDS = 600_000


class AccessError(ValueError):
    status = 400
    code = 'invalid_request'


class Unauthorized(AccessError):
    status = 401
    code = 'unauthorized'


class Forbidden(AccessError):
    status = 403
    code = 'forbidden'


class Conflict(AccessError):
    status = 409
    code = 'conflict'


class RateLimited(AccessError):
    status = 429
    code = 'rate_limited'


class QuotaExceeded(RateLimited):
    code = 'quota_exceeded'


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _string(value, label, maximum=200):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise AccessError(f'{label} must be a nonempty string of at most {maximum} characters')
    return value


def _password(value):
    if not isinstance(value, str) or not 12 <= len(value) <= 1024:
        raise AccessError('Password must be 12–1024 characters')


def _password_hash(password):
    salt = secrets.token_bytes(16)
    value = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, PASSWORD_ROUNDS)
    return f'pbkdf2_sha256${PASSWORD_ROUNDS}${salt.hex()}${value.hex()}'


def _check_password(password, encoded):
    try:
        scheme, rounds, salt, expected = encoded.split('$')
        if scheme != 'pbkdf2_sha256' or not isinstance(password, str) or len(password) > 1024:
            return False
        value = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(value.hex(), expected)
    except (ValueError, TypeError):
        return False


def require_scope(principal, scope):
    if scope not in KINDS:
        raise AccessError('Unknown scope')
    if not principal or scope not in principal.get('scopes', []):
        raise Forbidden(f'Token requires {scope} scope')


class Access:
    def __init__(self, database, bootstrap_token):
        self.database = database
        self.bootstrap_token = bootstrap_token if isinstance(bootstrap_token, str) else ''
        with self.database.connect() as db:
            if not self.database.postgres:
                db.execute('BEGIN IMMEDIATE')
            statements = (
                'CREATE TABLE IF NOT EXISTS access_mutex(id INTEGER PRIMARY KEY)',
                'CREATE TABLE IF NOT EXISTS access_admin(id INTEGER PRIMARY KEY, username TEXT NOT NULL, password_hash TEXT NOT NULL, created_at BIGINT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS access_sessions(id TEXT PRIMARY KEY, token_hash TEXT NOT NULL UNIQUE, csrf TEXT NOT NULL, expires_at BIGINT NOT NULL, created_at BIGINT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS access_throttle(id TEXT PRIMARY KEY, failures INTEGER NOT NULL, started_at BIGINT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS api_accounts(id TEXT PRIMARY KEY, name TEXT NOT NULL, external_id TEXT UNIQUE, created_at BIGINT NOT NULL)',
                'CREATE TABLE IF NOT EXISTS api_users(account_id TEXT NOT NULL, user_id TEXT NOT NULL, created_at BIGINT NOT NULL, PRIMARY KEY(account_id,user_id))',
                'CREATE TABLE IF NOT EXISTS access_keys(id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL, account_id TEXT, user_id TEXT, scopes TEXT NOT NULL, token_hash TEXT NOT NULL UNIQUE, created_at BIGINT NOT NULL, expires_at BIGINT, revoked_at BIGINT)',
                'CREATE TABLE IF NOT EXISTS access_limits(owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, text_limit BIGINT, creation_limit BIGINT, PRIMARY KEY(owner_type,owner_id))',
                'CREATE TABLE IF NOT EXISTS access_usage(owner_type TEXT NOT NULL, owner_id TEXT NOT NULL, day TEXT NOT NULL, kind TEXT NOT NULL, used BIGINT NOT NULL, PRIMARY KEY(owner_type,owner_id,day,kind))',
                'CREATE TABLE IF NOT EXISTS access_operations(account_id TEXT NOT NULL, user_id TEXT NOT NULL, kind TEXT NOT NULL, operation_id TEXT NOT NULL, fingerprint TEXT NOT NULL, day TEXT NOT NULL, status TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(account_id,user_id,kind,operation_id))',
                'CREATE TABLE IF NOT EXISTS access_attempts(id TEXT PRIMARY KEY, account_id TEXT NOT NULL, user_id TEXT NOT NULL, kind TEXT NOT NULL, operation_id TEXT NOT NULL, status TEXT NOT NULL)',
            )
            for sql in statements:
                db.execute(sql)
            db.execute('INSERT INTO access_mutex(id) VALUES(1) ON CONFLICT(id) DO NOTHING')

    @contextmanager
    def _transaction(self):
        with self.database.connect() as db:
            if self.database.postgres:
                db.execute('SELECT id FROM access_mutex WHERE id=1 FOR UPDATE').fetchone()
            else:
                db.execute('BEGIN IMMEDIATE')
            yield db

    def status(self):
        with self.database.connect() as db:
            return {'setup_required': db.execute('SELECT id FROM access_admin WHERE id=1').fetchone() is None}

    def _throttle(self, db, identifier, now):
        row = db.execute('SELECT failures,started_at FROM access_throttle WHERE id=?', (identifier,)).fetchone()
        if row and now - row[1] < FAILURE_WINDOW and row[0] >= MAX_FAILURES:
            raise RateLimited('Too many attempts. Try again in 15 minutes.')

    def _failure(self, db, identifier, now):
        row = db.execute('SELECT failures,started_at FROM access_throttle WHERE id=?', (identifier,)).fetchone()
        count, started = (row[0] + 1, row[1]) if row and now - row[1] < FAILURE_WINDOW else (1, now)
        db.execute('INSERT INTO access_throttle(id,failures,started_at) VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET failures=excluded.failures,started_at=excluded.started_at', (identifier, count, started))

    def _session(self, db, username, now):
        raw, csrf, identifier = 'tps_' + secrets.token_urlsafe(32), secrets.token_urlsafe(32), str(uuid.uuid4())
        expiry = now + SESSION_SECONDS
        db.execute('INSERT INTO access_sessions(id,token_hash,csrf,expires_at,created_at) VALUES(?,?,?,?,?)', (identifier, _digest(raw), csrf, expiry, now))
        return {'session_token': raw, 'csrf': csrf, 'expires_at': expiry,
                'principal': {'kind': 'admin', 'username': username, 'csrf': csrf, 'session_id': identifier}}

    def setup(self, username, password, bootstrap, peer=''):
        username = _string(username, 'Username', 128).strip()
        _password(password)
        now = int(time.time())
        bucket = 'setup:' + _digest(str(peer))
        failure = None
        with self._transaction() as db:
            self._throttle(db, bucket, now)
            if db.execute('SELECT id FROM access_admin WHERE id=1').fetchone():
                raise Conflict('Administrator setup is already complete')
            if not self.bootstrap_token or not isinstance(bootstrap, str) or not hmac.compare_digest(_digest(bootstrap), _digest(self.bootstrap_token)):
                self._failure(db, bucket, now)
                failure = Unauthorized('Invalid setup credential')
            else:
                db.execute('INSERT INTO access_admin(id,username,password_hash,created_at) VALUES(1,?,?,?)', (username, _password_hash(password), now))
                db.execute('DELETE FROM access_throttle WHERE id=?', (bucket,))
                result = self._session(db, username, now)
        if failure:
            raise failure
        return result

    def login(self, username, password, peer=''):
        now = int(time.time())
        username = username if isinstance(username, str) and len(username) <= 128 else ''
        # Both an IP bucket and an administrator bucket prevent source rotation.
        buckets = ('login:peer:' + _digest(str(peer)), 'login:admin')
        failure = None
        with self._transaction() as db:
            for bucket in buckets:
                self._throttle(db, bucket, now)
            row = db.execute('SELECT username,password_hash FROM access_admin WHERE id=1').fetchone()
            valid = row and _check_password(password, row[1]) and hmac.compare_digest(username.encode(), row[0].encode())
            if not valid:
                for bucket in buckets:
                    self._failure(db, bucket, now)
                failure = Unauthorized('Invalid username or password')
            else:
                for bucket in buckets:
                    db.execute('DELETE FROM access_throttle WHERE id=?', (bucket,))
                result = self._session(db, row[0], now)
        if failure:
            raise failure
        return result

    def authenticate_session(self, raw):
        if not isinstance(raw, str) or len(raw) > 512:
            raise Unauthorized('Administrator login required')
        with self.database.connect() as db:
            row = db.execute('SELECT s.id,s.csrf,s.expires_at,a.username FROM access_sessions s CROSS JOIN access_admin a WHERE s.token_hash=? AND a.id=1', (_digest(raw),)).fetchone()
        if not row or row[2] <= int(time.time()):
            raise Unauthorized('Administrator login required')
        return {'kind': 'admin', 'username': row[3], 'csrf': row[1], 'session_id': row[0]}

    def logout(self, raw):
        if isinstance(raw, str):
            with self._transaction() as db:
                db.execute('DELETE FROM access_sessions WHERE token_hash=?', (_digest(raw),))

    @staticmethod
    def verify_csrf(principal, csrf):
        if not isinstance(csrf, str) or not hmac.compare_digest(principal.get('csrf', '').encode(), csrf.encode()):
            raise Forbidden('Valid CSRF token required')

    def _account(self, db, account_id):
        row = db.execute('SELECT id,name,external_id,created_at FROM api_accounts WHERE id=?', (account_id,)).fetchone()
        if not row:
            raise KeyError('API account not found')
        return dict(zip(('id', 'name', 'external_id', 'created_at'), row))

    def account_exists(self, account_id):
        with self.database.connect() as db:
            return bool(db.execute('SELECT id FROM api_accounts WHERE id=?', (account_id,)).fetchone())

    def get_account(self, account_id):
        with self.database.connect() as db:
            return self._account(db, account_id)

    def create_account(self, name, external_id=None, limits=None):
        name = _string(name, 'Account name', 180)
        if external_id is not None:
            external_id = _string(external_id, 'External ID')
        if limits is not None:
            self._validate_limits(limits)
        identifier, now = str(uuid.uuid4()), int(time.time())
        with self._transaction() as db:
            if external_id is not None and db.execute('SELECT id FROM api_accounts WHERE external_id=?', (external_id,)).fetchone():
                raise Conflict('External account ID already exists')
            db.execute('INSERT INTO api_accounts(id,name,external_id,created_at) VALUES(?,?,?,?)', (identifier, name, external_id, now))
            if limits is not None:
                self._update_limits(db, 'account', identifier, limits)
            return self._account(db, identifier)

    def list_accounts(self):
        with self.database.connect() as db:
            rows = db.execute('SELECT id,name,external_id,created_at FROM api_accounts ORDER BY created_at,id').fetchall()
            return [dict(zip(('id', 'name', 'external_id', 'created_at'), row)) for row in rows]

    @staticmethod
    def _user_owner(account_id, user_id):
        return json.dumps([account_id, user_id], separators=(',', ':'))

    def _provision_user(self, db, account_id, user_id):
        self._account(db, account_id)
        _string(user_id, 'User ID')
        db.execute('INSERT INTO api_users(account_id,user_id,created_at) VALUES(?,?,?) ON CONFLICT(account_id,user_id) DO NOTHING', (account_id, user_id, int(time.time())))
        return {'account_id': account_id, 'user_id': user_id}

    def provision_user(self, account_id, user_id):
        with self._transaction() as db:
            return self._provision_user(db, account_id, user_id)

    def _check_user(self, db, account_id, user_id):
        if user_id is not None:
            _string(user_id, 'User ID')
            if not db.execute('SELECT user_id FROM api_users WHERE account_id=? AND user_id=?', (account_id, user_id)).fetchone():
                raise Forbidden('User must be provisioned for this account')

    def issue_key(self, name, kind='account', account_id=None, user_id=None, scopes=None, expires_at=None):
        name = _string(name, 'Token name', 180)
        if kind not in ('admin', 'account'):
            raise AccessError('Token kind must be admin or account')
        if kind == 'admin' and (account_id is not None or user_id is not None):
            raise AccessError('Admin tokens cannot be bound to an account or user')
        if kind == 'account':
            _string(account_id, 'Account ID')
        scopes = list(KINDS) if scopes is None and kind == 'admin' else ['text'] if scopes is None else scopes
        if not isinstance(scopes, list) or not scopes or any(s not in KINDS for s in scopes) or len(set(scopes)) != len(scopes):
            raise AccessError('Scopes must be a nonempty list of text and/or create_description')
        now = int(time.time())
        if isinstance(expires_at, str):
            try:
                parsed = datetime.fromisoformat(expires_at.replace('Z', '+00:00'))
                if parsed.tzinfo is None:
                    raise ValueError('Timezone required')
                expires_at = int(parsed.timestamp())
            except ValueError as exc:
                raise AccessError('Expiry must be a UTC timestamp or an ISO date with timezone') from exc
        if expires_at is not None and (type(expires_at) is not int or expires_at <= now):
            raise AccessError('Expiry must be a future integer timestamp')
        identifier, raw = str(uuid.uuid4()), 'tpk_' + secrets.token_urlsafe(32)
        with self._transaction() as db:
            if kind == 'account':
                self._account(db, account_id)
                if user_id is not None:
                    self._provision_user(db, account_id, user_id)
            db.execute('INSERT INTO access_keys(id,name,kind,account_id,user_id,scopes,token_hash,created_at,expires_at,revoked_at) VALUES(?,?,?,?,?,?,?,?,?,NULL)', (identifier, name, kind, account_id, user_id, json.dumps(scopes), _digest(raw), now, expires_at))
            result = self._key_metadata(db, identifier)
            result['token'] = raw
            return result

    def _key_metadata(self, db, identifier):
        row = db.execute('SELECT id,name,kind,account_id,user_id,scopes,created_at,expires_at,revoked_at FROM access_keys WHERE id=?', (identifier,)).fetchone()
        if not row:
            raise KeyError('API token not found')
        result = dict(zip(('id', 'name', 'kind', 'account_id', 'user_id', 'scopes', 'created_at', 'expires_at', 'revoked_at'), row))
        result['scopes'] = json.loads(result['scopes'])
        return result

    def list_keys(self, account_id=None):
        with self.database.connect() as db:
            if account_id is not None:
                self._account(db, account_id)
                rows = db.execute('SELECT id FROM access_keys WHERE account_id=? ORDER BY created_at,id', (account_id,)).fetchall()
            else:
                rows = db.execute('SELECT id FROM access_keys ORDER BY created_at,id').fetchall()
            return [self._key_metadata(db, row[0]) for row in rows]

    def revoke_key(self, token_id):
        with self._transaction() as db:
            self._key_metadata(db, token_id)
            db.execute('UPDATE access_keys SET revoked_at=COALESCE(revoked_at,?) WHERE id=?', (int(time.time()), token_id))
            return self._key_metadata(db, token_id)

    def authenticate_key(self, raw):
        if not isinstance(raw, str) or len(raw) > 512:
            raise Unauthorized('Valid API token required')
        with self.database.connect() as db:
            row = db.execute('SELECT id,kind,account_id,user_id,scopes,expires_at,revoked_at FROM access_keys WHERE token_hash=?', (_digest(raw),)).fetchone()
        if not row or row[6] is not None or (row[5] is not None and row[5] <= int(time.time())):
            raise Unauthorized('Valid API token required')
        return {'token_id': row[0], 'kind': row[1], 'account_id': row[2], 'user_id': row[3], 'scopes': json.loads(row[4])}

    @staticmethod
    def _validate_limits(limits):
        if not isinstance(limits, dict) or set(limits) - set(KINDS):
            raise AccessError('Limits require text and/or create_description')
        for value in limits.values():
            if value is not None and (type(value) is not int or value < -1 or value > 2**63 - 1):
                raise AccessError('Limits must be null, -1, zero, or a positive integer')

    def _override(self, db, owner_type, owner_id):
        row = db.execute('SELECT text_limit,creation_limit FROM access_limits WHERE owner_type=? AND owner_id=?', (owner_type, owner_id)).fetchone()
        return dict(zip(KINDS, row or (None, None)))

    def _update_limits(self, db, owner_type, owner_id, limits):
        values = self._override(db, owner_type, owner_id)
        values.update(limits)
        db.execute('INSERT INTO access_limits(owner_type,owner_id,text_limit,creation_limit) VALUES(?,?,?,?) ON CONFLICT(owner_type,owner_id) DO UPDATE SET text_limit=excluded.text_limit,creation_limit=excluded.creation_limit', (owner_type, owner_id, values['text'], values['create_description']))
        return values

    def update_limits(self, account_id=None, user_id=None, limits=None):
        self._validate_limits(limits)
        with self._transaction() as db:
            if account_id is None:
                if user_id is not None:
                    raise AccessError('User limits require an account')
                owner_type, owner_id = 'default', ''
            else:
                self._account(db, account_id)
                if user_id is None:
                    owner_type, owner_id = 'account', account_id
                else:
                    self._provision_user(db, account_id, user_id)
                    owner_type, owner_id = 'user', self._user_owner(account_id, user_id)
            overrides = self._update_limits(db, owner_type, owner_id, limits)
            return {'account_id': account_id, 'user_id': user_id, 'overrides': overrides,
                    'limits': self._limits(db, account_id, user_id)}

    def _limits(self, db, account_id=None, user_id=None):
        effective = dict(DEFAULT_LIMITS)
        owners = [('default', '')]
        if account_id is not None:
            owners.append(('account', account_id))
            if user_id is not None:
                owners.append(('user', self._user_owner(account_id, user_id)))
        for owner_type, owner_id in owners:
            effective.update({key: value for key, value in self._override(db, owner_type, owner_id).items() if value is not None})
        return effective

    def get_limits(self, account_id=None, user_id=None):
        with self.database.connect() as db:
            if account_id is not None:
                self._account(db, account_id)
                self._check_user(db, account_id, user_id)
            elif user_id is not None:
                raise AccessError('User limits require an account')
            return self._limits(db, account_id, user_id)

    def describe_limits(self, account_id=None, user_id=None):
        """Return editable overrides separately from their inherited limits."""
        with self.database.connect() as db:
            if account_id is not None:
                self._account(db, account_id)
                self._check_user(db, account_id, user_id)
            elif user_id is not None:
                raise AccessError('User limits require an account')
            owner_type, owner_id = ('default', '') if account_id is None else ('account', account_id)
            inherited = dict(DEFAULT_LIMITS) if account_id is None else self._limits(db)
            if user_id is not None:
                owner_type, owner_id = 'user', self._user_owner(account_id, user_id)
                inherited = self._limits(db, account_id)
            return {'account_id': account_id, 'user_id': user_id,
                    'overrides': self._override(db, owner_type, owner_id),
                    'inherited': inherited, 'limits': self._limits(db, account_id, user_id)}

    @staticmethod
    def _day():
        return datetime.now(timezone.utc).date().isoformat()

    @staticmethod
    def _used(db, owner_type, owner_id, day, kind):
        row = db.execute('SELECT used FROM access_usage WHERE owner_type=? AND owner_id=? AND day=? AND kind=?', (owner_type, owner_id, day, kind)).fetchone()
        return row[0] if row else 0

    @staticmethod
    def _adjust(db, owner_type, owner_id, day, kind, amount):
        db.execute('INSERT INTO access_usage(owner_type,owner_id,day,kind,used) VALUES(?,?,?,?,?) ON CONFLICT(owner_type,owner_id,day,kind) DO UPDATE SET used=access_usage.used+excluded.used', (owner_type, owner_id, day, kind, amount))

    def _operation_args(self, account_id, user_id, kind, operation_id, fingerprint):
        _string(account_id, 'Account ID')
        if user_id is not None:
            _string(user_id, 'User ID')
        if kind not in KINDS:
            raise AccessError('Unknown quota kind')
        _string(operation_id, 'Operation ID', 512)
        _string(fingerprint, 'Fingerprint', 512)
        return account_id, user_id or '', kind, operation_id

    def reserve(self, account_id, user_id, kind, operation_id, fingerprint):
        args = self._operation_args(account_id, user_id, kind, operation_id, fingerprint)
        with self._transaction() as db:
            self._account(db, account_id)
            self._check_user(db, account_id, user_id)
            old = db.execute('SELECT fingerprint,status,result FROM access_operations WHERE account_id=? AND user_id=? AND kind=? AND operation_id=?', args).fetchone()
            if old and not hmac.compare_digest(old[0].encode(), fingerprint.encode()):
                raise Conflict('Idempotency key reused with different content')
            if old and old[1] != 'released':
                attempt_id = self._claim(db, args) if old[1] == 'reserved' else None
                return {**json.loads(old[2]), 'status': old[1], 'replayed': True, 'attempt_id': attempt_id}
            day = self._day()
            account_limit = self._limits(db, account_id)[kind]
            account_used = self._used(db, 'account', account_id, day, kind)
            owners = [('account', account_id)]
            limit, used = account_limit, account_used
            if user_id is not None:
                user_owner = self._user_owner(account_id, user_id)
                limit = self._limits(db, account_id, user_id)[kind]
                used = self._used(db, 'user', user_owner, day, kind)
                owners.append(('user', user_owner))
            if (account_limit != -1 and account_used >= account_limit) or (limit != -1 and used >= limit):
                raise QuotaExceeded(f'Daily {kind} quota exhausted')
            for owner_type, owner_id in owners:
                self._adjust(db, owner_type, owner_id, day, kind, 1)
            result = {'day': day, 'kind': kind, 'used': used + 1, 'limit': limit,
                      'account_used': account_used + 1, 'account_limit': account_limit,
                      'status': 'reserved', 'replayed': False}
            db.execute('INSERT INTO access_operations(account_id,user_id,kind,operation_id,fingerprint,day,status,result) VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(account_id,user_id,kind,operation_id) DO UPDATE SET day=excluded.day,status=excluded.status,result=excluded.result', (*args, fingerprint, day, 'reserved', json.dumps(result)))
            return {**result, 'attempt_id': self._claim(db, args)}

    @staticmethod
    def _claim(db, args):
        attempt_id = str(uuid.uuid4())
        db.execute('INSERT INTO access_attempts(id,account_id,user_id,kind,operation_id,status) VALUES(?,?,?,?,?,?)', (attempt_id, *args, 'active'))
        return attempt_id

    def _finish(self, account_id, user_id, kind, operation_id, fingerprint, status, attempt_id=None):
        args = self._operation_args(account_id, user_id, kind, operation_id, fingerprint)
        with self._transaction() as db:
            row = db.execute('SELECT fingerprint,day,status,result FROM access_operations WHERE account_id=? AND user_id=? AND kind=? AND operation_id=?', args).fetchone()
            if not row:
                raise KeyError('Quota reservation not found')
            if not hmac.compare_digest(row[0].encode(), fingerprint.encode()):
                raise Conflict('Idempotency key reused with different content')
            if attempt_id is None and row[2] == 'reserved':
                active = db.execute('SELECT id FROM access_attempts WHERE account_id=? AND user_id=? AND kind=? AND operation_id=? AND status=?', (*args, 'active')).fetchall()
                if len(active) > 1:
                    raise Conflict('Attempt ID required for a concurrent reservation')
                attempt_id = active[0][0] if active else None
            attempt_active = attempt_id is None
            if attempt_id is not None:
                attempt = db.execute('SELECT status FROM access_attempts WHERE id=? AND account_id=? AND user_id=? AND kind=? AND operation_id=?', (attempt_id, *args)).fetchone()
                if not attempt:
                    raise Forbidden('Invalid quota reservation attempt')
                attempt_active = attempt[0] == 'active'
                if attempt_active:
                    db.execute('UPDATE access_attempts SET status=? WHERE id=?', (status, attempt_id))
            if row[2] == 'reserved':
                active_count = db.execute('SELECT COUNT(*) FROM access_attempts WHERE account_id=? AND user_id=? AND kind=? AND operation_id=? AND status=?', (*args, 'active')).fetchone()[0]
                # Any successful attempt keeps the charge. A failure refunds only
                # after the final outstanding attempt has also failed.
                finish = attempt_active and (status == 'completed' or active_count == 0)
                if finish and status == 'released':
                    self._adjust(db, 'account', account_id, row[1], kind, -1)
                    if user_id is not None:
                        self._adjust(db, 'user', self._user_owner(account_id, user_id), row[1], kind, -1)
                if finish:
                    db.execute('UPDATE access_operations SET status=? WHERE account_id=? AND user_id=? AND kind=? AND operation_id=?', (status, *args))
                result_status = status if finish else 'reserved'
            else:
                result_status = row[2]
            return {**json.loads(row[3]), 'status': result_status, 'replayed': not attempt_active or row[2] != 'reserved', 'attempt_id': attempt_id}

    def complete(self, account_id, user_id, kind, operation_id, fingerprint, attempt_id=None):
        return self._finish(account_id, user_id, kind, operation_id, fingerprint, 'completed', attempt_id)

    def release(self, account_id, user_id, kind, operation_id, fingerprint, attempt_id=None):
        return self._finish(account_id, user_id, kind, operation_id, fingerprint, 'released', attempt_id)

    def usage(self, account_id=None, user_id=None, day=None):
        day = day or self._day()
        try:
            if datetime.strptime(day, '%Y-%m-%d').date().isoformat() != day:
                raise ValueError('Noncanonical day')
        except (TypeError, ValueError) as exc:
            raise AccessError('Day must be YYYY-MM-DD (UTC)') from exc
        with self.database.connect() as db:
            if account_id is None:
                if user_id is not None:
                    raise AccessError('User usage requires an account')
                rows = db.execute('SELECT kind,SUM(used) FROM access_usage WHERE owner_type=? AND day=? GROUP BY kind', ('account', day)).fetchall()
                used = {**dict.fromkeys(KINDS, 0), **dict(rows)}
            else:
                self._account(db, account_id)
                self._check_user(db, account_id, user_id)
                owner_type, owner_id = ('account', account_id) if user_id is None else ('user', self._user_owner(account_id, user_id))
                used = {kind: self._used(db, owner_type, owner_id, day, kind) for kind in KINDS}
            result = {'day': day, 'account_id': account_id, 'user_id': user_id,
                      'limits': self._limits(db, account_id, user_id), 'used': used}
            if user_id is not None:
                result.update(account_limits=self._limits(db, account_id), account_used={kind: self._used(db, 'account', account_id, day, kind) for kind in KINDS})
            return result
