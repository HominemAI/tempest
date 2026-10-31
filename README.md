# Tempest

Tempest is HOMINEM's experimental system for behavioral dispositions and personality state. It combines CPU model inference with an event-driven state engine, persistent turn history, and tools for training and evaluating synthetic teacher labels.

The 23 coordinates are simulation variables, not measured human biochemistry. Tempest does not establish biological or psychological validity.

## Repository guide

- [Service and API](service/README.md): persistent personalities, event turns, PostgreSQL/SQLite storage, and HTTP integration.
- [Access model](service/ACCESS.md): setup, account keys, ownership, and quotas.
- [Training pipeline](papers/emotion_training/README.md): data preparation, frozen encoders, training, and evaluation.
- [First regression experiment](papers/emotion_training/FIRST-RUN.md): held-out results, baselines, and limitations for the original neutral-state checkpoint.
- [Model card](model-cards/tempest-1.md): the original checkpoint's contract. This is distinct from the later discrete classifier used by the service.
- [Data and artifacts](DATA.md): what is included and what must be supplied separately.

## Run source-level checks

Python 3.11 and Node.js are required. From the repository root:

```sh
python3 -m unittest discover -s service -p 'test_*.py'
```

The service tests use a fake classifier and the real state engine; they do not require model weights, paid inference, or a production database. PostgreSQL tests require a separately configured test database.

## Scope

This is an early research and systems project. Training code and service source are public; datasets, checkpoints, encoders, credentials, and private working records are not distributed here. Running real inference requires compatible model artifacts. The Docker application build currently depends on a private runtime image; see the service guide before building.

Research collection tools remain under `papers/` for compatibility. Their corpus-dependent commands require local inputs and may submit paid labeling jobs when explicitly invoked. Reading or testing the repository does not require submitting those jobs.
