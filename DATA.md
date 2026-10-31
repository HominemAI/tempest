# Data and model artifacts

Git tracks source code, tests, reusable prompts, model metadata, and selected evaluation reports. Working data and model artifacts are kept outside the public repository.

## Local inputs and outputs

| Location | Contents |
| --- | --- |
| `papers/emotion-labeling/` | Source events, provider requests and responses, labels, and batch records |
| `papers/personality-data/` | Local personality records |
| `papers/emotion_training/artifacts/` | Checkpoints, encoder assets, features, split records, and experiment outputs |
| `papers/personality-mapping/batches/` and `workflows/` | Language-index generation jobs and working records |
| `model-bundle/` | Locally supplied checkpoint and encoder for runtime builds |
| Ignored corpus directories under `papers/` | Research full texts and source-specific records |

SQLite databases, logs, generated corpus indexes, caches, and local planning notes are ignored as well. Published language anchors and the active mapping manifest remain tracked because the state engine uses them.

## Reproducing an experiment

See [training instructions](papers/emotion_training/README.md) for the label contract and preparation commands. Supply lawfully obtained source data and compatible teacher labels at the documented paths. Preserve source attribution, dataset checksums, split assignments, encoder revision, and checkpoint configuration with each run.

The [first-run report](papers/emotion_training/FIRST-RUN.md) describes the original experiment; its dataset and weights are not included. The repository's synthetic test fixtures check implementation behavior, not reproduction of that report's metrics.

## Distribution

No dataset or model-weight release is part of this source publication. Corpus access does not grant redistribution rights. Any later dataset or model release needs its own provenance, licensing, version, and checksums. Private model bundles and container images remain separately controlled.

## Runtime event examples

`service/example_events.json` contains five synthetic examples for source-only development. The full event pool is ignored at `service/random_events.json`. Set `TEMPEST_EVENT_POOL` to a separately supplied JSON file with a nonempty `events` array of strings (at most 1,200 characters each). These examples are not training or evaluation records.
