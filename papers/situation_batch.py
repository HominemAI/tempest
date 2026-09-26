"""Generate five unlabeled situations per crowd-enVENT source; extract Markdown docs.

Commands: prepare, submit, status, collect. No automatic retries or token caps.
Uses OPENAI_API_KEY through the existing redacting transport, never prints it.
"""
import argparse
import csv
from datetime import datetime, timezone
from difflib import SequenceMatcher
import hashlib
import json
import re
import uuid

from emotion_batch import BASE, CORPUS, MODEL, ROOT, api, read, save

RUN = BASE / 'batches/situations-v1'
PROMPT = ROOT / 'docs/emotional-event-generation-prompt.md'
SCHEMA = {'type': 'object', 'additionalProperties': False,
          'properties': {'situations': {'type': 'array', 'minItems': 5, 'maxItems': 5,
                                        'items': {'type': 'string'}}}, 'required': ['situations']}


def normalized(text):
    return re.sub(r'\W+', ' ', text.casefold()).strip()


def validate(value):
    if not isinstance(value, dict) or set(value) != {'situations'}:
        raise ValueError('Expected only situations')
    items = value['situations']
    if not isinstance(items, list) or len(items) != 5:
        raise ValueError('Expected exactly five situations')
    if any(not isinstance(s, str) or not normalized(s) for s in items):
        raise ValueError('Situations must be nonempty strings')
    if len({normalized(s) for s in items}) != 5:
        raise ValueError('Duplicate situations within response')
    return [s.strip() for s in items]


def build_body(prompt, scenario):
    return {'model': MODEL, 'store': False, 'reasoning': {'effort': 'medium'},
            'input': [{'role': 'developer', 'content': prompt},
                      {'role': 'user', 'content': json.dumps({'source_scenario': scenario}, ensure_ascii=False)}],
            'text': {'format': {'type': 'json_schema', 'name': 'new_situations',
                                'strict': True, 'schema': SCHEMA}}}


def prepare():
    if (RUN / 'manifest.json').exists():
        return read(RUN / 'manifest.json')
    RUN.mkdir(parents=True, exist_ok=True)
    template = PROMPT.read_text()
    prompt = '## Task\n' + template.split('## Task\n', 1)[1].split('## Dataset handling', 1)[0]
    events, ids = [], set()
    with CORPUS.open(newline='') as source, (RUN / 'input.jsonl').open('w') as output:
        for row in csv.DictReader(source, delimiter='\t'):
            sid = row['text_id']
            if not re.fullmatch(r'\d+', sid) or sid in ids:
                raise ValueError('Invalid or duplicate source ID')
            ids.add(sid)
            cid = 'situations-' + sid
            scenario = row['generated_text']
            record = {'custom_id': cid, 'method': 'POST', 'url': '/v1/responses',
                      'body': build_body(prompt, scenario)}
            output.write(json.dumps(record, ensure_ascii=False) + '\n')
            events.append({'custom_id': cid, 'parent_id': 'crowd-envent-' + sid,
                           'source_record_id': sid, 'scenario': scenario})
    if len(events) != 6600:
        raise ValueError('Expected 6,600 source events; review corpus before submission')
    save(RUN / 'events.json', events)
    save(RUN / 'schema.json', SCHEMA)
    (RUN / 'prompt.md').write_text(prompt)
    size = (RUN / 'input.jsonl').stat().st_size
    if size >= 200_000_000:
        raise ValueError('Input too large')
    manifest = dict(records=len(events), situations_per_record=5, target_situations=len(events) * 5,
                    model=MODEL, reasoning_effort='medium', custom_output_token_cap=None,
                    input_bytes=size, input_sha256=hashlib.sha256((RUN / 'input.jsonl').read_bytes()).hexdigest(),
                    prepared_at=datetime.now(timezone.utc).isoformat(), generation_only=True)
    save(RUN / 'manifest.json', manifest)
    return manifest


def submit():
    manifest = prepare()
    if (RUN / 'batch.json').exists():
        return read(RUN / 'batch.json')
    if (RUN / 'submission-started.json').exists():
        raise RuntimeError('Possible prior submission: reconcile before resubmitting')
    data = (RUN / 'input.jsonl').read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest['input_sha256']:
        raise ValueError('Batch input changed')
    if not (RUN / 'upload.json').exists():
        boundary = 'situations-' + uuid.uuid4().hex
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="purpose"\r\n\r\nbatch\r\n'
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="situations.jsonl"\r\n'
                'Content-Type: application/jsonl\r\n\r\n').encode() + data + f'\r\n--{boundary}--\r\n'.encode()
        print('Uploading situation-generation requests...', flush=True)
        save(RUN / 'upload.json', api('files', body, 'multipart/form-data; boundary=' + boundary))
    payload = dict(input_file_id=read(RUN / 'upload.json')['id'], endpoint='/v1/responses',
                   completion_window='24h', metadata={'run': 'situations-v1', 'records': str(manifest['records'])})
    save(RUN / 'submission-started.json', payload)
    result = api('batches', json.dumps(payload).encode())
    save(RUN / 'batch.json', result)
    return result


def status():
    result = api('batches/' + read(RUN / 'batch.json')['id'])
    save(RUN / 'batch.json', result)
    return result


def extract(run):
    events = {r['custom_id']: r for r in read(run / 'events.json')}
    source_texts = {normalized(r['scenario']) for r in events.values()}
    seen, accepted, failures, rejected, flags = set(), [], [], [], []
    generated_texts = set()
    documents = run / 'documents'
    documents.mkdir(exist_ok=True)
    usage = {'input_tokens': 0, 'output_tokens': 0}
    for filename in ('output.jsonl', 'errors.jsonl'):
        path = run / filename
        if not path.exists():
            continue
        for line in path.read_text().splitlines():
            record = json.loads(line)
            cid = record['custom_id']
            if cid not in events or cid in seen:
                raise ValueError('Unknown or duplicate response ID')
            seen.add(cid)
            response = record.get('response') or {}
            body = response.get('body') or {}
            for key in usage:
                usage[key] += (body.get('usage') or {}).get(key, 0)
            try:
                if record.get('error') or response.get('status_code') != 200 or body.get('status') != 'completed':
                    raise ValueError('Failed or incomplete response')
                parts = [c for item in body.get('output', []) if item.get('type') == 'message' for c in item.get('content', [])]
                if any(c.get('type') == 'refusal' for c in parts):
                    raise ValueError('Refusal')
                texts = validate(json.loads(''.join(c['text'] for c in parts if c.get('type') == 'output_text')))
            except (ValueError, KeyError, TypeError) as exc:
                failures.append({'custom_id': cid, 'reason': str(exc)})
                continue
            event = events[cid]
            comparisons = [normalized(event['scenario'])]
            for i, scenario in enumerate(texts, 1):
                sid = cid + '-' + str(i)
                norm = normalized(scenario)
                if norm in source_texts or norm in generated_texts:
                    rejected.append({'id': sid, 'reason': 'Exact normalized duplicate', 'scenario': scenario})
                    continue
                if any(SequenceMatcher(None, norm, other, autojunk=False).ratio() >= .85 for other in comparisons):
                    flags.append({'id': sid, 'reason': 'Possible near-duplicate of parent or sibling'})
                comparisons.append(norm)
                generated_texts.add(norm)
                target = documents / (sid + '.md')
                content = f'# {sid}\n\n{scenario}\n'
                if target.exists() and target.read_text() != content:
                    raise ValueError(f'Refusing to overwrite edited document: {target.name}')
                target.write_text(content)
                accepted.append(dict(id=sid, parent_id=event['parent_id'], child_index=i,
                                     scenario=scenario, document_path=target.relative_to(run).as_posix(),
                                     synthetic=True, starting_state=[0.0] * 23,
                                     perspective='The first-person event narrator', model=body.get('model'),
                                     batch_custom_id=cid))
    with (run / 'situations.jsonl').open('w') as output:
        for item in accepted:
            output.write(json.dumps(item, ensure_ascii=False) + '\n')
    save(run / 'review-flags.json', flags)
    save(run / 'rejected-situations.json', rejected)
    summary = dict(accepted_situations=len(accepted), failed_requests=failures,
                   missing_requests=sorted(set(events) - seen), rejected_duplicates=len(rejected),
                   near_duplicate_flags=len(flags), usage=usage, automatically_retried=False,
                   review_required='Review emotional leakage and cross-parent near-duplicates before labeling/training')
    save(run / 'collection-summary.json', summary)
    return summary


def collect():
    batch = status()
    if batch['status'] not in {'completed', 'failed', 'expired', 'cancelled'}:
        return {'status': batch['status'], 'message': 'Not terminal; no results collected'}
    for key, name in [('output_file_id', 'output.jsonl'), ('error_file_id', 'errors.jsonl')]:
        if batch.get(key) and not (RUN / name).exists():
            (RUN / name).write_bytes(api('files/' + batch[key] + '/content', raw=True))
    return extract(RUN)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['prepare', 'submit', 'status', 'collect'])
    args = parser.parse_args()
    try:
        print(json.dumps(globals()[args.action](), indent=2), flush=True)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from None
