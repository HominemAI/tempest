# Tempest

Emotion and personality research, dataset generation, editing, and CPU model training.
The first model is **tempest-1**. These are provisional simulation coordinates
and synthetic teacher labels, not a model of measured human biochemistry.

## Current extraction layout

The first migration preserves `papers/` and `docs/` so scripts, corpus-relative
links and submitted batch files remain recognizable. Package reorganization and
the unified Research / Personalities / Events & Deltas / Jobs / Training interface
follow this verified extraction; they are not yet complete.

- Personality/delta editor: `python3 papers/personality_app.py` (port 8767).
- Research explorer: see `papers/SUBJECT-INDEX.md` for its existing server command.
- Background batches: `papers/DELTA-WORKFLOW.md`.
- Training: `papers/emotion_training/README.md`.
- Model: `model-cards/tempest-1.md`.
- Migration: `docs/tempest-migration-plan.md` and local `migration/` records.

Canonical editable personalities and deltas live in SQLite, with revision history.
Provider request files remain frozen. Editing a personality does not change the
state attached to an already-submitted event label.

## Local data and portability

Corpora, SQLite stores, provider runs, weights, encoder assets and caches are local
and ignored by Git. Source and attribution records stay with their data. This
repository has not been published; redistribution rights must be reviewed first.
ADC and API keys remain outside the repo. Environment recreation records are kept
locally in `migration/`; do not copy virtualenv launchers from the old checkout.

Legacy operational manifests are resolved through `papers/portable_paths.py`;
raw request bytes and their recorded hashes remain untouched. The existing model
config now uses an artifact-relative encoder path. The old checkout is retained
as a rollback copy with its monitor stopped. Do not run both monitors.
