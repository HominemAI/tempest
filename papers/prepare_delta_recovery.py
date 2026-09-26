"""Prepare one inspected recovery attempt; no API requests or submissions."""
import shutil
from pathlib import Path
import json
from delta_workflow import WORK,read,save,jsonl,digest


def copy_retry(source,target,ids=None):
    if target.exists():
        raise ValueError('Recovery directory already exists; inspect rather than overwrite')
    target.mkdir()
    rows=[r for r in jsonl(source/'input.jsonl') if ids is None or r['custom_id'] in ids]
    events=[r for r in read(source/'events.json') if ids is None or r['custom_id'] in ids]
    if len(rows)!=len(events) or (ids is not None and {r['custom_id'] for r in rows}!=ids):
        raise ValueError('Retry ID mismatch')
    (target/'input.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    save(target/'events.json',events)
    for name in ['schema.json','dimensions.json','prompt.md']:
        shutil.copy2(source/name,target/name)
    meta=read(source/'manifest.json')
    meta.update(records=len(rows),input_bytes=(target/'input.jsonl').stat().st_size,
                input_sha256=digest(target/'input.jsonl'),retry_of=str(source),attempt=1)
    save(target/'manifest.json',meta)
    # Reuse an already-uploaded file only when every byte matches.
    if (source/'upload.json').exists() and digest(source/'input.jsonl')==meta['input_sha256']:
        shutil.copy2(source/'upload.json',target/'upload.json')
    return str(target)


def main():
    root=WORK/'neutral-situations'
    plan=read(root/'manifest.json')
    if plan.get('recovery_prepared'):
        print('Recovery already prepared; no changes')
        return
    first=Path(plan['jobs'][0])
    errors=list(jsonl(first/'errors.jsonl'))
    ids={r['custom_id'] for r in errors}
    if len(errors)!=58 or len(ids)!=58 or any(
        (r.get('response') or {}).get('body',{}).get('error',{}).get('code')!='credit_balance_exhausted' for r in errors):
        raise ValueError('Failure scope differs from inspected billing failures')
    summary=read(first/'collection-summary.json')
    if summary['valid']!=6542 or summary['missing'] or {r['custom_id'] for r in summary['failures']}!=ids:
        raise ValueError('Unexpected collection failures beyond the 58 billing errors')
    second=Path(plan['jobs'][1]); s=read(second/'collection-summary.json')
    if s['valid']!=6600 or s['failed'] or s['missing']:
        raise ValueError('Batch 2 not completely validated')
    rejected=[Path(p) for p in plan['jobs'][2:]]
    for folder in rejected:
        b=read(folder/'batch.json')
        codes={e['code'] for e in (b.get('errors') or {}).get('data',[])}
        if b['status']!='failed' or codes!={'token_limit_exceeded'} or any(b['request_counts'].values()):
            raise ValueError('Refuse to replay a job that may have processed requests')
    save(WORK/'BILLING_HOLD.json',dict(reason='58 credit_balance_exhausted responses',
        release='Requires user confirmation that credits are replenished'))
    save(root/'manifest-before-recovery.json',plan)
    retries=[copy_retry(first,root/'neutral-situations-001-billing-retry',ids)]
    retries += [copy_retry(p,root/(p.name+'-queue-retry')) for p in rejected]
    plan['jobs']=plan['jobs'][:2]+retries
    plan['accepted_partial_jobs']={str(first):dict(valid=6542,retry_ids=sorted(ids))}
    plan['recovery_prepared']=True
    plan['max_active_openai_batches']=1
    save(root/'manifest.json',plan)
    state=read(WORK/'state.json')
    state.update(next_check_at=None,max_active_openai_batches=1,
        error=dict(type='BillingHold',message='58 billing failures; awaiting credit replenishment'))
    save(WORK/'state.json',state)
    print(json.dumps(dict(preserved=13142,retry_requests=19858,retry_batches=len(retries),submitted=False),indent=2))


if __name__=='__main__':main()
