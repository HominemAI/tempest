"""Final local CPU retraining stage, invoked only after the batch dependency gates."""
import json
import hashlib
import os
from pathlib import Path
import subprocess


def run(work):
    from delta_workflow import ROOT, BATCHES, jsonl, read, save, digest, validate_collection
    output = work/'training'
    output.mkdir(exist_ok=True)
    sources = [BATCHES/'crowd-envent-v1/labels.jsonl']
    for name in ['neutral-situations','neutral-stories','baseline-0001-deltas']:
        manifest=read(work/name/'manifest.json')
        for folder in manifest['jobs']:
            folder = Path(folder)
            validate_collection(folder,manifest)
            sources.append(folder/'labels.jsonl')
    merged = output/'all-deltas.jsonl'
    from personality_app import training_edits
    edits = training_edits()
    edits_hash = hashlib.sha256(json.dumps(edits,sort_keys=True).encode()).hexdigest()
    if not merged.exists():
        seen = set()
        with merged.with_suffix('.tmp').open('w') as target:
            for source in sources:
                for row in jsonl(source):
                    row = edits.get(row['custom_id'], row)
                    if row['custom_id'] in seen:
                        raise ValueError('Duplicate training ID')
                    seen.add(row['custom_id'])
                    row.setdefault('split_group',row['custom_id'])
                    target.write(json.dumps(row,ensure_ascii=False)+'\n')
        merged.with_suffix('.tmp').replace(merged)
        save(output/'sources.json',dict(sources=[dict(path=str(p),sha256=digest(p)) for p in sources],
                                       records=len(seen), merged_sha256=digest(merged), personality_edits_sha256=edits_hash))
    provenance = read(output/'sources.json')
    if provenance.get('personality_edits_sha256', edits_hash if not edits else None) != edits_hash:
        raise ValueError('Editor changes postdate this training snapshot. Preserve it and create a new training run.')
    if digest(merged) != provenance['merged_sha256'] or any(digest(p['path'])!=p['sha256'] for p in provenance['sources']):
        raise ValueError('Training sources changed')
    python = ROOT/'papers/emotion_training/.venv/bin/python'
    pipeline = ROOT/'papers/emotion_training/pipeline.py'
    data, model = output/'features', output/'model'
    env = dict(os.environ, HF_HUB_OFFLINE='1',
               HF_HUB_CACHE=str(ROOT/'papers/emotion_training/artifacts/encoder-cache'),
               OMP_NUM_THREADS='2', MKL_NUM_THREADS='2')
    if not (data/'metadata.json').exists():
        subprocess.run([str(python),str(pipeline),'prepare','--labels',str(merged),'--out',str(data),
            '--revision','1110a243fdf4706b3f48f1d95db1a4f5529b4d41',
            '--exclude-overlength','--allow-nonzero'],env=env,check=True)
    if not (model/'metrics.json').exists():
        subprocess.run([str(python),str(pipeline),'train','--data',str(data),'--out',str(model)],env=env,check=True)
    subprocess.run([str(python),str(pipeline),'predict','--model',str(model),
        '--event','My friend came back to help me.'],env=env,check=True)
    profile = read(work/'selected-baseline.json')
    subprocess.run([str(python),str(pipeline),'predict','--model',str(model),
        '--event','My friend came back to help me.','--state',json.dumps(profile['state_values'])],env=env,check=True)
    save(output/'completion.json',dict(model=str(model),metadata=str(data/'metadata.json'),
        metrics=read(model/'metrics.json'), records=provenance['records'],
        limitation='Only neutral and baseline-0001 represented. No claim of generalization across other states.'))
