# User identity sources

Every API field named `user_id` now accepts a sibling `source` string (1–128
characters, case-sensitive). Omission means `default`: existing tokens, records,
and clients retain their identity. Explicit `default` is equivalent to omission.
Use a stable integration namespace, for example `denizens` or `discord`.

```json
{
  "events": [
    {"text": "They offer you help.", "user_id": "123", "source": "denizens"}
  ],
  "idempotency_key": "turn-1"
}
```

Relationship reads: `/personalities/{id}/status/users?user_id=123&source=denizens`.
An unfiltered read still returns all relationships, each with `source` and
`user_id`. A source filter requires a user ID; source-only listing is not provided.
Each event can carry its own source. Untagged text events are unchanged.

The same sibling field applies to admin key issuance, instance registration,
template copying, description creation, and per-user limits. User-filtered
instance, usage and limit GET requests accept `source` in the query string.
User-bound tokens retain their source internally; callers cannot change their
ownership or quota identity by submitting a different source. Event actors remain
metadata, not authorization identities.

Relationship identity is `(persona instance, source, user_id)`; ownership and
quotas remain account-scoped. Returned user records include both identity fields.
Existing global persona computation is unchanged; relationships are still not
fed back into inference.

Implementation: `user_identity_sources` maps non-default external pairs to opaque
internal IDs. All existing ownership, quota, draft, replay and relationship keys
use these IDs consistently. This additive table avoids rewriting existing keys,
histories or credentials. Default identities remain stored verbatim. The prefix
`tempest-source:` is reserved for internal IDs and rejected in new external input.
