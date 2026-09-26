"""Prepare only the verified aborted literature-pair-0003 request. No network."""
import json
import hashlib
from pathlib import Path
import vertex_literature_batch as prep
from emotion_batch import ROOT, read, save

BASE=ROOT/'papers/emotion-labeling/batches'
SOURCE=BASE/'vertex-literature-repair-oss120b-v1'
OUT=BASE/'vertex-literature-repair-oss120b-v2'
CID='literature-pair-0003'


def main():
    if OUT.exists():
        raise ValueError('Retry already prepared; do not duplicate it')
    evidence=[]
    for path in (SOURCE/'downloaded-output').glob('*.jsonl'):
        for line in path.open():
            row=json.loads(line)
            if row.get('custom_id')==CID:
                evidence.append(row)
    if len(evidence)!=1:
        raise ValueError('Expected one failed response')
    choice=evidence[0]['response']['choices'][0]
    if choice['finish_reason']!='abort' or choice['message'].get('content'):
        raise ValueError('Retry target does not match the inspected abort')
    requests=[json.loads(line) for line in (SOURCE/'input.jsonl').open()]
    requests=[r for r in requests if r['custom_id']==CID]
    pairs=[r for r in read(SOURCE/'pairs.json') if r['request_id']==CID]
    if len(requests)!=1 or len(pairs)!=1:
        raise ValueError('Missing or duplicate source request')
    OUT.mkdir()
    (OUT/'input.jsonl').write_text(json.dumps(requests[0])+'\n')
    (OUT/'prompt.md').write_text((SOURCE/'prompt.md').read_text())
    save(OUT/'pairs.json',pairs)
    save(OUT/'aborted-response.json',evidence[0])
    save(OUT/'manifest.json',dict(records=1,target_examples=10,replaces_invalid_ids=[CID],
        retry_of=str(SOURCE),reason='Explicit user retry of empty finish_reason=abort response',
        input_bytes=(OUT/'input.jsonl').stat().st_size,
        input_sha256=hashlib.sha256((OUT/'input.jsonl').read_bytes()).hexdigest(),custom_output_token_cap=None))
    prep.RUN=OUT
    prep.job_config('smg-cluster','us-central1','gs://dz_batch_data/literature-repair-oss120b-v2')
    job=read(OUT/'job-config.json');job['displayName']='literature-single-abort-retry'
    save(OUT/'job-config.json',job)
    print('Prepared exactly one retry for literature-pair-0003; no other requests included.')


if __name__=='__main__':main()
