"""Defer the user-selected 58-request retry locally; never cancel remote work."""
import fcntl
import time
from pathlib import Path
from delta_workflow import WORK, read, save


def main():
    with (WORK/'monitor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        path=WORK/'neutral-situations/manifest.json'
        manifest=read(path)
        state=read(WORK/'state.json')
        if state['stage']!='wait-neutral-situations':
            raise ValueError('Unexpected workflow stage')
        retry=WORK/'neutral-situations/neutral-situations-001-billing-retry'
        if str(retry) not in manifest['jobs']:
            raise ValueError('Retry already deferred or unexpected queue')
        events=read(retry/'events.json')
        if len(events)!=58 or manifest['records']!=33000:
            raise ValueError('Unexpected deferral scope')
        save(path.with_name('manifest-before-deferral.json'),manifest)
        manifest['jobs'].remove(str(retry))
        manifest['original_records']=33000
        manifest['records']=32942
        manifest['deferred_jobs']=[dict(path=str(retry),records=58,
            request_ids=[e['custom_id'] for e in events],
            reason='User requested skipping this retry for now; retain for later retraining',
            remote_job_untouched=True)]
        save(path,manifest)
        state.update(next_check_at=None,deferred_neutral_records=58)
        state.pop('error',None)
        save(WORK/'state.json',state)
        (WORK/'STOP').unlink(missing_ok=True)
        save(WORK/'deferral.json',dict(at=time.time(),records=58,remote_job_cancelled=False,
            neutral_target=32942,baseline_pass_scope='Unchanged: all 42,600 examples',
            combined_delta_target=85142))
        print('Deferred 58 neutral labels; batch 3 is now next. Remote retry untouched.')


if __name__=='__main__':main()
