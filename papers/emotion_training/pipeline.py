"""CPU-only, neutral-state emotional adjustment regression. No API calls."""
import argparse
import hashlib
import importlib.metadata
import json
import random
import re
from pathlib import Path

import numpy as np
import torch
from sklearn.model_selection import GroupShuffleSplit
from torch import nn

DIMENSIONS = ['Self-preservation', 'Threat vigilance', 'Mobilization', 'Alarm',
              'Aversion', 'Anger/resistance', 'Frustration', 'Agency', 'Exploration',
              'Reward pursuit', 'Pleasure', 'Contentment', 'Affiliation',
              'Trust/openness', 'Social withdrawal', 'Care', 'Separation distress',
              'Grief/sadness', 'Recognition seeking', 'Shame/exposure sensitivity',
              'Guilt/repair motivation', 'Restoration', 'Play']
ENCODER = 'sentence-transformers/all-MiniLM-L6-v2'


def versions():
    return {name: importlib.metadata.version(name) for name in
            ('numpy', 'torch', 'scikit-learn', 'sentence-transformers', 'transformers')}


def file_digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024*1024), b''):
            h.update(block)
    return h.hexdigest()


def vector(value):
    if not isinstance(value, list) or len(value) != 23:
        raise ValueError('Expected an ordered list of 23 numbers')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in value):
        raise ValueError('State/target values must be numbers, not strings or booleans')
    result = np.asarray(value, dtype=np.float32)
    if not np.isfinite(result).all():
        raise ValueError('State/target contains non-finite values')
    return result


def neutral(value):
    result = vector(value)
    if np.any(result != 0):
        raise ValueError('V1 only supports neutral starting states; nonzero states require new training data')
    return result


def event_text(event, perspective):
    if not isinstance(event, str) or not event.strip() or not isinstance(perspective, str) or not perspective.strip():
        raise ValueError('Nonempty event and perspective required')
    return f'Perspective: {perspective}\nEvent: {event}'


def load_records(path, allow_nonzero=False):
    records, seen = [], set()
    with Path(path).open() as source:
        for line in source:
            r = json.loads(line)
            if r['custom_id'] in seen:
                raise ValueError('Duplicate custom_id')
            seen.add(r['custom_id'])
            # Stream large datasets; discard teacher extras from input features.
            records.append(dict(id=r['custom_id'], text=event_text(r['scenario'], r['perspective']),
                                group=r.get('split_group') or re.sub(r'\W+', ' ', r['scenario'].casefold()).strip(),
                                normalized_event=re.sub(r'\W+', ' ', r['scenario'].casefold()).strip(),
                                state=(vector if allow_nonzero else neutral)(r['starting_state']), target=vector(r['label']['adjustments'])))
    # Connect exact duplicates across families as well as all members of each family.
    parents, normalized = {}, {}
    def find(x):
        parents.setdefault(x, x)
        while parents[x] != x:
            parents[x] = parents[parents[x]]
            x = parents[x]
        return x
    for r in records:
        key = r['normalized_event']
        root = find(r['group'])
        if key in normalized:
            parents[root] = find(normalized[key])
        normalized[key] = r['group']
    for r in records:
        r['group'] = find(r['group'])
    if len({r['group'] for r in records}) < 10:
        raise ValueError('At least 10 distinct events required for train/validation/test splits')
    return records


def split_groups(groups, seed):
    indices = np.arange(len(groups))
    trainval, test = next(GroupShuffleSplit(n_splits=1, test_size=.15, random_state=seed).split(indices, groups=groups))
    train, val = next(GroupShuffleSplit(n_splits=1, test_size=.15 / .85, random_state=seed + 1).split(trainval, groups=np.asarray(groups)[trainval]))
    return trainval[train], trainval[val], test


def encode(model, texts):
    # Never silently truncate a long event. A future version can add explicit chunking.
    lengths = [len(model.tokenizer(t, truncation=False)['input_ids']) for t in texts]
    if max(lengths) > model.max_seq_length:
        raise ValueError(f'Event exceeds encoder limit {model.max_seq_length}; longest={max(lengths)}. Review or implement chunking.')
    return model.encode(texts, batch_size=32, normalize_embeddings=True, convert_to_numpy=True, show_progress_bar=len(texts) > 1)


def prepare(args):
    from sentence_transformers import SentenceTransformer
    rows = load_records(args.labels, allow_nonzero=getattr(args, 'allow_nonzero', False))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    model = SentenceTransformer(args.encoder, device='cpu', revision=args.revision)
    model.eval()
    excluded = []
    if getattr(args, 'exclude_overlength', False):
        kept = []
        for row in rows:
            length = len(model.tokenizer(row['text'], truncation=False)['input_ids'])
            if length > model.max_seq_length:
                excluded.append(dict(id=row['id'], tokens=length, reason='Exceeds encoder token limit'))
            else:
                kept.append(row)
        rows = kept
        (out / 'excluded-records.json').write_text(json.dumps(excluded, indent=2))
    embeddings = encode(model, [r['text'] for r in rows]).astype(np.float32)
    # Save exact encoder locally so inference doesn't resolve a different remote revision.
    model.save(str(out / 'encoder'))
    train, val, test = split_groups([r['group'] for r in rows], args.seed)
    np.savez_compressed(out / 'data.npz', embeddings=embeddings,
                        states=np.stack([r['state'] for r in rows]), targets=np.stack([r['target'] for r in rows]),
                        train=train, val=val, test=test)
    metadata = dict(dimensions=DIMENSIONS, encoder=args.encoder, revision=args.revision, packages=versions(),
                    excluded_records=excluded, encoder_token_limit=model.max_seq_length,
                    embedding_dim=embeddings.shape[1], seed=args.seed,
                    neutral_only=not any(np.any(r['state'] != 0) for r in rows),
                    supported_states=np.unique(np.stack([r['state'] for r in rows]), axis=0).tolist(),
                    groups=[r['group'] for r in rows],
                    source_sha256=file_digest(args.labels),
                    data_sha256=hashlib.sha256((out / 'data.npz').read_bytes()).hexdigest(),
                    ids=[r['id'] for r in rows], counts=dict(train=len(train), validation=len(val), test=len(test)))
    (out / 'metadata.json').write_text(json.dumps(metadata, indent=2))
    print(json.dumps(metadata['counts']))


class Regressor(nn.Module):
    def __init__(self, embedding_dim):
        super().__init__()
        self.layers = nn.Sequential(nn.Linear(embedding_dim + 23, 128), nn.GELU(),
                                    nn.Linear(128, 64), nn.GELU(), nn.Linear(64, 23))

    def forward(self, embeddings, states):
        return self.layers(torch.cat([embeddings, states], dim=-1))


def metrics(actual, predicted):
    error = predicted - actual
    meaningful = np.abs(actual) >= .1
    zeros = actual == 0
    return dict(mae=float(np.abs(error).mean()), rmse=float(np.sqrt((error ** 2).mean())),
                per_dimension_mae=dict(zip(DIMENSIONS, np.abs(error).mean(axis=0).tolist())),
                sign_accuracy_at_target_magnitude_01=float((np.sign(actual[meaningful]) == np.sign(predicted[meaningful])).mean()) if meaningful.any() else None,
                false_activation_rate_at_01=float((np.abs(predicted[zeros]) >= .1).mean()) if zeros.any() else None)


def train(args):
    random.seed(args.seed)
    np.random.seed(args.seed)
    torch.manual_seed(args.seed)
    torch.set_num_threads(args.threads)
    folder = Path(args.data)
    meta = json.loads((folder / 'metadata.json').read_text())
    if meta['dimensions'] != DIMENSIONS or meta['data_sha256'] != hashlib.sha256((folder / 'data.npz').read_bytes()).hexdigest():
        raise ValueError('Dataset metadata or fingerprint mismatch')
    with np.load(folder / 'data.npz', allow_pickle=False) as data:
        e, s, y = [torch.from_numpy(data[k].copy()) for k in ('embeddings', 'states', 'targets')]
        tr, va, te = [data[k].copy() for k in ('train', 'val', 'test')]
    if (meta.get('neutral_only',True) and s.count_nonzero()) or not all(torch.isfinite(t).all() for t in (e, s, y)):
        raise ValueError('Invalid training data')
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=False)
    model = Regressor(meta['embedding_dim'])
    optimizer = torch.optim.AdamW(model.parameters(), lr=args.lr, weight_decay=.01)
    loss_fn = nn.HuberLoss(delta=.1)
    best, stale, history = float('inf'), 0, []
    for epoch in range(args.epochs):
        model.train()
        order = np.random.permutation(tr)
        for start in range(0, len(order), args.batch_size):
            ix = order[start:start + args.batch_size]
            optimizer.zero_grad()
            loss = loss_fn(model(e[ix], s[ix]), y[ix])
            loss.backward()
            optimizer.step()
        model.eval()
        with torch.inference_mode():
            val = float(loss_fn(model(e[va], s[va]), y[va]))
        history.append(dict(epoch=epoch + 1, validation_huber=val))
        if val < best:
            best, stale = val, 0
            torch.save(model.state_dict(), out / 'weights.pt')
        else:
            stale += 1
        if stale >= args.patience:
            break
    model.load_state_dict(torch.load(out / 'weights.pt', map_location='cpu', weights_only=True))
    with torch.inference_mode():
        predicted = model(e[te], s[te]).numpy()
    actual = y[te].numpy()
    report = dict(model=metrics(actual, predicted), zero_baseline=metrics(actual, np.zeros_like(actual)),
                  train_mean_baseline=metrics(actual, np.broadcast_to(y[tr].numpy().mean(axis=0), actual.shape)),
                  history=history, parameters=sum(p.numel() for p in model.parameters()),
                  warning='Synthetic teacher agreement, not biological accuracy. Only observed starting states are supported.')
    report['per_starting_state'] = []
    test_states = s[te].numpy()
    for state in np.unique(test_states, axis=0):
        ix = np.all(test_states == state, axis=1)
        report['per_starting_state'].append(dict(state=state.tolist(), records=int(ix.sum()), metrics=metrics(actual[ix],predicted[ix])))
    import os
    config = dict(metadata=meta, packages=versions(), encoder_path=os.path.relpath((folder / 'encoder').resolve(),out.resolve()),
                  training={k: v for k, v in vars(args).items() if k != 'func'})
    (out / 'config.json').write_text(json.dumps(config, indent=2))
    (out / 'metrics.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: v for k, v in report.items() if k != 'history'}, indent=2))


def predict(args):
    from sentence_transformers import SentenceTransformer
    torch.set_num_threads(args.threads)
    folder = Path(args.model)
    config = json.loads((folder / 'config.json').read_text())
    if config['metadata']['dimensions'] != DIMENSIONS:
        raise ValueError('Dimension order mismatch')
    state = vector(json.loads(args.state))
    supported = config['metadata'].get('supported_states', [[0.0]*23])
    if not any(np.allclose(state, s, rtol=0, atol=1e-6) for s in supported):
        raise ValueError('Starting state was not represented in training; refusing unsupported inference')
    encoder_path = Path(config['encoder_path'])
    if not encoder_path.is_absolute():
        encoder_path = folder / encoder_path
    encoder = SentenceTransformer(str(encoder_path), device='cpu', local_files_only=True)
    model = Regressor(config['metadata']['embedding_dim'])
    model.load_state_dict(torch.load(folder / 'weights.pt', map_location='cpu', weights_only=True))
    model.eval()
    embedding = encode(encoder, [event_text(args.event, args.perspective)])
    with torch.inference_mode():
        result = model(torch.from_numpy(embedding), torch.from_numpy(state[None])).numpy()[0]
    print(json.dumps(dict(dimensions=DIMENSIONS, adjustments=result.tolist()), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--labels', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--encoder', default=ENCODER)
    p.add_argument('--revision', default=None)
    p.add_argument('--exclude-overlength', action='store_true', help='Log and exclude long events instead of truncating')
    p.add_argument('--allow-nonzero', action='store_true', help='Explicitly permit state-conditioned training records')
    p.add_argument('--seed', type=int, default=42)
    p.set_defaults(func=prepare)
    p = sub.add_parser('train')
    p.add_argument('--data', required=True)
    p.add_argument('--out', required=True)
    p.add_argument('--epochs', type=int, default=200)
    p.add_argument('--patience', type=int, default=20)
    p.add_argument('--batch-size', type=int, default=64)
    p.add_argument('--lr', type=float, default=.001)
    p.add_argument('--threads', type=int, default=2)
    p.add_argument('--seed', type=int, default=42)
    p.set_defaults(func=train)
    p = sub.add_parser('predict')
    p.add_argument('--model', required=True)
    p.add_argument('--event', required=True)
    p.add_argument('--perspective', default='The first-person event narrator')
    p.add_argument('--state', default=json.dumps([0] * 23))
    p.add_argument('--threads', type=int, default=2)
    p.set_defaults(func=predict)
    args = parser.parse_args()
    for name in ('epochs', 'patience', 'batch_size', 'threads', 'lr'):
        if hasattr(args, name) and getattr(args, name) <= 0:
            parser.error(f'{name} must be positive')
    args.func(args)


if __name__ == '__main__':
    main()
