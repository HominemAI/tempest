"""System-owned templates and private, persistent description-generation drafts.

The caller authenticates identities and supplies the admin flag. A generator is
an injected callable, never an implicit network client. Its output can describe
configuration or ask questions, but cannot choose ownership or access scope.
"""
import copy
import hashlib
import json
import threading
import time
import uuid

from core import Conflict, active_mapping, defaults, describe, settings, validate_vector


def bounded_text(value, name, maximum, required=False):
    if not isinstance(value, str) or len(value) > maximum or (required and not value.strip()):
        raise ValueError(f'{name} must be {"a nonempty" if required else "a"} string of at most {maximum} characters')
    return value


class Catalog:
    # The existing draft tables require a non-null account key. Keep system
    # drafts in a reserved internal namespace while resulting templates and
    # instances remain genuinely system-owned (no account owner).
    SYSTEM_DRAFT_ACCOUNT = '__tempest_system__'

    def __init__(self, database, account_exists=None):
        self.database = database
        self.account_exists = account_exists or self._account_exists
        self.lock = threading.RLock()
        self.mapping = active_mapping()
        with database.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS system_templates(id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS description_drafts(id TEXT PRIMARY KEY, account_id TEXT NOT NULL, user_id TEXT NOT NULL, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS description_requests(account_id TEXT NOT NULL, user_id TEXT NOT NULL, request_key TEXT NOT NULL, fingerprint TEXT NOT NULL, response TEXT, PRIMARY KEY(account_id,user_id,request_key))')

    def _account_exists(self, account_id):
        with self.database.connect() as db:
            return bool(db.execute('SELECT id FROM api_accounts WHERE id=?', (account_id,)).fetchone())

    def _account(self, account_id):
        if not isinstance(account_id, str) or not account_id or len(account_id) > 128:
            raise PermissionError('Authenticated API account required')
        if not self.account_exists(account_id):
            raise PermissionError('API account does not exist')

    @staticmethod
    def _configuration(payload):
        if not isinstance(payload, dict) or set(payload) - {'name', 'baseline', 'settings'}:
            raise ValueError('Configuration accepts only name, baseline, and settings')
        validate_vector(payload.get('baseline'))
        return {'name': bounded_text(payload.get('name', ''), 'name', 180),
                'baseline': list(payload['baseline']), 'settings': settings(copy.deepcopy(payload.get('settings', {})))}

    def _template(self, payload, previous=None):
        allowed = {'name', 'description', 'baseline', 'settings', 'visibility', 'grants'}
        if not isinstance(payload, dict) or set(payload) - allowed:
            raise ValueError('Invalid template fields; ownership is always system')
        merged = {key: copy.deepcopy(previous[key]) for key in allowed if previous and key in previous}
        merged.update(copy.deepcopy(payload))
        config = self._configuration({key: merged[key] for key in ('name', 'baseline', 'settings') if key in merged})
        visibility = merged.get('visibility', 'system')
        if visibility not in ('system', 'restricted', 'public'):
            raise ValueError('visibility must be system, restricted, or public')
        grants = merged.get('grants', [])
        if not isinstance(grants, list) or len(grants) > 1000 or any(not isinstance(g, str) or not g or len(g) > 128 for g in grants):
            raise ValueError('grants must be a list of API account IDs')
        if len(set(grants)) != len(grants):
            raise ValueError('Duplicate template grants')
        for grant in grants:
            if not self.account_exists(grant):
                raise ValueError('Template grant refers to an unknown API account')
        if visibility == 'system' and grants:
            raise ValueError('System-only templates cannot have account grants')
        return dict(config, description=bounded_text(merged.get('description', ''), 'description', 20000),
                    visibility=visibility, grants=grants, owner='system')

    @staticmethod
    def _read(db, identifier, lock=False, postgres=False):
        bounded_text(identifier, 'template ID', 128, True)
        suffix = ' FOR UPDATE' if lock and postgres else ''
        row = db.execute('SELECT body FROM system_templates WHERE id=?' + suffix, (identifier,)).fetchone()
        if not row:
            raise KeyError('Template not found')
        return json.loads(row[0])

    @staticmethod
    def _allowed(template, account_id, admin):
        return admin or (template['visibility'] != 'system' and
                         (template['visibility'] == 'public' or account_id in template['grants']))

    def _present(self, template, admin=False, configuration=False):
        result = {key: copy.deepcopy(template[key]) for key in ('id', 'name', 'description', 'owner', 'visibility', 'created_at', 'updated_at')}
        result['state_text'] = describe(template['baseline'], self.mapping, seed=template['settings']['seed'],
                                        mode='nearest', richness=True)['teacher_payload']
        if admin:
            result['grants'] = list(template['grants'])
        if configuration:
            result.update(baseline=list(template['baseline']), settings=copy.deepcopy(template['settings']))
        return result

    def save_template(self, payload, identifier=None, admin=False):
        if not admin:
            raise PermissionError('Only administrators may edit system templates')
        with self.lock, self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = self._read(db, identifier, True, self.database.postgres) if identifier is not None else None
            template = self._template(payload, previous)
            now = int(time.time())
            template.update(id=identifier or str(uuid.uuid4()), created_at=previous['created_at'] if previous else now, updated_at=now)
            if previous:
                db.execute('UPDATE system_templates SET body=? WHERE id=?', (json.dumps(template, allow_nan=False), identifier))
            else:
                db.execute('INSERT INTO system_templates VALUES(?,?)', (template['id'], json.dumps(template, allow_nan=False)))
            return self._present(template, admin=True, configuration=True)

    def list_templates(self, account_id=None, admin=False):
        if not admin:
            self._account(account_id)
        with self.database.connect() as db:
            templates = [json.loads(row[0]) for row in db.execute('SELECT body FROM system_templates ORDER BY id')]
        return [self._present(template, admin=admin) for template in templates if self._allowed(template, account_id, admin)]

    def get_template(self, identifier, account_id=None, admin=False, configuration=False):
        if not admin:
            self._account(account_id)
        with self.database.connect() as db:
            template = self._read(db, identifier)
        if not self._allowed(template, account_id, admin):
            raise KeyError('Template not found')
        if configuration and not admin:
            raise PermissionError('Raw template configuration is administrator-only')
        return self._present(template, admin=admin, configuration=configuration)

    def configuration_for_copy(self, identifier, account_id=None, admin=False):
        """Internal service boundary: return a fresh snapshot for Store.register."""
        if not admin:
            self._account(account_id)
        with self.database.connect() as db:
            template = self._read(db, identifier)
        if not self._allowed(template, account_id, admin):
            raise KeyError('Template not found')
        return self._configuration({key: template[key] for key in ('name', 'baseline', 'settings')})

    def _read_draft(self, db, identifier, account_id, user_id, lock=False):
        bounded_text(identifier, 'draft_id', 128, True)
        suffix = ' FOR UPDATE' if lock and self.database.postgres else ''
        row = db.execute('SELECT body FROM description_drafts WHERE id=? AND account_id=? AND user_id=?' + suffix,
                         (identifier, account_id, user_id)).fetchone()
        if not row:
            raise KeyError('Description draft not found')
        return json.loads(row[0])

    def get_draft(self, identifier, account_id, user_id):
        self._account(account_id)
        user_id = self._user(user_id)
        with self.database.connect() as db:
            draft = self._read_draft(db, identifier, account_id, user_id)
        return copy.deepcopy(draft)

    @staticmethod
    def _user(user_id):
        # Account-wide credentials own a distinct namespace from named users.
        return '' if user_id is None else bounded_text(user_id, 'user_id', 128)

    @staticmethod
    def _answers(value):
        if isinstance(value, str):
            return bounded_text(value, 'answers', 20000, True)
        if isinstance(value, list) and 1 <= len(value) <= 10:
            result = [bounded_text(answer, 'answer', 20000, True) for answer in value]
            if sum(map(len, result)) <= 20000:
                return result
        raise ValueError('answers must be nonempty text or 1–10 nonempty strings, at most 20,000 characters total')

    @staticmethod
    def _generated(value):
        if not isinstance(value, dict):
            raise ValueError('Description generator must return a JSON object')
        status = value.get('status')
        if status == 'questions':
            if set(value) - {'status', 'questions'}:
                raise ValueError('Unexpected generated question fields')
            questions = value.get('questions')
            if not isinstance(questions, list) or not 1 <= len(questions) <= 10:
                raise ValueError('Generator must return 1–10 questions')
            return {'status': status, 'questions': [bounded_text(q, 'question', 2000, True) for q in questions]}
        if status == 'complete':
            if set(value) - {'status', 'configuration', 'description'}:
                raise ValueError('Unexpected generated configuration fields; access scope cannot be generated')
            return {'status': status, 'configuration': Catalog._configuration(value.get('configuration')),
                    'description': bounded_text(value.get('description', ''), 'description', 20000)}
        raise ValueError('Generator status must be questions or complete')

    def create_from_description(self, account_id, user_id, payload, generator, admin=False):
        """Generate or clarify; persist one result per identity and request key.

        Initial payload: {description, idempotency_key}. Follow-up payload:
        {draft_id, answers, idempotency_key}. The callable receives description,
        history, and a configuration schema; returns questions or complete.
        Generation runs within a serialized DB transaction so concurrent replay
        cannot issue duplicate provider requests. Failed generation rolls back.
        """
        system_owned = admin and account_id is None
        if system_owned:
            if user_id is not None:
                raise ValueError('System-owned personality creation cannot specify a user')
            account_id = self.SYSTEM_DRAFT_ACCOUNT
            user_id = None
        else:
            self._account(account_id)
        user_id = self._user(user_id)
        if not isinstance(payload, dict):
            raise ValueError('Description request must be an object')
        key = bounded_text(payload.get('idempotency_key'), 'idempotency_key', 128, True)
        followup = 'draft_id' in payload
        allowed = {'draft_id', 'answers', 'idempotency_key'} if followup else {'description', 'idempotency_key'}
        if set(payload) - allowed:
            raise ValueError('Unexpected description request fields')
        if followup:
            bounded_text(payload['draft_id'], 'draft_id', 128, True)
            answers = self._answers(payload.get('answers'))
        else:
            bounded_text(payload.get('description'), 'description', 20000, True)
        fingerprint = hashlib.sha256(json.dumps(payload, sort_keys=True, allow_nan=False).encode()).hexdigest()
        with self.lock, self.database.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO description_requests(account_id,user_id,request_key,fingerprint,response) VALUES(?,?,?,?,NULL) ON CONFLICT(account_id,user_id,request_key) DO NOTHING',
                       (account_id, user_id, key, fingerprint))
            suffix = ' FOR UPDATE' if self.database.postgres else ''
            old = db.execute('SELECT fingerprint,response FROM description_requests WHERE account_id=? AND user_id=? AND request_key=?' + suffix,
                             (account_id, user_id, key)).fetchone()
            if old[0] != fingerprint:
                raise Conflict('Idempotency key reused with different content')
            if old[1] is not None:
                return json.loads(old[1])
            if not callable(generator):
                raise ValueError('Description generation is not configured')
            if followup:
                draft = self._read_draft(db, payload['draft_id'], account_id, user_id, True)
                if draft['status'] != 'questions':
                    raise Conflict('Description draft is already complete')
                if len(draft['history']) >= 12:
                    raise ValueError('Description draft follow-up limit reached')
                if isinstance(answers, list) and len(answers) != len(draft['questions']):
                    raise ValueError('Provide one answer per question')
                draft['history'].append({'questions': draft['questions'], 'answers': answers})
            else:
                draft = {'draft_id': str(uuid.uuid4()), 'description': payload['description'], 'history': [], 'status': 'pending'}
            context = {'description': draft['description'], 'history': copy.deepcopy(draft['history']),
                       'schema': {'configuration_fields': ['name', 'baseline', 'settings'], 'baseline_count': 23,
                                  'baseline_range': [-10, 10], 'settings_defaults': defaults(),
                                  'dimensions': [d['name'] for d in json.loads(self.mapping.read_text())['dimensions']]}}
            generated = self._generated(generator(context))
            draft['status'] = generated['status']
            draft['updated_at'] = int(time.time())
            if generated['status'] == 'questions':
                draft['questions'] = generated['questions']
                response = {'draft_id': draft['draft_id'], 'status': 'questions', 'questions': list(draft['questions'])}
            else:
                template = self._template(dict(generated['configuration'], description=generated['description'] or draft['description'],
                                               visibility='system' if system_owned else 'restricted',
                                               grants=[] if system_owned else [account_id]))
                template.update(id=str(uuid.uuid4()), created_at=draft['updated_at'], updated_at=draft['updated_at'])
                db.execute('INSERT INTO system_templates VALUES(?,?)', (template['id'], json.dumps(template, allow_nan=False)))
                draft.pop('questions', None)
                draft['template_id'] = template['id']
                response = {'draft_id': draft['draft_id'], 'status': 'complete', 'template': self._present(template)}
            if followup:
                db.execute('UPDATE description_drafts SET body=? WHERE id=? AND account_id=? AND user_id=?',
                           (json.dumps(draft, allow_nan=False), draft['draft_id'], account_id, user_id))
            else:
                db.execute('INSERT INTO description_drafts VALUES(?,?,?,?)',
                           (draft['draft_id'], account_id, user_id, json.dumps(draft, allow_nan=False)))
            db.execute('UPDATE description_requests SET response=? WHERE account_id=? AND user_id=? AND request_key=?',
                       (json.dumps(response, allow_nan=False), account_id, user_id, key))
            return response
