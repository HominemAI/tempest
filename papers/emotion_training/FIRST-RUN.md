# First training run — 2026-09-25

Completed locally on CPU using the first crowd-enVENT labeling batch. No hosted
inference calls or new labeling jobs were made.

## Data and checkpoint

- 6,600 validated teacher-labeled records; 6,599 used.
- `crowd-envent-456` excluded: 488 tokens exceeds the encoder's 256-token limit.
  Original remains in the labeling dataset; no text was silently truncated.
- Grouped split: 4,624 training / 987 validation / 988 test events.
- Frozen MiniLM encoder revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`.
- 61,975-parameter prediction head, 23 output adjustments, neutral states only.
- Default training settings; seed 42; two CPU threads. Stopped at epoch 86;
  checkpoint selected by validation loss at epoch 66. Test metrics did not select it.
- Features and saved encoder: `artifacts/crowd-envent-v1/`.
- Checkpoint, config and full metrics: `artifacts/model-v1/`.
- Head weights occupy 250,645 bytes; this excludes the much larger frozen encoder.

## Held-out results

| Predictor | MAE | RMSE |
|---|---:|---:|
| Trained head | 0.19185 | 0.25822 |
| Training-set mean | 0.28206 | 0.33687 |
| All zeros | 0.29294 | 0.35481 |

MAE is the average absolute difference in rubric units across events and channels;
it is not a percent accuracy. The trained model reduces MAE 31.98% versus the mean
baseline. Sign agreement is 79.14% for teacher targets with absolute magnitude at
least 0.1.

Important weakness: on exactly-zero teacher targets, 38.09% of predictions have
absolute magnitude at least 0.1, versus 24.51% for the mean baseline. Better overall
error does not mean better inactivity detection. No thresholding or caps were added.

## Reload and latency check

Reloaded the saved weights and encoder and obtained finite 23-number predictions
on 100 distinct randomly selected test events (seed 42). Ten warm-ups, two PyTorch
CPU threads, batch size one. Timed token-length validation, encoding and prediction
together; excluded startup/model loading:

- Median: 12.58 ms
- 95th percentile: 33.67 ms
- Maximum: 83.80 ms

This is a local warm benchmark, not a service latency guarantee. No quantization.

## Use

```sh
papers/emotion_training/.venv/bin/python papers/emotion_training/pipeline.py predict \
  --model papers/emotion_training/artifacts/model-v1 \
  --event 'My friend came back to help me.'
```

Feature preparation used `--exclude-overlength` and the pinned encoder revision.
The exclusion list is recorded in dataset metadata and `excluded-records.json`.
Training used the README command with the default configuration. The exact command
arguments and package versions are preserved in the model's `config.json`.

These results measure synthetic teacher agreement, not biological accuracy. Splits
keep exact normalized duplicates together, but do not establish independence across
authors or paraphrases. Teacher input included source annotations that the student
does not receive. Emotional label quality still needs human review. Nonzero starting
states, personality, caps and memory remain outside this experiment.
