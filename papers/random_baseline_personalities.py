"""Seeded 23-value baseline randomizer and Vertex batch preparation. No network."""
import argparse
import hashlib
import json
from pathlib import Path
import random
import re

from emotion_batch import ROOT, PROMPT, read, save
import vertex_literature_batch as vertex

RUN = ROOT / 'papers/emotion-labeling/batches/vertex-random-baselines-oss120b-v2'


def random_states(count=2000, seed=42):
    if count <= 0:
        raise ValueError('count must be positive')
    rng, seen, rows = random.Random(seed), set(), []
    while len(rows) < count:
        values = tuple(rng.randint(-1000, 1000) / 100 for _ in range(23))
        if values in seen:
            continue
        seen.add(values)
        rows.append(dict(request_id=f'baseline-{len(rows)+1:04d}', state_values=list(values)))
    return rows


def validate_profile(value, expected):
    fields = {'request_id', 'state_values', 'description', 'likely_feelings', 'needs',
              'action_tendencies', 'possible_physical_experiences', 'tensions',
              'uncertainty', 'dimension_readings'}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError('Unexpected baseline schema')
    if value['request_id'] != expected['request_id'] or value['state_values'] != expected['state_values']:
        raise ValueError('Baseline ID/vector mismatch')
    if any(type(v) not in (int, float) or not -10 <= v <= 10 for v in value['state_values']):
        raise ValueError('Invalid baseline numbers')
    if not isinstance(value['description'], str) or not value['description'].strip():
        raise ValueError('Empty baseline description')
    for key in fields - {'request_id', 'state_values', 'description', 'dimension_readings'}:
        if not isinstance(value[key], list) or any(not isinstance(s, str) or not s.strip() for s in value[key]):
            raise ValueError(f'Invalid {key}')
    if not isinstance(value['dimension_readings'], list) or len(value['dimension_readings']) != 23:
        raise ValueError('Expected 23 dimension interpretations')
    for i, (item, n) in enumerate(zip(value['dimension_readings'], expected['state_values']), 1):
        if (not isinstance(item, dict) or set(item) != {'index', 'value', 'interpretation'}
                or type(item['index']) is not int or item['index'] != i
                or type(item['value']) not in (int, float) or item['value'] != n
                or not isinstance(item['interpretation'], str) or not item['interpretation'].strip()):
            raise ValueError('Invalid dimension interpretation')
    return value


def prepare(out=RUN, count=2000, seed=42):
    out = Path(out)
    if out.exists():
        raise ValueError('Preserve existing run; choose a new directory')
    template = PROMPT.read_text()
    rubric = template.split('## Our rubric\n', 1)[1].split('### Adjustment magnitude anchors', 1)[0]
    prompt = (ROOT / 'docs/random-baseline-personalities-prompt.md').read_text().replace('{{RUBRIC}}', rubric)
    dimensions = [l.split('|')[2].strip() for l in rubric.splitlines() if re.match(r'\| \d+ \|', l)]
    assert len(dimensions) == 23
    rows = random_states(count, seed)
    out.mkdir(parents=True)
    save(out / 'profiles.json', rows)
    save(out / 'dimensions.json', dimensions)
    (out / 'prompt.md').write_text(prompt)
    with (out / 'input.jsonl').open('w') as target:
        for row in rows:
            body = dict(model=vertex.MODEL, messages=[dict(role='system', content=prompt),
                dict(role='user', content=json.dumps(row))], stream=False)
            target.write(json.dumps(dict(custom_id=row['request_id'], method='POST',
                url='/v1/chat/completions', body=body)) + '\n')
    manifest = dict(status='prepared_not_submitted', records=count, seed=seed, dimensions=23,
        distribution='Independent discrete uniform hundredths, inclusive [-10,10]; unique vectors',
        task='random_baseline_personality_description', model=vertex.MODEL, custom_output_token_cap=None,
        input_bytes=(out/'input.jsonl').stat().st_size,
        input_sha256=hashlib.sha256((out/'input.jsonl').read_bytes()).hexdigest())
    save(out / 'manifest.json', manifest)
    vertex.RUN = out
    vertex.job_config('smg-cluster', 'us-central1', 'gs://dz_batch_data/' + out.name.removeprefix('vertex-'))
    job = read(out / 'job-config.json')
    job['displayName'] = 'random-baseline-personalities-2000'
    save(out / 'job-config.json', job)
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out', type=Path, default=RUN)
    p.add_argument('--count', type=int, default=2000)
    p.add_argument('--seed', type=int, default=42)
    a = p.parse_args()
    print(json.dumps(prepare(a.out, a.count, a.seed), indent=2))
