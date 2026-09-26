# Emotional adjustment model — CPU training scaffold

First real training run completed: see [results and usage](FIRST-RUN.md). The saved
checkpoint is `artifacts/model-v1`; warm median end-to-end CPU latency was 12.58 ms
over 100 held-out events. This supersedes the initial untrained smoke test below.

This is a **neutral-state, synthetic-label regression experiment**, not a model of measured human biochemistry. The language encoder is pretrained and frozen; the small regression network learns our 23-channel rubric. No paid API calls occur in this package.

## What runs where

| Piece | Technology | Role |
|---|---|---|
| Labels | Existing batch collector / JSONL | Event, zero starting state, teacher adjustments; retains extras separately |
| Validation and splits | Python, NumPy, scikit-learn | Check numerical targets; group normalized duplicate events into one split |
| Language encoder | Sentence Transformers / all-MiniLM-L6-v2 | Encode event and perspective into 384 numbers, on CPU |
| Cached features | NumPy `.npz` and JSON manifest | Encode once, reuse across training experiments |
| Adjustment network | PyTorch | 407 inputs → 128 → 64 → 23 unconstrained numeric outputs |
| Learning | AdamW + Huber loss | Update only the small network, using training labels |
| Evaluation | NumPy metrics | Held-out MAE, RMSE, per-channel MAE, sign accuracy and false activation rate |
| Saved model | Tensor weights + JSON config | Reload the trained network and exact locally saved encoder |

The network has 61,975 parameters. The encoder is separate and much larger. Calling the entire system a 62k-parameter model would be misleading. No LoRA, generation model, graph store or vector database is needed for this stage. ONNX export, quantization and a persistent inference service are future work.

## The transformer, step by step

Consider: “My friend did not abandon me; she came back to help.”

1. **Tokenization:** text becomes token IDs. A token may be a whole word, a word fragment, or punctuation. IDs are lookup keys, not emotional scores.
2. **Token and position embeddings:** each ID becomes a learned vector. Position information helps preserve order: “I threatened him” is different from “he threatened me.”
3. **Self-attention:** each token's representation is projected into query, key and value vectors. Query–key compatibility determines a weighted combination of value vectors. This allows representations of “abandon” to incorporate surrounding context, potentially including “not” and “came back.” It does not guarantee correct interpretation.
4. **Multiple attention heads:** several learned attention operations run alongside each other. We do not assign a head to fear, anger or grammar. Such roles are not fixed or guaranteed.
5. **Feed-forward transformation:** a small network transforms each contextual token vector. Residual connections and normalization help information and gradients travel through the layers. Attention and feed-forward stages repeat.
6. **Pooling:** this encoder combines the final token vectors using attention-mask-aware mean pooling. Padding is excluded. Sentence Transformers then normalizes the resulting 384-dimensional vector in our configuration.
7. **Our predictor:** concatenate that event vector with the 23 current-state numbers. Two hidden layers transform those 407 numbers into 23 adjustments. The output has no softmax, clamp or sum-to-one constraint.

Attention uses `softmax(QKᵀ / sqrt(d))V`. Its softmax weights select how token information is mixed; that has nothing to do with normalizing our final emotional adjustments.

This is an **encoder**, not an autoregressive chat model. It reads the available input bidirectionally and computes a representation in a forward pass; it does not generate a reply token by token. It also does not hold episodic memory between calls. The caller must supply the relevant event context.

The 384 embedding coordinates are learned features, **not 384 named emotions or chemicals**. Our named, interpretable values are the 23 output channels.

## What training actually does

For each small batch of examples, the predictor produces 23 guesses. Huber loss compares these with the teacher's 23 targets. Backpropagation computes how changing each trainable weight would change that error. AdamW adjusts the weights, and we repeat over the training examples.

The frozen encoder is not in that training loop: we already cached its output. A validation split decides when to stop and which checkpoint to keep. The test split is evaluated after checkpoint selection; do not repeatedly tune against it.

Defaults are float32, learning rate 0.001, batch size 64, Huber delta 0.1, weight decay 0.01, up to 200 epochs, and patience 20. These are initial engineering choices, not scientifically established constants. The Huber threshold changes the loss, **not the allowed output range**.

## Limits that matter

- All first-batch starting states are zero. This cannot teach state-conditioned responses. V1 rejects nonzero state inputs rather than pretending to support them. State slots remain in the architecture for a later dataset.
- Caps, baselines, personality/context multipliers, decay, and applying deltas to a live character remain outside the model. Nothing here implements a runtime character controller.
- The source's emotion/appraisal annotations and all teacher extras are excluded from input features. Extras remain in the original labels file for later research.
- Splits are roughly 70/15/15 by normalized event groups. This prevents exact normalized duplicate leakage, not paraphrase/author leakage. Audit near-duplicates and source grouping before treating evaluation as rigorous.
- Targets are provisional teacher judgments. Metrics measure agreement with those judgments, not biological truth. Review label quality, especially widespread low-magnitude activation.
- Default encoder input length is limited. Preparation fails on overlength text instead of silently truncating it. Explicit chunking or an encoder change requires a deliberate follow-up decision.
- A CPU millisecond target is not yet demonstrated. Measure **warm end-to-end encoding plus prediction**, separately from startup/model loading. The tiny prediction network is only part of the latency.
- Model folders are local artifacts. Config currently records an absolute encoder path; adjust it or regenerate the artifact if moving folders.

## Commands

Run from repository root. Each output folder must be new to avoid overwriting an experiment.

```sh
python3 -m venv papers/emotion_training/.venv
papers/emotion_training/.venv/bin/pip install -r papers/emotion_training/requirements.txt
papers/emotion_training/.venv/bin/python -m unittest discover -s papers/emotion_training -v
```

After the existing batch is terminal, collect and inspect its validation summary. Collection does not retry failed requests automatically.

```sh
python3 papers/emotion_batch.py status
python3 papers/emotion_batch.py collect
```

Then prepare features (first encoder use requires a model download), review split counts, and train:

```sh
papers/emotion_training/.venv/bin/python papers/emotion_training/pipeline.py prepare \
  --labels papers/emotion-labeling/batches/crowd-envent-v1/labels.jsonl \
  --out papers/emotion_training/artifacts/crowd-envent-v1

papers/emotion_training/.venv/bin/python papers/emotion_training/pipeline.py train \
  --data papers/emotion_training/artifacts/crowd-envent-v1 \
  --out papers/emotion_training/artifacts/model-v1

papers/emotion_training/.venv/bin/python papers/emotion_training/pipeline.py predict \
  --model papers/emotion_training/artifacts/model-v1 \
  --event 'My friend came back to help me.'
```

Use `prepare --revision COMMIT_HASH` to select a particular encoder revision. Regardless, preparation saves the resolved encoder locally for inference, fingerprints the labels/features, and records the split IDs. Keep that encoder, dataset metadata and model config together. Requirements specify compatible ranges, not a fully locked environment; archive exact dependency versions with production experiments.

Tests use fabricated numeric embeddings and targets to verify the training machinery without model downloads or API calls. Passing them does not establish language accuracy or runtime performance.

### Initial verification

Seven tests passed: input validation, group separation, teacher-field exclusion, long-input rejection, unclamped outputs, metrics, and synthetic training/checkpoint reload with cache-corruption detection. Dependency consistency also passed.

An actual MiniLM download/CPU smoke test produced a 384-dimensional embedding and 23-dimensional output. With two PyTorch CPU threads, one short event, five warm-ups and 20 measured runs, encoding plus the **untrained** head took approximately **5.63 ms median / 7.19 ms maximum** on this machine. This excludes process startup and model loading, and is not a corpus-wide benchmark. No full batch-label training has been performed at this stage.

## References

- [MiniLM encoder model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2)
- [Sentence Transformers inference backends and optimization](https://www.sbert.net/docs/sentence_transformer/usage/efficiency.html)
- [PyTorch optimization tutorial](https://docs.pytorch.org/tutorials/beginner/basics/optimization_tutorial.html)
- [Grouped train/test splitting](https://scikit-learn.org/stable/modules/generated/sklearn.model_selection.GroupShuffleSplit.html)
