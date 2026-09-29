# Tempest service — draft v1

Standalone CPU HTTP service with persistent PostgreSQL personalities and turn history
in deployment, and SQLite for local development.
Event classification stays local; description creation can use an explicitly
configured external provider. No automatic ticking: one event request is one turn.
The service includes an administrator workspace, scoped account keys, quotas,
templates, and hosted live-model/index views. Research data remains separate.
Administrator registration requires the ordered 23-value baseline in [-10,10].
See [ACCESS.md](ACCESS.md) for setup, credentials, ownership, quotas, and routes.

## Build and run

From the repository root:

```sh
docker build -f service/Dockerfile -t tempest:1 .
docker volume create tempest-data
# Set the setup-only TEMPEST_API_TOKEN securely (at least 24 random characters).
docker run --rm --name tempest -p 127.0.0.1:8080:8080 \
  -e TEMPEST_API_TOKEN \
  -v tempest-data:/data \
  -v "$PWD/papers/emotion_training/artifacts/jev-discrete-v1-512-20260927:/models/checkpoint:ro" \
  -v "$PWD/papers/emotion_training/artifacts/capacity-comparison-20260926-v1/mpnet-encoder:/models/encoder:ro" \
  tempest:1
```

Application releases reuse the digest-pinned, validated `v0.2.0` runtime from
GHCR, including CPU dependencies, checkpoint, and encoder. Authenticate Docker
to GHCR if required to pull that private base. No model download is needed for
this application-only build. The image includes the random-event text pool but
not the source labeling database, credentials, or user records.

For dependency/model changes, use `service/Dockerfile.runtime`: first download
the private `model-v1` release asset, verify `service/MODEL_SHA256`, and extract
it into `model-bundle/`. Build and validate a new runtime, then deliberately update
the pinned base reference in `service/Dockerfile`. You can override weights by mounting a checkpoint containing
`config.json` and `weights.pt`, and its complete SentenceTransformer encoder directory.
The encoder hash must match the checkpoint. The active v2 state-language index and
shared pressure engine/relationship coefficients are included in the image.
Startup loads the model before opening the socket. No model downloads occur.

Local run using the existing training environment:

```sh
TEMPEST_DB=/tmp/tempest-api.sqlite \
TEMPEST_API_TOKEN="$TEMPEST_API_TOKEN" \
papers/emotion_training/.venv/bin/python service/server.py
```

Default host is localhost; container binds 0.0.0.0 internally. Supply TLS via your
ingress/reverse proxy before exposing it. Set `TEMPEST_PUBLIC_ORIGIN` to the exact
HTTPS origin when using a proxy. Open `/admin` to create the first administrator
with the setup token, then issue separate API keys. The setup token no longer
authorizes API requests. Account keys can access only their owned personalities;
optionally bound quota users further restrict ownership. Browser cookies are
secure by default, so use HTTPS for the administrator workspace.
Use one process per container. Turns are serialized within a process, including across
different personalities, to protect the CPU model and avoid lost updates.
PostgreSQL additionally locks each personality row for an atomic turn, including
idempotency checks. Set `DATABASE_URL` or `PG_HOST`, `PG_PORT`, `PG_USER`,
`PG_PASSWORD`, `PG_DATABASE`. `PG_SSLMODE` and `PG_CA_CERT` (PEM or base64 PEM)
reuse Omni's TLS configuration; a supplied CA enforces `verify-full`.
Tables live in schema `tempest`; the existing database role needs permission to
create/use it. `TEMPEST_REQUIRE_POSTGRES=true` prevents silent SQLite fallback.
The Kubernetes deployment is initially one replica. Do not share SQLite volumes
over network filesystems or scale replicas against SQLite.
Back up SQLite with its backup API (not by copying only the main file during writes).

## API

Application APIs require an issued Bearer key or an administrator browser session.
The administrator shell, health check, setup status, setup, and login are public;
the setup secret is required for initial setup. Account responses contain language
and metadata only; administrators can inspect the underlying vectors/settings.
Full route and access details are in [ACCESS.md](ACCESS.md).

| Request | Purpose |
|---|---|
| `GET /schema` | Administrator dimension order, defaults, limits and versions |
| `POST /admin/instances` | Administrator registration with optional account/user ownership |
| `POST /templates/{id}/copy` | Copy a permitted template into an owned instance |
| `POST /characters/describe` | Opt-in description creation or clarification |
| `GET /personalities/{id}` | Current state (administrator vectors/text; account language) |
| `POST /personalities/{id}/events` | One or more events combined into one turn |
| `POST /personalities/{id}/events/users` | Same turn plus actor-specific relationship records |
| `GET /personalities/{id}/status/users` | Accumulated relationship adjustment language |
| `GET /personalities/{id}/history?after=0` | Up to 100 turns; paginate using last returned turn |

Administrator registration example (`POST /admin/instances`; add `account_id`
and optional bound quota `user_id` to assign ownership):

```json
{
  "name": "Example",
  "baseline": [0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0,0],
  "settings": {"pressureEnabled": true, "longEnabled": false}
}
```

Optional settings (use `GET /schema` for full arrays):

- `recovery`: 23 fractions [0,1], default .25.
- `multipliers`: 23 values [-1,2], default 1.
- `effects`: nine category magnitudes ordered -4 through +4; each [-10,10].
  Default `[-2,-1,-.5,-.25,0,.25,.5,1,2]`.
- `constitution`: optional per-state arrays `sensitivity`, `shift`, `returnRate`,
  `release`, `longRate`, matching the UI. Omitted arrays use defaults.
- `pressureEnabled`, `longEnabled`, `relationshipsEnabled`: default false.
  Long-term adaptation and relationships only operate when pressure is enabled.
- `seed`: description seed, default 42 (state text currently uses nearest anchors).

Baseline is immutable within this API version. Current state initially equals it;
temporary/adapted baselines start there, with pressure zero. UI-only controls such
as random event selection, graph colors and animation timing are not server settings.
Administrators can update the name and settings through
`POST /admin/instances/{id}` with the current `expected_turn`. The original
baseline and ownership remain immutable; create another instance to change them.

Turn example:

```json
{
  "events": ["A colleague thanks them for their help.", "Their train is delayed."],
  "idempotency_key": "conversation-42-turn-7",
  "expected_turn": 0
}
```

`expected_turn` is optional optimistic concurrency protection. Idempotency keys
are required and scoped to the personality. Retry the **identical request** after
a timeout: it returns the stored response without applying another turn. Reusing
a key with a different payload, or a stale expected turn, returns HTTP 409.
Up to 32 events; each must fit the checkpoint's token limit (512 for the default),
with a 20,000-character preliminary limit. No silent truncation.

Turn sequence:

1. Recover toward the active baseline once.
2. Classify all events against the same recovered vector.
3. Map each category to its effect and multiply by that state's multiplier.
4. Per state: strongest positive + most negative (zero if absent). No stacking.
5. Feed the combined **unclipped** effect into the same pressure engine as the UI.
6. Add combined effect to recovered state; clamp [-10,10].
7. Return the authorized view; atomically persist state plus full turn history.

Administrator response includes `state_text` (existing state index), `delta_text` (authored delta-index
phrases combined into one paragraph, excluding zero categories), `current`, `net_delta`, `combined_event_effect`,
per-event `predictions`, `model_input`, settings, and pressure/baseline snapshots.
Net delta is final minus initial, including recovery and caps. `net_delta_text`
describes these actual numeric differences. `delta_text` describes model-category
changes before translation/multipliers/recovery/caps. For multiple events its
`delta_categories` use strongest positive plus strongest negative categories;
this language summary is separate from the numeric effect union. Each prediction
also includes its own delta-index paragraph. `delta_index_sha256` pins the wording.
Account keys receive `id`, `name`, `turn`, `state_text`, `delta_text`, and any
actor-specific relationship language, without numeric predictions or state.
Tagged events use `{ "text": "...", "user_id": "actor-id" }`; this actor ID is
metadata, not a credential or quota identity. Relationship adjustments are
additional records and do not cause an extra global update or feed inference yet.

Reads do not advance state. Failed classification rolls back the entire turn.
The current implementation recomputes descriptions on reads from the image's
index; stored turn responses retain the descriptions originally returned.
Pin the image, model mounts and indexes for reproducible behavior across deployment.

## Verification

```sh
python3 -m unittest discover -s service -p 'test_*.py' -v
```

Tests use a fake classifier and injected description generator, but the real
shared pressure/union engine and language index. They cover access, isolation,
quotas, template visibility, description workflows, and relationships on SQLite
and PostgreSQL. They do not call paid APIs or modify research personalities.
Model smoke testing should additionally be performed with the mounted checkpoint.

## Publishing and infrastructure

The Omni-style GitHub Actions workflow publishes version tags (`v*.*.*`) to
`ghcr.io/hominemai/tempest`, using `GITHUB_TOKEN`, Docker metadata and Buildx.
It tests SQLite and PostgreSQL before publishing. The `model-v1` release stores
only the pinned config/weights/encoder archive (private repository).
PRs build without publishing. Images remain private; this SMG cluster already
has working pull access, so its manifest does not add a pull secret. Other
clusters need their own registry authorization. The manifest uses existing
`omni-secrets` PostgreSQL/setup-token entries. No secrets are committed here.
