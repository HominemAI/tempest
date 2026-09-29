"""Namespaced external identities without rewriting legacy user IDs.

All existing storage keys, ownership checks, quotas and idempotency paths use
the same opaque internal ID. Only the HTTP boundary translates these IDs.
"""
import uuid


class UserIdentities:
    def __init__(self, database):
        self.database = database
        with database.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS user_identity_sources(source TEXT NOT NULL, user_id TEXT NOT NULL, internal_id TEXT NOT NULL UNIQUE, PRIMARY KEY(source,user_id))')

    def encode(self, user_id, source='default'):
        if not isinstance(source, str) or not source.strip() or len(source) > 128:
            raise ValueError('source requires 1–128 characters')
        if not isinstance(user_id, str) or not user_id.strip() or len(user_id) > 128:
            raise ValueError('user_id requires 1–128 characters')
        if user_id.startswith('tempest-source:'):
            raise ValueError('Reserved user_id prefix')
        if source == 'default':
            return user_id
        with self.database.connect() as db:
            row = db.execute('SELECT internal_id FROM user_identity_sources WHERE source=? AND user_id=?', (source, user_id)).fetchone()
            if row:
                return row[0]
            internal = 'tempest-source:' + uuid.uuid4().hex
            # Avoid even a pre-existing legacy ID in our reserved namespace.
            for table in ('api_users', 'instance_owners', 'relationship_states', 'description_drafts'):
                if db.execute('SELECT 1 FROM '+table+' WHERE user_id=?', (internal,)).fetchone():
                    raise ValueError('Identity collision; retry request')
            db.execute('INSERT INTO user_identity_sources VALUES(?,?,?) ON CONFLICT(source,user_id) DO NOTHING', (source, user_id, internal))
            return db.execute('SELECT internal_id FROM user_identity_sources WHERE source=? AND user_id=?', (source, user_id)).fetchone()[0]

    def input(self, value):
        if not isinstance(value, dict):
            return value
        result = dict(value)
        if result.get('user_id') not in (None, ''):
            result['user_id'] = self.encode(result['user_id'], result.pop('source', 'default'))
        elif 'source' in result:
            raise ValueError('source requires user_id')
        if isinstance(result.get('events'), list):
            result['events'] = [self.input(event) for event in result['events']]
        return result

    def output(self, value):
        if isinstance(value, list):
            return [self.output(item) for item in value]
        if not isinstance(value, dict):
            return value
        result = {key:self.output(item) for key,item in value.items()}
        internal = result.get('user_id')
        if internal is not None:
            with self.database.connect() as db:
                row = db.execute('SELECT source,user_id FROM user_identity_sources WHERE internal_id=?', (internal,)).fetchone()
            source, external = row if row else ('default', internal)
            result.update(user_id=external, source=source)
            if isinstance(result.get('text'), str) and result['text'].startswith('For user '+internal+' — '):
                result['text'] = 'For user '+external+' ('+source+') — '+result['text'].split(' — ',1)[1]
        return result
