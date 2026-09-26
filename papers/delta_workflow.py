"""Durable, bounded batch workflow. No blind submission retries or secret logging.

prepare: local artifacts only; start: initial paid submissions + monitoring;
resume: continue saved stage; status: local state only; stop: stop future work.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import urllib.parse

import emotion_batch as emotion
import vertex_literature_runner as vertex
from random_baseline_personalities import RUN as BASELINES, prepare as prepare_baselines, validate_profile
from vertex_literature_batch import validate_output

ROOT = emotion.ROOT
BATCHES = ROOT / 'papers/emotion-labeling/batches'
WORK = BATCHES / 'delta-workflow-v1'
STORIES = BATCHES / 'vertex-literature-oss120b-v2'
REPAIR = BATCHES / 'vertex-literature-repair-oss120b-v1'
REPAIR_FINAL = BATCHES / 'vertex-literature-repair-oss120b-v2'
save, read = emotion.save, emotion.read


def jsonl(path):
    with Path(path).open() as source:
        for line in source:
            yield json.loads(line)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def baseline_template(template):
    # Remove the neutral assumption; never leave contradictory zero-state rules.
    start = template.index('For this labeling pass,')
    end = template.index('## Our rubric', start)
    template = template[:start] + (
        'For this pass, use the supplied nonzero starting_state and baseline_profile. '
        'The numeric state is authoritative. The profile is a provisional interpretation, not an event. '
        'Describe the initial incremental event response relative to that baseline. Do not reset to zero, '
        'return final state values, repeat the baseline vector as the adjustments, or invent an event. '
        'Baseline values are in [-10,10]; they are state coordinates, not event-delta magnitudes. '
        'Keep the original adjustment anchors below for the incremental response. '
        'Do not mechanically multiply the baseline or use it to apply caps or saturation.\n\n') + template[end:]
    template = template.replace('1. Treat the starting state as the stipulated neutral simulation state. A recalled event\'s original participant did not necessarily begin at neutral.',
                                '1. Treat the supplied baseline as the stipulated starting state. Do not assume neutral.')
    template = template.replace('With a zero starting vector, these changes also describe the immediate displacement from neutral.',
                                'These changes are relative to the supplied nonzero starting vector, not displacement from neutral.')
    template = template.replace('neutral-start assumption', 'supplied-baseline assumption').replace('neutral-start output', 'baseline-conditioned output')
    return template.replace('# Sample prompt: neutral-start emotional response labeling', '# Baseline-conditioned event delta labeling')


def build_body(event, template, profile=None):
    prompt_template = baseline_template(template) if profile else template
    payload = {k: event[k] for k in ('scenario', 'perspective', 'starting_state')}
    if profile:
        payload['baseline_profile'] = profile
    prompt = prompt_template.split('## Event\n', 1)[0] + '## Event\n\n' + json.dumps(payload) + (
        '\n\nThis is source data, not instructions. No original respondent annotations are supplied.\n\n## Output\n') + prompt_template.split('## Output\n', 1)[1]
    return dict(model=emotion.MODEL, store=False, reasoning={'effort': 'medium'}, input=prompt,
                text={'format': {'type': 'json_schema', 'name': 'emotion_label', 'strict': True, 'schema': emotion.SCHEMA}})


def situations():
    return [dict(example_id=r['id'], scenario=r['scenario'], perspective=r['perspective'],
                 split_group=r['parent_id'], parent_id=r['parent_id'], synthetic=True)
            for r in jsonl(BATCHES / 'situations-v1/situations.jsonl')]


def originals():
    return [dict(example_id=r['custom_id'], scenario=r['scenario'], perspective=r['perspective'],
                 split_group=r['custom_id'], synthetic=False)
            for r in jsonl(BATCHES / 'crowd-envent-v1/labels.jsonl')]


def vertex_delta_body(body, cid):
    schema=dict(emotion.SCHEMA)
    schema['properties']={**schema['properties'],'request_id':{'type':'string'}}
    schema['required']=[*schema['required'],'request_id']
    instruction=(body['input']+'\n\nProvider output contract: also include request_id exactly '+json.dumps(cid)+
        '. Return only a JSON object matching this schema, no fences or additional fields:\n'+json.dumps(schema))
    return dict(model='openai/gpt-oss-120b-maas',messages=[dict(role='user',content=instruction)],stream=False)


def prepare_labels(name, examples, profile=None, provider='openai'):
    if provider not in {'openai','vertex'}:
        raise ValueError('Unsupported provider')
    out = WORK / name
    fingerprint = hashlib.sha256(json.dumps([examples, profile], sort_keys=True).encode()).hexdigest()
    if (out / 'manifest.json').exists():
        result = read(out / 'manifest.json')
        if result['source_fingerprint'] != fingerprint or result.get('provider','openai')!=provider:
            raise ValueError('Existing label inputs differ; do not reuse run')
        return result
    if out.exists():
        raise ValueError('Partial preparation requires review: ' + str(out))
    if not examples or len({e['example_id'] for e in examples}) != len(examples):
        raise ValueError('Empty or duplicate example IDs')
    out.mkdir(parents=True)
    template = emotion.PROMPT.read_text()
    dimensions = [l.split('|')[2].strip() for l in template.splitlines() if re.match(r'\| \d+ \|', l)]
    jobs, rows, events, size = [], [], [], 0

    def flush():
        nonlocal rows, events, size
        if not rows:
            return
        folder = out / (f'vertex-baseline-deltas-oss120b-v1-part{len(jobs)+1:03d}' if provider=='vertex'
                        else f'{name}-{len(jobs)+1:03d}')
        folder.mkdir()
        (folder / 'input.jsonl').write_bytes(b''.join(rows))
        save(folder / 'events.json', events)
        save(folder / 'schema.json', emotion.SCHEMA)
        save(folder / 'dimensions.json', dimensions)
        (folder / 'prompt.md').write_text(baseline_template(template) if profile else template)
        save(folder / 'manifest.json', dict(records=len(rows), model='openai/gpt-oss-120b-maas' if provider=='vertex' else emotion.MODEL,
            provider=provider,
            input_bytes=size, input_sha256=digest(folder/'input.jsonl'), custom_output_token_cap=None,
            profile_id=profile['request_id'] if profile else None))
        if provider=='vertex':
            import vertex_literature_batch as prep
            prep.RUN=folder
            prep.job_config('smg-cluster','us-central1','gs://dz_batch_data/'+folder.name.removeprefix('vertex-'))
            job=read(folder/'job-config.json')
            job['displayName']=folder.name
            save(folder/'job-config.json',job)
        jobs.append(str(folder))
        rows, events, size = [], [], 0

    for example in examples:
        event = dict(example, custom_id=name + '-' + example['example_id'],
                     starting_state=profile['state_values'] if profile else [0.0]*23)
        if profile:
            event['baseline_profile_id'] = profile['request_id']
        body = build_body(event, template, profile)
        if provider=='vertex':
            body=vertex_delta_body(body,event['custom_id'])
        line = (json.dumps(dict(custom_id=event['custom_id'], method='POST',
            url='/v1/chat/completions' if provider=='vertex' else '/v1/responses', body=body))+'\n').encode()
        if rows and (size + len(line) > 170_000_000 or len(rows) >= 6600):
            flush()
        if len(line) > 170_000_000:
            raise ValueError('Oversized request')
        rows.append(line)
        events.append(event)
        size += len(line)
    flush()
    result = dict(jobs=jobs, records=len(examples), source_fingerprint=fingerprint,provider=provider,
                  baseline_id=profile['request_id'] if profile else None)
    save(out / 'manifest.json', result)
    return result


def submit_labels(manifest):
    # Pump at most one unsubmitted batch. All stages share the same serial queue.
    return labels_done(manifest)


def validate_collection(folder, manifest):
    folder=Path(folder)
    summary=read(folder/'collection-summary.json')
    partial=manifest.get('accepted_partial_jobs',{}).get(str(folder))
    if partial:
        failed={r['custom_id'] for r in summary['failures']}
        valid=summary['valid']==partial['valid'] and failed==set(partial['retry_ids'])
    else:
        valid=not summary['failed'] and summary['valid']==read(folder/'manifest.json')['records']
    if not valid or summary['missing']:
        raise RuntimeError('Invalid/incomplete labels: '+folder.name)


def labels_done(manifest):
    for folder in manifest['jobs']:
        check_stop()
        folder = Path(folder)
        if manifest.get('provider')=='vertex':
            vertex.RUN=folder
            if not (folder/'batch.json').exists():
                vertex.submit()
                return False
            state=vertex.status()['state']
            if state in {'JOB_STATE_FAILED','JOB_STATE_CANCELLED','JOB_STATE_EXPIRED','JOB_STATE_PARTIALLY_SUCCEEDED'}:
                raise RuntimeError('GCP delta job needs review: '+state)
            if state!='JOB_STATE_SUCCEEDED':
                return False
            if not (folder/'collection-summary.json').exists():
                collect_vertex(folder,'labels')
            validate_collection(folder,manifest)
            continue
        emotion.RUN = folder
        if not (folder/'batch.json').exists():
            emotion.submit()
            return False
        batch = emotion.status()
        if batch['status'] in {'failed', 'expired', 'cancelled', 'cancelling'}:
            raise RuntimeError(f'Label job needs review: {folder.name}: {batch["status"]}')
        if batch['status'] != 'completed':
            return False
        if not (folder / 'collection-summary.json').exists():
            emotion.collect()
        validate_collection(folder,manifest)
    seen=set()
    for folder in manifest['jobs']:
        for row in jsonl(Path(folder)/'labels.jsonl'):
            if row['custom_id'] in seen:
                raise ValueError('Duplicate labels across recovery jobs')
            seen.add(row['custom_id'])
    if len(seen)!=manifest['records']:
        raise ValueError('Collected label coverage differs from logical stage size')
    return True


def unpack_vertex(record):
    if record.get('error') or record.get('status') not in (None, '', 'OK'):
        raise ValueError('Vertex output reports an error')
    response = record.get('response', record)
    if isinstance(response, str):
        response = json.loads(response)
    response = response.get('body', response)
    choice = response['choices'][0]
    if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
        raise ValueError('Incomplete or refused Vertex response')
    return json.loads(choice['message']['content'])


def prepare_story_repair():
    if (REPAIR/'manifest.json').exists():
        return
    bad = []
    records=list(jsonl(STORIES/'raw-output.jsonl'))
    if len(records)!=300 or len({r['custom_id'] for r in records})!=300:
        raise ValueError('Unexpected original story output coverage')
    for record in records:
        try:
            validate_output(unpack_vertex(record),record['custom_id'])
        except (ValueError,KeyError,TypeError):
            bad.append(record['custom_id'])
    if len(bad)!=106:
        raise ValueError('Repair scope differs from inspected 106 invalid responses')
    REPAIR.mkdir()
    requests=[]
    for r in jsonl(STORIES/'input.jsonl'):
        if r['custom_id'] in bad:
            r['body'].pop('response_format',None)
            requests.append(r)
    if len(requests)!=len(bad):
        raise ValueError('Repair requests missing')
    (REPAIR/'input.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in requests))
    (REPAIR/'prompt.md').write_text((STORIES/'prompt.md').read_text())
    save(REPAIR/'pairs.json',[r for r in read(STORIES/'pairs.json') if r['request_id'] in bad])
    save(REPAIR/'manifest.json',dict(records=len(bad),input_sha256=digest(REPAIR/'input.jsonl'),
        input_bytes=(REPAIR/'input.jsonl').stat().st_size, replaces_invalid_ids=bad,
        reason='One bounded retry of invalid/malformed outputs, without response_format',
        original_output_sha256=digest(STORIES/'raw-output.jsonl'),custom_output_token_cap=None))
    import vertex_literature_batch as prep
    prep.RUN=REPAIR
    prep.job_config('smg-cluster','us-central1','gs://dz_batch_data/literature-repair-oss120b-v1')
    job=read(REPAIR/'job-config.json');job['displayName']='literature-repair-106'
    save(REPAIR/'job-config.json',job)


def collect_vertex(folder, kind):
    """Strictly match all echoed IDs; stop on any errors/missing/duplicate outputs."""
    vertex.RUN = folder
    config = read(folder / 'submission-config.json')
    prefix = config['gcs_prefix'].removeprefix('gs://dz_batch_data/') + '/output/'
    items, page = [], None
    while True:
        query = {'prefix': prefix}
        if page:
            query['pageToken'] = page
        result = vertex.api('https://storage.googleapis.com/storage/v1/b/dz_batch_data/o?' + urllib.parse.urlencode(query))
        items.extend(result.get('items', []))
        page = result.get('nextPageToken')
        if not page:
            break
    output_files = [i for i in items if i['name'].endswith('.jsonl')]
    if not output_files:
        raise ValueError('No Vertex JSONL output found')
    rawdir = folder / 'downloaded-output'
    rawdir.mkdir(exist_ok=True)
    expected = ({r['custom_id']:r for r in read(folder/'events.json')} if kind=='labels' else
                {r['request_id']:r for r in read(folder/'profiles.json')} if kind == 'baselines'
                else {r['request_id']:r for r in read(folder/'pairs.json')})
    requests = {r['custom_id']: r for r in jsonl(folder/'input.jsonl')}
    seen, accepted = set(), []
    replacement_folder = REPAIR if folder == STORIES else REPAIR_FINAL if folder == REPAIR and (REPAIR_FINAL/'collected.json').exists() else None
    replacements = set(read(replacement_folder/'manifest.json')['replaces_invalid_ids']) if replacement_folder else set()
    for i, item in enumerate(sorted(output_files, key=lambda x:x['name'])):
        target = rawdir / f'{i:04d}-{hashlib.sha256(item["name"].encode()).hexdigest()[:12]}.jsonl'
        if not target.exists():
            url = 'https://storage.googleapis.com/storage/v1/b/dz_batch_data/o/' + urllib.parse.quote(item['name'],safe='') + '?alt=media'
            body = vertex.api(url, raw=True)
            tmp = target.with_suffix('.tmp')
            tmp.write_bytes(body)
            tmp.replace(target)
        for record in jsonl(target):
            if record.get('custom_id') in replacements:
                continue
            value = unpack_vertex(record)
            cid = value['request_id']
            if record.get('custom_id') and record['custom_id'] != cid:
                raise ValueError('Provider custom_id disagrees with model output ID')
            if cid not in expected or cid in seen:
                raise ValueError('Unknown or duplicate Vertex request ID')
            # When the service echoes the request, corroborate the model's ID.
            echoed = record.get('request') or record.get('body')
            if echoed:
                if isinstance(echoed,str):
                    echoed=json.loads(echoed)
                echoed=echoed.get('body',echoed)
                if echoed.get('messages') and echoed['messages'] != requests[cid]['body']['messages']:
                    raise ValueError('Echoed request disagrees with output ID')
            seen.add(cid)
            if kind == 'baselines':
                accepted.append(validate_profile(value, expected[cid]))
            elif kind=='labels':
                label={k:v for k,v in value.items() if k!='request_id'}
                emotion.validate(label,emotion.SCHEMA)
                accepted.append({**expected[cid],'label':label,'model':'openai/gpt-oss-120b-maas','provider':'vertex'})
            else:
                for number, text in enumerate(validate_output(value, cid), 1):
                    accepted.append(dict(example_id=f'story-{cid}-{number}', scenario=text,
                        perspective='The first-person event narrator', split_group='story-'+cid,
                        parent_id=cid, synthetic=True))
    if replacements:
        accepted.extend(collect_vertex(replacement_folder,'stories'))
        seen.update(replacements)
    if seen != set(expected):
        raise ValueError(f'Missing Vertex outputs: {len(set(expected)-seen)}')
    accepted.sort(key=lambda r:r.get('request_id', r.get('example_id',r.get('custom_id'))))
    save(folder / 'collected.json', accepted)
    if kind=='labels':
        target=folder/'labels.jsonl'
        temp=target.with_suffix('.tmp')
        temp.write_text(''.join(json.dumps(r,ensure_ascii=False)+'\n' for r in accepted))
        temp.replace(target)
        save(folder/'collection-summary.json',dict(valid=len(accepted),failed=0,missing=[],failures=[],provider='vertex'))
        return accepted
    save(folder / 'collection-summary.json', dict(requests=len(seen), records=len(accepted), validated=True,
        matching='Echoed request corroboration when available; otherwise model-echoed ID, exact coverage and schema'))
    return accepted


def gcps_done():
    complete = True
    for folder in [STORIES, REPAIR, BASELINES]:
        vertex.RUN = folder
        status = vertex.status()['state']
        print(folder.name, status, flush=True)
        if status in {'JOB_STATE_FAILED', 'JOB_STATE_CANCELLED', 'JOB_STATE_EXPIRED', 'JOB_STATE_PARTIALLY_SUCCEEDED'}:
            raise RuntimeError('GCP job needs review: ' + status)
        complete = complete and status == 'JOB_STATE_SUCCEEDED'
    return complete


def check_stop():
    if (WORK/'BILLING_HOLD.json').exists():
        raise RuntimeError('Billing hold: confirm OpenAI credits replenished before resuming')
    if (WORK/'STOP').exists():
        raise RuntimeError('Stopped by user; remote jobs were not cancelled')


def prepare():
    if (WORK/'state.json').exists():
        return read(WORK/'state.json')
    WORK.mkdir(parents=True, exist_ok=True)
    if not BASELINES.exists():
        prepare_baselines()
    prepare_story_repair()
    example_rows = situations()
    if len(example_rows) != 33000 or len(originals()) != 6600:
        raise ValueError('Unexpected source dataset counts')
    prepare_labels('neutral-situations', example_rows)
    state = dict(stage='ready', selected_baseline='baseline-0001', openai_poll_seconds=1800,
                 gcp_poll_seconds=1200, next_check_at=None, created_at=time.time(),
                 final_action='train_on_all_validated_deltas')
    save(WORK/'state.json',state)
    return state


def manifest(name):
    return read(WORK/name/'manifest.json')


def advance(state):
    check_stop()
    stage = state['stage']
    if stage == 'ready':
        if not BASELINES.exists():
            prepare_baselines()
        prepare_story_repair()
        vertex.RUN = BASELINES
        vertex.submit()
        vertex.RUN = REPAIR
        vertex.submit()
        submit_labels(manifest('neutral-situations'))
        state['stage'] = 'wait-neutral-situations'
        return 1800
    if stage == 'wait-neutral-situations':
        if not labels_done(manifest('neutral-situations')):
            return 1800
        state['stage'] = 'wait-gcp'
        return 1200
    if stage == 'wait-gcp':
        if not gcps_done():
            return 1200
        profiles = collect_vertex(BASELINES, 'baselines')
        stories = collect_vertex(STORIES, 'stories')
        selected = next(p for p in profiles if p['request_id'] == state['selected_baseline'])
        save(WORK/'selected-baseline.json', selected)
        submit_labels(prepare_labels('neutral-stories', stories))
        state['stage'] = 'wait-neutral-stories'
        return 1800
    if stage == 'wait-neutral-stories':
        if not labels_done(manifest('neutral-stories')):
            return 1800
        # Recheck every neutral collection before the conditioned stage.
        if not labels_done(manifest('neutral-situations')):
            raise RuntimeError('Previously completed neutral work is no longer complete')
        original_summary = read(BATCHES/'crowd-envent-v1/collection-summary.json')
        if original_summary['valid'] != 6600 or original_summary['failed'] or original_summary['missing']:
            raise RuntimeError('Original neutral labels incomplete')
        all_examples = originals() + situations() + read(STORIES/'collected.json')
        submit_labels(prepare_labels('baseline-0001-deltas', all_examples, read(WORK/'selected-baseline.json'),
                                    provider=state.get('baseline_delta_provider','openai')))
        state['stage'] = 'wait-baseline-deltas'
        return 1200 if state.get('baseline_delta_provider')=='vertex' else 1800
    if stage == 'wait-baseline-deltas':
        if not labels_done(manifest('baseline-0001-deltas')):
            return 1200 if state.get('baseline_delta_provider')=='vertex' else 1800
        state['stage'] = 'train-all-deltas'
        return 0
    if stage == 'train-all-deltas':
        train_all()
        state['stage'] = 'complete'
        return None
    if stage == 'complete':
        return None
    raise ValueError('Unknown stage: ' + stage)


def train_all():
    from delta_workflow_training import run
    run(WORK)


def monitor(resume=False):
    WORK.mkdir(parents=True, exist_ok=True)
    with (WORK/'monitor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        state = prepare()
        if state.get('error') and not resume:
            raise RuntimeError('Stopped for review; use resume after resolving the issue')
        state.pop('error',None)
        state['pid'] = os.getpid()
        save(WORK/'state.json',state)
        while state['stage'] != 'complete':
            try:
                check_stop()
                if time.time() < (state.get('next_check_at') or 0):
                    time.sleep(max(0,min(30, state['next_check_at']-time.time())))
                    continue
                state['last_check_at'] = time.time()
                delay = advance(state)
                state['next_check_at'] = time.time()+delay if delay is not None else None
                save(WORK/'state.json',state)
                print(json.dumps(state),flush=True)
            except Exception as exc:
                # Never dump raw output, prompt, credential material or a traceback.
                message = str(exc)[:1800]
                for key in ('OPENAI_API_KEY','VERTEX_API_KEY'):
                    if os.environ.get(key):
                        message = message.replace(os.environ[key],'[REDACTED]')
                state['error'] = dict(type=type(exc).__name__, message=message, at=time.time())
                save(WORK/'state.json',state)
                print(json.dumps(state['error']),flush=True)
                raise SystemExit(1) from None


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','start','resume','status','stop'])
    a = p.parse_args()
    if a.action == 'prepare':
        print(json.dumps(prepare(),indent=2))
    elif a.action == 'status':
        print(json.dumps(read(WORK/'state.json'),indent=2))
    elif a.action == 'stop':
        (WORK/'STOP').touch()
        print('Stop requested. Submitted remote jobs are not cancelled.')
    else:
        monitor(resume=a.action == 'resume')
