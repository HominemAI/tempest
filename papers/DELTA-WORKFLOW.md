# Delta generation and retraining workflow

## Early personality #1 launch

The user requested starting the personality-conditioned pass before neutral
labeling finishes. `python3 papers/start_personality_one.py --submit` validates
the saved baseline-0001 response, merges the final ten repaired story events,
and prepares all 42,600 requests in seven Vertex batches. It submits each batch
without waiting for earlier batches to finish; existing job records and
submission markers prevent duplicate submissions. No output-token cap is added.
The existing neutral workflow is not cancelled or advanced by this command.
The final story repair is now included by the story collector. Other invalid
random-baseline profiles are not used in this pass; they still require separate
review before the original full-profile validation gate can pass.

## Baseline-#1 provider change

The baseline-0001 delta pass is routed to **Vertex gpt-oss-120b**, in the existing
`smg-cluster` / `us-central1` project and `dz_batch_data` bucket. Neutral event
labeling stays on GPT-5 nano. The baseline pass still waits for neutral labeling
and GCP source validation, then processes all 42,600 examples sequentially in
chunks of at most 6,600, checked every 20 minutes. No baseline-delta job is
submitted merely by changing this routing configuration.

GCP uses prompted JSON (without the previously problematic response_format),
an echoed request ID, the full rubric, actual baseline vector and description,
and the same adjustments/extras schema. Outputs are validated and converted to
the existing `labels.jsonl` format for final retraining. Data records retain their
teacher model/provider; evaluation therefore mixes teacher sources and is not
a controlled comparison of models. All prior jobs remain unchanged.

## Recovery update

The user subsequently deferred the 58-request neutral retry. Its remote job is
left untouched, with inputs and job ID retained for later collection/retraining.
It is excluded from the active queue and neutral completion gate: the current
neutral-situations target is 32,942. Batch 3 is next. The later baseline-conditioned
pass still covers all examples; combined training now targets 85,142 deltas before
any logged overlength exclusions. See `delta-workflow-v1/deferral.json` and the
manifest's `deferred_jobs`. Do not mistake these intentionally deferred labels for
silently missing data.

OpenAI submission is now **serial: one active workflow batch at a time**, including
all later neutral-story and baseline-conditioned stages. The monitor checks at
the existing 30-minute interval and submits the next batch only after the previous
batch completes and validates. This follows the queued-token behavior documented
in the [OpenAI rate-limit guide](https://developers.openai.com/api/docs/guides/rate-limits).

Inspection found all 58 request failures were HTTP 429 `credit_balance_exhausted`,
not invalid model labels. Batches 3–5 were rejected before processing with
`token_limit_exceeded`. Recovery preserves all 13,142 successful results and creates
a 58-request billing retry plus three 6,600-request queue retries in new folders.
Old jobs, errors, input files and submission markers remain untouched. The original
partial collection is explicitly approved only for those exact 58 retry IDs;
stage completion still requires 33,000 unique validated labels.

`BILLING_HOLD.json` prevents new requests until the user confirms credits have been
replenished. After confirmation, remove only that hold file and resume the monitor.
The monitor is stopped, not repeatedly testing the billing endpoint. Preparation
is reproducible with `python3 papers/prepare_delta_recovery.py` and makes no API calls.

Authorized sequence, September 25, 2026. A local background Python process performs
these bounded stages; it is not a cloud scheduler or a chat notification timer.

1. Submit five GPT-5 nano batches for 33,000 synthetic situations, generating one
   23-number neutral delta vector plus the established extras per situation.
   Submit 2,000 random-baseline profiles to Vertex gpt-oss-120b in parallel.
2. Check the neutral jobs every 30 minutes and collect/validate their outputs.
3. After those return, check the GCP dependencies every 20 minutes. The original
   transformed-story job is complete, but 106/300 outputs were invalid. One
   bounded repair batch regenerates only those 106 items without JSON mode;
   the other 194 outputs are preserved. No infinite repair/retry loop exists.
4. Once all GCP dependencies are successful and validated, label the 3,000 story
   situations from neutral with GPT-5 nano. Check every 30 minutes.
5. When all neutral labels are complete, select **baseline-0001 by ID**, not arrival
   order. Include its exact numeric state and generated descriptive profile in
   the labeling prompt and relabel all 42,600 examples (6,600 originals + 33,000
   synthetic situations + 3,000 story situations). Each request starts from the
   same profile; the examples are NOT a time-evolving sequence. Check every 30 minutes.
6. After validation, merge all neutral and baseline-0001 delta records (target
   85,200 records) and train a new local CPU model. Preserve the original model.

The randomizer creates 2,000 **vectors of 23 values** (46,000 scalar numbers).
Each coordinate is sampled independently, uniformly from hundredths in [-10,10],
inclusive, with seed 42. Vectors are unique. This is not population-calibrated data.
The baseline state anchors are separate from the original event-delta anchors.
No custom output token caps are applied to these API jobs.

## Files and commands

Run from repository root:

```sh
python3 papers/delta_workflow.py status
python3 papers/delta_workflow.py stop
```

`stop` creates a control marker and stops future local activity; **it does not cancel
already-submitted provider jobs**. The monitor checks the marker between requests
and during waits, not in the middle of an HTTP call or training subprocess.

- `papers/emotion-labeling/batches/delta-workflow-v1/state.json`: current stage,
  PID, last/next check times (Unix seconds), and errors if any.
- `.../delta-workflow-v1/monitor.log`: progress log, no credentials.
- `.../delta-workflow-v1/neutral-situations/`: five frozen request files and jobs.
- `.../delta-workflow-v1/neutral-stories/`: created after GCP validation.
- `.../delta-workflow-v1/baseline-0001-deltas/`: created after all neutral labels.
- `.../delta-workflow-v1/selected-baseline.json`: selected numeric + descriptive state.
- `.../delta-workflow-v1/training/model/`: final weights/config/metrics when complete.
- `.../vertex-random-baselines-oss120b-v2/`: 2,000 profiles, rubric and guide.
- `.../vertex-literature-repair-oss120b-v1/`: 106 invalid story outputs retried once.

The earlier random-baselines-v1 artifacts are an unsubmitted draft using the
problematic JSON-mode setting. The workflow uses v2 instead.

To resume after an interruption/error, first resolve the logged issue. If explicitly
stopped, remove only the workflow's `STOP` control marker, then run
`python3 papers/delta_workflow.py resume` in a persistent process with the required
credentials. Never remove submission markers to retry a possibly-created job;
reconcile with the provider first. The lock prevents simultaneous monitors.

## Safety and evaluation

- Uses the existing OPENAI_API_KEY environment variable and Google ADC. Keys are
  not saved in scripts, state, or logs. Computer sleep, shutdown, expired credentials,
  or ending the background process interrupts monitoring; remote jobs continue.
- OpenAI requests are split below the [documented batch file limit](https://developers.openai.com/api/docs/guides/batch).
  Frozen hashes, saved job IDs and submission markers prevent accidental duplicate
  submissions. An ambiguous submission error stops for reconciliation.
- Provider completion is not data validation. Missing, duplicate, refused,
  malformed or inconsistent results stop dependent work for review. JSON-format
  problems do not get silently interpreted as neutral responses.
- Baseline description generation uses prompted JSON, not response_format, due to
  observed malformed gpt-oss JSON-mode outputs. All 23 numbers must echo exactly;
  all 23 dimension interpretations must align with the supplied vector.
- Family grouping keeps originals, sibling generated events, and both states of
  each event together across train/validation/test. Exact normalized text duplicates
  additionally connect their families. Story siblings share a pair group. This
  still does not guarantee absence of paraphrase/book/author leakage.
- The new model supports the two observed states, not arbitrary 23-value states.
  Nonzero inputs require explicit opt-in during preparation. Predictions reject
  states absent from the saved training metadata. Per-state evaluation is saved.
- Overlength texts are excluded with a recorded ID/token count, never silently
  truncated. Caps, personality multipliers and memory remain external.
- The final model uses the same frozen MiniLM encoder and small regression head,
  comparing against zero and train-mean baselines. This tests teacher agreement,
  not biological accuracy. Fresh splits mean results are not a matched comparison
  to the initial experiment's exact test set.

## Tests

```sh
python3 -m unittest discover -s papers -p test_delta_workflow.py
papers/emotion_training/.venv/bin/python -m unittest discover -s papers/emotion_training
```
