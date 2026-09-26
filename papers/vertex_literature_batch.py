"""Prepare 300 exclusive literature pairs for Vertex gpt-oss-120b batch inference.

Local preparation only. job-config needs explicit project, region and GCS prefix.
Does not read API keys, submit requests, upload files, or incur inference charges.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'papers/emotion-labeling/datasets/public-domain-works/passages-by-book'
RUN = ROOT / 'papers/emotion-labeling/batches/vertex-literature-oss120b-v1'
PROMPT = ROOT / 'docs/literature-pair-situation-prompt.md'
MODEL = 'openai/gpt-oss-120b-maas'


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def pair_passages(rows, seed):
    if len(rows) != 600 or len({r['id'] for r in rows}) != 600:
        raise ValueError('Exactly 600 unique passage IDs required')
    if len({r['document_path'] for r in rows}) != 600:
        raise ValueError('Duplicate passage paths')
    shuffled = sorted(rows, key=lambda r: r['id'])
    random.Random(seed).shuffle(shuffled)
    return [shuffled[i:i + 2] for i in range(0, 600, 2)]


def prepare(seed):
    if RUN.exists():
        raise ValueError('Run folder exists; preserve frozen pairing and inputs')
    rows = [json.loads(line) for line in (SOURCE / 'passages.jsonl').read_text().splitlines()]
    pairs = pair_passages(rows, seed)
    prompt = PROMPT.read_text()
    requests, provenance = [], []
    for number, pair in enumerate(pairs, 1):
        cid = f'literature-pair-{number:04d}'
        texts = []
        parents = []
        for item in pair:
            path = (SOURCE / item['document_path']).resolve()
            if not path.is_relative_to(SOURCE.resolve()):
                raise ValueError('Source passage path leaves source directory')
            raw = path.read_bytes()
            text = raw.decode('utf-8').strip()
            if text != item['text'].strip():
                raise ValueError(f'Passage changed: {item["id"]}')
            texts.append(text)
            parents.append({**item, 'passage_file_sha256': hashlib.sha256(raw).hexdigest()})
        body = dict(model=MODEL,
                    messages=[dict(role='system', content=prompt),
                              dict(role='user', content=json.dumps(dict(request_id=cid, passage_a=texts[0], passage_b=texts[1]), ensure_ascii=False))],
                    response_format={'type': 'json_object'}, stream=False)
        requests.append(dict(custom_id=cid, method='POST', url='/v1/chat/completions', body=body))
        provenance.append(dict(request_id=cid, parents=parents, starting_state=[0.0] * 23,
                               requested_situations=10))
    RUN.mkdir(parents=True)
    with (RUN / 'input.jsonl').open('w') as output:
        for request in requests:
            output.write(json.dumps(request, ensure_ascii=False) + '\n')
    save(RUN / 'pairs.json', provenance)
    (RUN / 'prompt.md').write_text(prompt)
    (RUN / 'example-request.json').write_text(json.dumps(requests[0], indent=2, ensure_ascii=False) + '\n')
    manifest = dict(status='prepared_not_submitted', model=MODEL, seed=seed, calls=300,
                    source_directory=str(SOURCE.resolve()),
                    passages_per_call=2, distinct_passages=600, examples_per_call=10, target_examples=3000,
                    pairing='Seeded random shuffle without replacement; same-book pairs permitted',
                    custom_output_token_cap=None, input_bytes=(RUN / 'input.jsonl').stat().st_size,
                    input_sha256=hashlib.sha256((RUN / 'input.jsonl').read_bytes()).hexdigest(),
                    source_manifest_sha256=hashlib.sha256((SOURCE / 'passages.jsonl').read_bytes()).hexdigest(),
                    prepared_at=datetime.now(timezone.utc).isoformat())
    save(RUN / 'manifest.json', manifest)
    return manifest


def job_config(project, region, prefix):
    if not re.fullmatch(r'[a-z][a-z0-9-]{4,61}[a-z0-9]', project):
        raise ValueError('Supply a valid Google Cloud project ID')
    if region == 'global' or not re.fullmatch(r'[a-z]+-[a-z]+[0-9]', region):
        raise ValueError('Batch requires a supported regional location, not global')
    if not re.fullmatch(r'gs://[a-z0-9][a-z0-9._-]+/[A-Za-z0-9/_-]+/?', prefix):
        raise ValueError('Supply a gs://bucket/dedicated-prefix location')
    if not (RUN / 'manifest.json').exists():
        raise ValueError('Prepare inputs first')
    path = RUN / 'job-config.json'
    if path.exists():
        raise ValueError('Job config already exists; review before replacing')
    prefix = prefix.rstrip('/')
    body = dict(displayName='literature-oss120b-300-pairs', model='publishers/openai/models/gpt-oss-120b-maas',
                inputConfig=dict(instancesFormat='jsonl', gcsSource=dict(uris=[prefix + '/input.jsonl'])),
                outputConfig=dict(predictionsFormat='jsonl', gcsDestination=dict(outputUriPrefix=prefix + '/output/')))
    save(path, body)
    endpoint = f'https://{region}-aiplatform.googleapis.com/v1/projects/{project}/locations/{region}/batchPredictionJobs'
    save(RUN / 'submission-config.json', dict(endpoint=endpoint, project=project, region=region,
                                             gcs_prefix=prefix, state='not_uploaded_not_submitted'))
    return dict(endpoint=endpoint, body=body, submitted=False)


def validate_output(value, expected_id):
    if not isinstance(value, dict) or set(value) != {'request_id', 'situations'} or value['request_id'] != expected_id:
        raise ValueError('Unexpected output schema or request ID')
    items = value['situations']
    if not isinstance(items, list) or len(items) != 10 or any(not isinstance(s, str) or not s.strip() for s in items):
        raise ValueError('Expected ten nonempty situation strings')
    normalized = [re.sub(r'\W+', ' ', s.casefold()).strip() for s in items]
    if not all(normalized) or len(set(normalized)) != 10:
        raise ValueError('Empty or duplicate situations')
    return items


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, default=RUN, help='New run directory; existing runs are preserved')
    parser.add_argument('--source', type=Path, default=SOURCE, help='Passage directory containing passages.jsonl')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--seed', type=int, default=42)
    p = sub.add_parser('job-config')
    p.add_argument('--project', required=True)
    p.add_argument('--region', required=True)
    p.add_argument('--gcs-prefix', required=True)
    args = parser.parse_args()
    RUN, SOURCE = args.run.resolve(), args.source.resolve()
    try:
        result = prepare(args.seed) if args.command == 'prepare' else job_config(args.project, args.region, args.gcs_prefix)
        print(json.dumps(result, indent=2))
    except ValueError as exc:
        parser.error(str(exc))
