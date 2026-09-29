import concurrent.futures
from pathlib import Path
import tempfile
import time
import unittest
import uuid
from unittest.mock import patch

from access import Access, AccessError, Conflict, Forbidden, QuotaExceeded, RateLimited, Unauthorized, require_scope
from database import Database


class AccessTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.schema = 'tempest_test_access_' + uuid.uuid4().hex
        self.database = Database(Path(self.tmp.name) / 'access.sqlite', schema=self.schema)
        self.access = Access(self.database, 'bootstrap-secret-only-for-setup')
        self.account = self.access.create_account('First account', 'integration-one')['id']

    def tearDown(self):
        if self.database.postgres:
            with self.database.connect() as db:
                db.execute('DROP SCHEMA ' + self.schema + ' CASCADE')
        self.tmp.cleanup()

    def admin(self):
        return self.access.setup('operator', 'very-strong-password', 'bootstrap-secret-only-for-setup')

    def test_setup_login_and_credential_storage(self):
        self.assertEqual(self.access.status(), {'setup_required': True})
        with self.assertRaises(Unauthorized):
            self.access.setup('operator', 'very-strong-password', 'wrong', 'source')
        created = self.admin()
        self.assertEqual(self.access.status(), {'setup_required': False})
        with self.assertRaises(Conflict):
            self.admin()
        session = self.access.authenticate_session(created['session_token'])
        self.assertEqual(session['username'], 'operator')
        self.access.verify_csrf(session, created['csrf'])
        with self.assertRaises(Forbidden):
            self.access.verify_csrf(session, 'wrong')
        login = Access(self.database, 'changed-bootstrap').login('operator', 'very-strong-password', 'source')
        self.assertNotEqual(login['session_token'], created['session_token'])
        with self.database.connect() as db:
            password_hash = db.execute('SELECT password_hash FROM access_admin').fetchone()[0]
            session_hashes = [row[0] for row in db.execute('SELECT token_hash FROM access_sessions')]
        self.assertTrue(password_hash.startswith('pbkdf2_sha256$600000$'))
        self.assertNotIn('very-strong-password', password_hash)
        self.assertNotIn(created['session_token'], session_hashes)
        self.access.logout(created['session_token'])
        with self.assertRaises(Unauthorized):
            self.access.authenticate_session(created['session_token'])
        self.assertEqual(self.access.authenticate_session(login['session_token'])['kind'], 'admin')

    def test_sessions_api_keys_and_bootstrap_are_separate(self):
        session = self.admin()
        admin_key = self.access.issue_key('Provisioning', kind='admin')
        self.assertEqual(self.access.authenticate_key(admin_key['token'])['kind'], 'admin')
        for credential in (admin_key['token'], 'bootstrap-secret-only-for-setup'):
            with self.assertRaises(Unauthorized):
                self.access.authenticate_session(credential)
        for credential in (session['session_token'], 'bootstrap-secret-only-for-setup'):
            with self.assertRaises(Unauthorized):
                self.access.authenticate_key(credential)
        with self.assertRaises(Unauthorized):
            self.access.login('customer', admin_key['token'])

    def test_setup_concurrent_singleton(self):
        def setup(index):
            try:
                return self.access.setup('admin-' + str(index), 'very-strong-password', 'bootstrap-secret-only-for-setup')
            except Conflict:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(setup, range(4)))
        self.assertEqual(sum(result is not None for result in results), 1)
        with self.database.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM access_admin').fetchone()[0], 1)

    def test_login_and_setup_throttle_survive_restart(self):
        for _ in range(5):
            with self.assertRaises(Unauthorized):
                self.access.setup('operator', 'very-strong-password', 'wrong', 'source')
        restarted = Access(self.database, 'bootstrap-secret-only-for-setup')
        with self.assertRaises(RateLimited):
            restarted.setup('operator', 'very-strong-password', 'bootstrap-secret-only-for-setup', 'source')
        self.admin()
        for index in range(5):
            with self.assertRaises(Unauthorized):
                restarted.login('operator', 'wrong', 'source-' + str(index))
        with self.assertRaises(RateLimited):
            Access(self.database, '').login('operator', 'very-strong-password', 'new-source')
        with patch('access.time.time', return_value=time.time() + 901):
            self.assertIn('session_token', restarted.login('operator', 'very-strong-password', 'source'))

    def test_key_scopes_binding_expiry_and_revocation(self):
        key = self.access.issue_key('Inference', account_id=self.account, user_id='user-123')
        principal = self.access.authenticate_key(key['token'])
        self.assertEqual(principal['account_id'], self.account)
        self.assertEqual(principal['user_id'], 'user-123')
        self.assertEqual(principal['scopes'], ['text'])
        require_scope(principal, 'text')
        with self.assertRaises(Forbidden):
            require_scope(principal, 'create_description')
        with self.database.connect() as db:
            stored = db.execute('SELECT token_hash FROM access_keys WHERE id=?', (key['id'],)).fetchone()[0]
        self.assertNotEqual(stored, key['token'])
        self.assertEqual(len(stored), 64)
        self.assertNotIn('token', self.access.list_keys(self.account)[0])
        self.assertNotIn('token_hash', self.access.list_keys(self.account)[0])
        revoked = self.access.revoke_key(key['id'])
        self.assertIsNotNone(revoked['revoked_at'])
        with self.assertRaises(Unauthorized):
            self.access.authenticate_key(key['token'])
        expires = int(time.time()) + 60
        expiring = self.access.issue_key('Temporary', account_id=self.account, expires_at=expires)
        with patch('access.time.time', return_value=expires):
            with self.assertRaises(Unauthorized):
                self.access.authenticate_key(expiring['token'])

    def test_accounts_and_users_are_isolated(self):
        other = self.access.create_account('Second account')['id']
        with self.assertRaises(Conflict):
            self.access.create_account('Duplicate', 'integration-one')
        for account in (self.account, other):
            self.access.issue_key('Bound', account_id=account, user_id='same-external-user')
        self.access.update_limits(self.account, 'same-external-user', {'text': 1})
        self.assertEqual(self.access.get_limits(other, 'same-external-user')['text'], 1000)
        self.access.reserve(self.account, 'same-external-user', 'text', 'one', 'first')
        self.assertEqual(self.access.usage(other, 'same-external-user')['used']['text'], 0)
        with self.assertRaises(QuotaExceeded):
            self.access.reserve(self.account, 'same-external-user', 'text', 'two', 'second')
        with self.assertRaises(Forbidden):
            self.access.reserve(self.account, 'forged-header-user', 'text', 'one', 'first')
        with self.assertRaises(KeyError):
            self.access.issue_key('Unknown account', account_id='unknown')

    def test_limits_inherit_unlimited_disabled_and_validation(self):
        self.access.update_limits(limits={'text': 2, 'create_description': 0})
        self.assertEqual(self.access.get_limits(self.account), {'text': 2, 'create_description': 0})
        self.access.update_limits(self.account, limits={'text': -1})
        self.access.update_limits(self.account, 'bounded', {'text': 1})
        self.access.reserve(self.account, 'bounded', 'text', 'first', 'a')
        with self.assertRaises(QuotaExceeded):
            self.access.reserve(self.account, 'bounded', 'text', 'second', 'b')
        self.access.update_limits(self.account, 'bounded', {'text': None})
        self.assertEqual(self.access.get_limits(self.account, 'bounded')['text'], -1)
        description = self.access.describe_limits(self.account, 'bounded')
        self.assertEqual(description['overrides']['text'], None)
        self.assertEqual(description['inherited']['text'], -1)
        self.assertEqual(description['limits']['text'], -1)
        self.access.reserve(self.account, 'bounded', 'text', 'second', 'b')
        with self.assertRaises(QuotaExceeded):
            self.access.reserve(self.account, None, 'create_description', 'third', 'c')
        for limits in ({'text': True}, {'text': -2}, {'text': 1.5}, {'unknown': 1}, {'text': 2**63}):
            with self.assertRaises(AccessError):
                self.access.update_limits(self.account, limits=limits)
        self.access.update_limits(self.account, limits={'text': None})
        self.assertEqual(self.access.get_limits(self.account)['text'], 2)

    def test_session_expiry_and_password_validation(self):
        for password in ('short', 'x' * 1025, None):
            with self.assertRaises(AccessError):
                self.access.setup('operator', password, 'bootstrap-secret-only-for-setup')
        session = self.admin()
        with patch('access.time.time', return_value=session['expires_at']):
            with self.assertRaises(Unauthorized):
                self.access.authenticate_session(session['session_token'])
        with self.assertRaises(Unauthorized):
            self.access.login('wrong-operator', 'very-strong-password')
        with self.assertRaises(AccessError):
            self.access.issue_key('Admin bound', kind='admin', account_id=self.account)
        for scopes in ([], ['admin'], ['text', 'text'], 'text'):
            with self.assertRaises(AccessError):
                self.access.issue_key('Invalid scopes', account_id=self.account, scopes=scopes)

    def test_atomic_quota_under_concurrent_instances(self):
        self.access.update_limits(self.account, limits={'text': 7})
        second = Access(self.database, '')
        def reserve(index):
            try:
                return (self.access if index % 2 else second).reserve(self.account, None, 'text', str(index), str(index))
            except QuotaExceeded:
                return None
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(reserve, range(25)))
        self.assertEqual(sum(result is not None for result in results), 7)
        self.assertEqual(self.access.usage(self.account)['used']['text'], 7)

    def test_idempotency_release_and_completion(self):
        args = (self.account, None, 'text', '/route/request-one', 'fingerprint')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.access.reserve(*args), range(12)))
        self.assertEqual(sum(not result['replayed'] for result in results), 1)
        self.assertEqual(self.access.usage(self.account)['used']['text'], 1)
        with self.assertRaises(Conflict):
            self.access.reserve(*args[:-1], 'different')
        for result in results:
            released = self.access.release(*args, attempt_id=result['attempt_id'])
        self.assertEqual(released['status'], 'released')
        self.access.release(*args)
        self.assertEqual(self.access.usage(self.account)['used']['text'], 0)
        self.assertFalse(self.access.reserve(*args)['replayed'])
        self.assertEqual(self.access.complete(*args)['status'], 'completed')
        self.assertEqual(self.access.release(*args)['status'], 'completed')
        self.assertTrue(self.access.reserve(*args)['replayed'])
        self.assertEqual(self.access.usage(self.account)['used']['text'], 1)

    def test_concurrent_failed_attempt_cannot_refund_success(self):
        args = (self.account, None, 'text', 'concurrent-success', 'same')
        first = self.access.reserve(*args)
        second = Access(self.database, '').reserve(*args)
        self.assertNotEqual(first['attempt_id'], second['attempt_id'])
        self.assertEqual(self.access.release(*args, attempt_id=first['attempt_id'])['status'], 'reserved')
        self.assertEqual(self.access.usage(self.account)['used']['text'], 1)
        self.assertEqual(self.access.complete(*args, attempt_id=second['attempt_id'])['status'], 'completed')
        self.access.release(*args, attempt_id=first['attempt_id'])
        self.access.release(*args, attempt_id=second['attempt_id'])
        self.assertEqual(self.access.usage(self.account)['used']['text'], 1)
        self.assertIsNone(self.access.reserve(*args)['attempt_id'])

    def test_only_final_failed_attempt_refunds_and_claims_are_isolated(self):
        args = (self.account, None, 'text', 'all-fail', 'same')
        first = self.access.reserve(*args)
        second = self.access.reserve(*args)
        with self.assertRaises(Conflict):
            self.access.release(*args)
        with self.assertRaises(Forbidden):
            self.access.release(*args, attempt_id='forged')
        self.access.release(*args, attempt_id=first['attempt_id'])
        self.access.release(*args, attempt_id=first['attempt_id'])
        self.assertEqual(self.access.usage(self.account)['used']['text'], 1)
        self.assertEqual(self.access.release(*args, attempt_id=second['attempt_id'])['status'], 'released')
        self.assertEqual(self.access.usage(self.account)['used']['text'], 0)
        retry = self.access.reserve(*args)
        self.access.release(*args, attempt_id=second['attempt_id'])
        self.assertEqual(self.access.usage(self.account)['used']['text'], 1)
        other = self.access.reserve(self.account, None, 'text', 'other', 'different')
        with self.assertRaises(Forbidden):
            self.access.complete(*args, attempt_id=other['attempt_id'])
        self.access.complete(*args, attempt_id=retry['attempt_id'])

    def test_utc_daily_counters_and_cross_day_idempotency(self):
        args = (self.account, None, 'text', 'one', 'same')
        self.access.update_limits(self.account, limits={'text': 1})
        with patch.object(Access, '_day', return_value='2026-09-28'):
            self.access.reserve(*args)
            self.access.complete(*args)
        with patch.object(Access, '_day', return_value='2026-09-29'):
            old = self.access.reserve(*args)
            self.assertEqual(old['day'], '2026-09-28')
            self.assertEqual(self.access.usage(self.account)['used']['text'], 0)
            self.access.reserve(self.account, None, 'text', 'two', 'another')
            self.assertEqual(self.access.usage(self.account)['used']['text'], 1)
        self.assertEqual(self.access.usage(self.account, day='2026-09-28')['used']['text'], 1)
        with self.assertRaises(AccessError):
            self.access.usage(self.account, day='2026-9-28')


if __name__ == '__main__':
    unittest.main()
