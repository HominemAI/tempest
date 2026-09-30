import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
import uuid
from unittest.mock import patch

from access import Access
from catalog import Catalog
from core import Conflict, Store
from database import Database


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.database = Database(Path(self.tmp.name) / 'catalog.sqlite', schema='tempest_test_catalog_' + uuid.uuid4().hex)
        self.access = Access(self.database, 'catalog-test-bootstrap')
        self.a = self.access.create_account('First account')['id']
        self.b = self.access.create_account('Second account')['id']
        self.catalog = Catalog(self.database)

    def tearDown(self):
        if self.database.postgres:
            with self.database.connect() as db:
                db.execute('DROP SCHEMA ' + self.database.schema + ' CASCADE')
        self.tmp.cleanup()

    def template(self, **extra):
        return self.catalog.save_template(dict(name='Calm', description='A calm baseline', baseline=[1] * 23, **extra), admin=True)

    @staticmethod
    def complete(context):
        return {'status': 'complete', 'configuration': {'name': 'Generated', 'baseline': [2] * 23, 'settings': {'pressureEnabled': True}}}

    def test_authorization_and_metadata_only(self):
        system = self.template()
        restricted = self.template(visibility='restricted', grants=[self.a])
        public = self.template(visibility='public')
        self.assertEqual(len(self.catalog.list_templates(admin=True)), 3)
        self.assertEqual({t['id'] for t in self.catalog.list_templates(self.a)}, {restricted['id'], public['id']})
        self.assertEqual([t['id'] for t in self.catalog.list_templates(self.b)], [public['id']])
        for identifier, account in ((system['id'], self.a), (restricted['id'], self.b)):
            with self.assertRaises(KeyError):
                self.catalog.get_template(identifier, account)
            with self.assertRaises(KeyError):
                self.catalog.configuration_for_copy(identifier, account)
        for account in (None, '', 'missing'):
            with self.assertRaises(PermissionError):
                self.catalog.list_templates(account)
            with self.assertRaises(PermissionError):
                self.catalog.get_template(public['id'], account)
        with self.assertRaises(PermissionError):
            self.catalog.get_template(public['id'], self.a, configuration=True)
        view = self.catalog.get_template(restricted['id'], self.a)
        for key in ('baseline', 'settings', 'grants', 'current', 'configuration'):
            self.assertNotIn(key, view)
        self.assertEqual(set(view['state_text']['states'][0]), {'state', 'description'})
        with self.assertRaises(PermissionError):
            self.catalog.save_template({'baseline': [0] * 23})

    def test_admin_edit_and_snapshot_copy(self):
        original = self.template(visibility='restricted', grants=[self.a])
        snapshot = self.catalog.configuration_for_copy(original['id'], self.a)
        snapshot['settings']['recovery'][0] = .7
        self.assertEqual(self.catalog.configuration_for_copy(original['id'], self.a)['settings']['recovery'][0], .25)
        with patch('core.Database', return_value=self.database):
            store = Store(self.database.path, lambda event, state: [0] * 23, 'test')
        copied = store.register(self.catalog.configuration_for_copy(original['id'], self.a))
        edited = self.catalog.save_template({'name': 'Changed', 'baseline': [3] * 23}, original['id'], admin=True)
        self.assertEqual(edited['owner'], 'system')
        self.assertEqual(edited['grants'], [self.a])
        self.assertEqual(store.get(copied['id'])['baseline'], [1] * 23)
        self.assertEqual(self.catalog.configuration_for_copy(original['id'], admin=True)['baseline'], [3] * 23)

    def test_template_validation(self):
        bad = [{'owner': self.a}, {'visibility': 'private'}, {'grants': ['missing']}, {'grants': [self.a]},
               {'visibility': 'restricted', 'grants': [self.a, self.a]}, {'baseline': [True] * 23},
               {'baseline': [float('nan')] * 23}, {'settings': {'pressureEnabled': 1}},
               {'settings': {'admin': True}}, {'name': 'n' * 181}, {'description': 'd' * 20001}]
        for override in bad:
            with self.subTest(override=override), self.assertRaises(ValueError):
                self.catalog.save_template(dict({'baseline': [0] * 23}, **override), admin=True)

    def test_description_questions_completion_and_private_persistence(self):
        calls = []
        def generator(context):
            calls.append(copy.deepcopy(context))
            if not context['history']:
                return {'status': 'questions', 'questions': ['How does this person handle stress?']}
            return self.complete(context)
        initial = {'description': 'A patient person', 'idempotency_key': 'initial'}
        first = self.catalog.create_from_description(self.a, 'user-one', initial, generator)
        self.assertEqual(first['status'], 'questions')
        reloaded = Catalog(self.database)
        self.assertEqual(reloaded.create_from_description(self.a, 'user-one', initial, None), first)
        draft = reloaded.get_draft(first['draft_id'], self.a, 'user-one')
        self.assertEqual(draft['description'], initial['description'])
        for account, user in ((self.b, 'user-one'), (self.a, 'user-two')):
            with self.assertRaises(KeyError):
                reloaded.get_draft(first['draft_id'], account, user)
            with self.assertRaises(KeyError):
                reloaded.create_from_description(account, user, {'draft_id': first['draft_id'], 'answers': 'Calmly', 'idempotency_key': 'forbidden'}, generator)
        follow = {'draft_id': first['draft_id'], 'answers': ['Calmly'], 'idempotency_key': 'answer'}
        final = reloaded.create_from_description(self.a, 'user-one', follow, generator)
        self.assertEqual(final['status'], 'complete')
        self.assertEqual(final, reloaded.create_from_description(self.a, 'user-one', follow, None))
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1]['history'][0]['answers'], ['Calmly'])
        created = self.catalog.get_template(final['template']['id'], admin=True, configuration=True)
        self.assertEqual(created['owner'], 'system')
        self.assertEqual(created['visibility'], 'restricted')
        self.assertEqual(created['grants'], [self.a])
        self.assertTrue(created['settings']['pressureEnabled'])
        self.assertNotIn('baseline', final['template'])
        with self.assertRaises(KeyError):
            self.catalog.configuration_for_copy(created['id'], self.b)
        with self.assertRaises(Conflict):
            reloaded.create_from_description(self.a, 'user-one', dict(follow, idempotency_key='after'), generator)
        with self.assertRaises(Conflict):
            reloaded.create_from_description(self.a, 'user-one', dict(initial, description='Changed'), generator)

    def test_generated_scope_rejection_and_rollback(self):
        payload = {'description': 'Person', 'idempotency_key': 'bad'}
        cases = [None, {'status': 'unknown'}, {'status': 'questions', 'questions': []},
                 {'status': 'questions', 'questions': ['?'], 'admin': True},
                 {'status': 'complete', 'visibility': 'public', 'configuration': {'baseline': [0] * 23}},
                 {'status': 'complete', 'grants': [self.b], 'configuration': {'baseline': [0] * 23}},
                 {'status': 'complete', 'configuration': {'baseline': [0] * 23, 'owner': self.b}},
                 {'status': 'complete', 'configuration': {'baseline': [0] * 22}},
                 {'status': 'complete', 'configuration': {'baseline': [False] * 23}},
                 {'status': 'complete', 'configuration': {'baseline': [0] * 23, 'settings': {'seed': -1}}}]
        for generated in cases:
            with self.subTest(generated=generated), self.assertRaises(ValueError):
                self.catalog.create_from_description(self.a, 'user', payload, lambda context: generated)
        with self.assertRaises(ValueError):
            self.catalog.create_from_description(self.a, 'user', payload, None)
        self.assertEqual(self.catalog.list_templates(admin=True), [])
        with self.database.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM description_drafts').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM description_requests').fetchone()[0], 0)
        valid = self.catalog.create_from_description(self.a, 'user', payload, self.complete)
        self.assertEqual(valid['status'], 'complete')
        with self.assertRaises(ValueError):
            self.catalog.create_from_description(self.a, 'user', dict(payload, idempotency_key='scope', admin=True), self.complete)

    def test_concurrent_request_replay_calls_generator_once(self):
        barrier = threading.Barrier(2)
        calls = []
        responses = []
        errors = []
        def worker():
            catalog = Catalog(self.database)
            barrier.wait(timeout=5)
            try:
                responses.append(catalog.create_from_description(self.a, 'user', {'description': 'Patient', 'idempotency_key': 'same'},
                                 lambda context: (calls.append(context), self.complete(context))[1]))
            except Exception as error:
                errors.append(error)
        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(errors, [])
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(responses), 2)
        self.assertEqual(responses[0], responses[1])
        self.assertEqual(len(self.catalog.list_templates(admin=True)), 1)

    def test_account_wide_draft_is_separate_from_named_user(self):
        first = self.catalog.create_from_description(self.a, None, {'description': 'Patient', 'idempotency_key': 'account'},
                    lambda context: {'status': 'questions', 'questions': ['How patient?']})
        self.assertEqual(self.catalog.get_draft(first['draft_id'], self.a, None)['description'], 'Patient')
        with self.assertRaises(KeyError):
            self.catalog.get_draft(first['draft_id'], self.a, 'named')

    def test_admin_can_create_system_owned_description_without_account(self):
        result = self.catalog.create_from_description(
            None, None, {'description': 'A system character', 'idempotency_key': 'system-create'},
            self.complete, admin=True,
        )
        self.assertEqual(result['status'], 'complete')
        template = self.catalog.get_template(result['template']['id'], admin=True, configuration=True)
        self.assertEqual(template['visibility'], 'system')
        self.assertEqual(template['owner'], 'system')
        self.assertEqual(template['grants'], [])
        with self.database.connect() as db:
            row = db.execute('SELECT account_id,user_id FROM description_drafts WHERE id=?',
                             (result['draft_id'],)).fetchone()
        self.assertEqual(tuple(row), (Catalog.SYSTEM_DRAFT_ACCOUNT, ''))

    def test_admin_can_continue_legacy_account_scoped_description_draft(self):
        initial = {'description': 'A system character', 'idempotency_key': 'legacy-question'}
        questions = self.catalog.create_from_description(
            self.a, None, initial,
            lambda context: {'status': 'questions', 'questions': ['What matters to them?']},
            admin=True,
        )
        result = self.catalog.create_from_description(
            None, None,
            {'draft_id': questions['draft_id'], 'answers': ['Loyalty'], 'idempotency_key': 'legacy-answer'},
            self.complete, admin=True,
        )
        template = self.catalog.get_template(result['template']['id'], admin=True, configuration=True)
        self.assertEqual(template['visibility'], 'system')
        with self.database.connect() as db:
            row = db.execute('SELECT account_id,user_id FROM description_drafts WHERE id=?',
                             (questions['draft_id'],)).fetchone()
        self.assertEqual(tuple(row), (Catalog.SYSTEM_DRAFT_ACCOUNT, ''))


if __name__ == '__main__':
    unittest.main()
