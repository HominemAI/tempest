# Tempest service — draft v1

Standalone CPU HTTP service with persistent PostgreSQL personalities and turn history
in deployment, and SQLite for local development.
No cloud requests. No automatic ticking: one request is one turn. UI data remains
separate. Required registration input: the ordered 23-value baseline in [-10,10].

## Build and run

From the repository root:

```sh
docker build -f service/Dockerfile -t tempest:1 .
docker volume create tempest-data
# Set TEMPEST_API_TOKEN securely in your environment (at least 24 characters).
docker run --rm --name tempest -p 127.0.0.1:8080:8080 \
  -e TEMPEST_API_TOKEN \
  -v tempest-data:/data \
  -v "$PWD/papers/emotion_training/artifacts/jev-discrete-v1-512-20260927:/models/checkpoint:ro" \
  -v "$PWD/papers/emotion_training/artifacts/capacity-comparison-20260926-v1/mpnet-encoder:/models/encoder:ro" \
  tempest:1
```

Before building locally, download the private `model-v1` release asset, verify it
against `service/MODEL_SHA256`, and extract it into `model-bundle/`. GitHub Actions
does this automatically. The published image includes the pinned checkpoint and
encoder, but excludes datasets and credentials. You can override it by mounting a checkpoint containing
`config.json` and `weights.pt`, and its complete SentenceTransformer encoder directory.
The encoder hash must match the checkpoint. The active v2 state-language index and
shared pressure engine/relationship coefficients are included in the image.
Startup loads the model before opening the socket. No model downloads occur.

Local run using the existing training environment:

```sh
TEMPEST_DB=/tmp/tempest-api.sqlite \
papers/emotion_training/.venv/bin/python service/server.py
```

Default host is localhost; container binds 0.0.0.0 internally. Supply TLS via your
ingress/reverse proxy before exposing it. The token grants access to **all** stored
personalities: this is a single-tenant service, not user-level authorization.
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

All routes except `GET /healthz` require `Authorization: Bearer $TEMPEST_API_TOKEN`.

| Request | Purpose |
|---|---|
| `GET /schema` | Dimension order, defaults, limits and versions |
| `POST /personalities` | Register, returning generated ID and initial state |
| `GET /personalities/{id}?format=both` | Values, text, or both (`format=values\|text\|both`) |
| `POST /personalities/{id}/events` | One or more events combined into one turn |
| `GET /personalities/{id}/history?after=0` | Up to 100 turns; paginate using last returned turn |

Registration example:

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
Use a new registration for another configuration; update/import endpoints are not
part of this first version.

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
7. Return language and vectors; atomically persist state plus full turn history.

Response includes `state_text` (existing state index), `delta_text` (authored delta-index
phrases combined into one paragraph, excluding zero categories), `current`, `net_delta`, `combined_event_effect`,
per-event `predictions`, `model_input`, settings, and pressure/baseline snapshots.
Net delta is final minus initial, including recovery and caps. `net_delta_text`
describes these actual numeric differences. `delta_text` describes model-category
changes before translation/multipliers/recovery/caps. For multiple events its
`delta_categories` use strongest positive plus strongest negative categories;
this language summary is separate from the numeric effect union. Each prediction
also includes its own delta-index paragraph. `delta_index_sha256` pins the wording.

Reads do not advance state. Failed classification rolls back the entire turn.
The current implementation recomputes descriptions on reads from the image's
index; stored turn responses retain the descriptions originally returned.
Pin the image, model mounts and indexes for reproducible behavior across deployment.

## Verification

```sh
python3 -m unittest discover -s service -p 'test_*.py' -v
```

Tests use a fake classifier but the real shared pressure/union engine and real
language index. They do not call paid APIs or modify research personalities.
Model smoke testing should additionally be performed with the mounted checkpoint.

## Publishing and infrastructure

The Omni-style GitHub Actions workflow publishes version tags (`v*.*.*`) to
`ghcr.io/hominemai/tempest`, using `GITHUB_TOKEN`, Docker metadata and Buildx.
It tests SQLite and PostgreSQL before publishing. The `model-v1` release stores
only the pinned config/weights/encoder archive (private repository).
PRs build without publishing. Images remain private; Kubernetes needs a GHCR
pull secret. The infra manifest references `omni/ghcr-hominem` and existing
`omni-secrets` PostgreSQL/API-key entries. No secrets are committed here.
