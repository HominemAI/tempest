# tempest-1

Status: first model family registered during Tempest extraction. No retraining or
new evaluation is implied by this name. Existing checkpoint: `model-v1`.

## Contract

Event/perspective text and a 23-coordinate starting state → 23 unconstrained
incremental adjustments. State application, caps, decay and contextual modifiers
remain caller responsibilities. Episodic memory remains external.

## Current checkpoint

The initial checkpoint is neutral-only and rejects unsupported starting states.
It uses a frozen all-MiniLM-L6-v2 language encoder and a 407→128→64→23 regression
network with 61,975 trainable parameters. The encoder is additional, not included
in that count. Later neutral + personality-0001 training is a separate run.

Local artifacts retain their historical paths:

- weights/config/metrics: `papers/emotion_training/artifacts/model-v1/`
- encoder/features: `papers/emotion_training/artifacts/crowd-envent-v1/`

Measured metrics and methodology remain in `papers/emotion_training/FIRST-RUN.md`
and the checkpoint's `metrics.json`; do not substitute migration tests for a new
model evaluation. Labels are synthetic teacher judgments, not biological truth.

## Identity and versioning

Public identifier: `tempest-1`. Historical artifact identifier: `model-v1`.
Run IDs and dataset/encoder/weight hashes remain distinct from the public name.
Migration preserves weight bytes; only local path resolution changes.
