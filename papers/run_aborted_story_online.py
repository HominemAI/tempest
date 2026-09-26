"""Run the single aborted story request online, with duplicate-call protection."""
import json
from datetime import datetime, timezone

from retry_aborted_story import OUT, CID
from vertex_literature_runner import api
from vertex_literature_batch import save, validate_output


def main():
    started = OUT / 'online-started.json'
    if started.exists():
        raise RuntimeError('Online attempt already recorded; inspect its result before retrying')
    batch = json.loads((OUT / 'batch.json').read_text())
    name = batch['name']
    expected = 'projects/1036852640072/locations/us-central1/batchPredictionJobs/7700378428989505536'
    if name != expected:
        raise RuntimeError('Unexpected batch target')
    url = 'https://us-central1-aiplatform.googleapis.com/v1/' + name
    status = api(url)
    save(OUT / 'batch.json', status)
    terminal = {'JOB_STATE_CANCELLED', 'JOB_STATE_FAILED', 'JOB_STATE_EXPIRED', 'JOB_STATE_SUCCEEDED', 'JOB_STATE_PARTIALLY_SUCCEEDED'}
    if status['state'] not in terminal and status['state'] != 'JOB_STATE_CANCELLING':
        api(url + ':cancel', b'{}', raw=True)
        save(OUT / 'online-batch-cancellation.json', {'name': name, 'requested_at': datetime.now(timezone.utc).isoformat()})
        status = api(url)
        save(OUT / 'batch.json', status)
    print('Previous single-request batch:', status['state'], flush=True)
    if status['state'] in {'JOB_STATE_SUCCEEDED', 'JOB_STATE_PARTIALLY_SUCCEEDED'}:
        raise RuntimeError('Batch already finished; inspect its output before duplicating the request')
    rows = [json.loads(line) for line in (OUT / 'input.jsonl').read_text().splitlines() if line.strip()]
    if len(rows) != 1 or rows[0]['custom_id'] != CID:
        raise RuntimeError('Expected exactly the one aborted request')
    body = rows[0]['body']
    endpoint = 'https://us-central1-aiplatform.googleapis.com/v1/projects/smg-cluster/locations/us-central1/endpoints/openapi/chat/completions'
    save(OUT / 'online-request.json', body)
    save(started, {'custom_id': CID, 'endpoint': endpoint, 'started_at': datetime.now(timezone.utc).isoformat()})
    print('Submitting one online request (no custom output-token cap).', flush=True)
    response = api(endpoint, json.dumps(body).encode())
    save(OUT / 'online-response.json', response)
    choice = response['choices'][0]
    if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
        raise RuntimeError('Response incomplete or refused; raw response saved')
    value = json.loads(choice['message']['content'])
    items = validate_output(value, CID)
    save(OUT / 'online-result.json', value)
    save(OUT / 'online-validation.json', {'valid': True, 'situations': len(items), 'custom_id': CID, 'usage': response.get('usage'), 'completed_at': datetime.now(timezone.utc).isoformat()})
    print(f'Validated and saved {len(items)} situations.', flush=True)


if __name__ == '__main__':
    main()
