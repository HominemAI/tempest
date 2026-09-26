"""Prepare and submit personality #1 deltas without waiting for neutral labels.

Uses the existing idempotent submission guards; never retries ambiguous POSTs.
"""
import argparse
import fcntl
from pathlib import Path

import delta_workflow as w


def prepare():
    cid = 'baseline-0001'
    expected = next(p for p in w.read(w.BASELINES/'profiles.json') if p['request_id'] == cid)
    records = [r for path in sorted((w.BASELINES/'downloaded-output').glob('*.jsonl'))
               for r in w.jsonl(path) if r.get('custom_id') == cid]
    if len(records) != 1:
        raise ValueError('Expected exactly one saved personality #1 response')
    profile = w.validate_profile(w.unpack_vertex(records[0]), expected)
    target = w.WORK/'selected-baseline.json'
    if target.exists() and w.read(target) != profile:
        raise ValueError('Saved personality differs; stop for review')
    w.save(target, profile)
    stories = w.collect_vertex(w.STORIES, 'stories')
    if len(stories) != 3000:
        raise ValueError('Expected all 3,000 story events')
    examples = w.originals() + w.situations() + stories
    if len(examples) != 42600:
        raise ValueError('Expected all 42,600 events')
    manifest = w.prepare_labels('baseline-0001-deltas', examples, profile, provider='vertex')
    print(f"Prepared {manifest['records']} requests in {len(manifest['jobs'])} batches.", flush=True)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--submit', action='store_true')
    args = parser.parse_args()
    with (w.WORK/'personality-launch.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        manifest = prepare()
        if args.submit:
            for folder in manifest['jobs']:
                w.vertex.RUN = Path(folder)
                result = w.vertex.submit()
                print(Path(folder).name, result['name'], result['state'], flush=True)


if __name__ == '__main__':
    main()
