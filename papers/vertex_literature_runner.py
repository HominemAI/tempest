"""Upload and submit the frozen literature batch using Google ADC. No token logging."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request

from vertex_literature_batch import RUN, save


def read(name):
    return json.loads((RUN / name).read_text())


def api(url, payload=None, content_type='application/json', raw=False):
    result = subprocess.run(['gcloud', 'auth', 'application-default', 'print-access-token'],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Application Default Credentials unavailable; refresh with gcloud auth application-default login')
    token = result.stdout.strip()
    request = urllib.request.Request(url, data=payload, headers={
        'Authorization': 'Bearer ' + token, 'Content-Type': content_type,
        'X-Goog-User-Project': 'smg-cluster'})
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            body = response.read()
    except urllib.error.HTTPError as exc:
        message = exc.read().decode(errors='replace').replace(token, '[REDACTED]')
        raise RuntimeError(f'HTTP {exc.code}: {message[:1800]}') from None
    except Exception as exc:
        raise RuntimeError(f'Network failure: {type(exc).__name__}; reconcile submission before any retry') from None
    return body if raw else json.loads(body)


def preflight():
    bucket = api('https://storage.googleapis.com/storage/v1/b/dz_batch_data?fields=name,location,projectNumber')
    project = api('https://cloudresourcemanager.googleapis.com/v1/projects/smg-cluster')
    if str(bucket['projectNumber']) != str(project['projectNumber']):
        raise RuntimeError('Bucket is not in the selected project; stop for review')
    result = dict(bucket=bucket, project_id=project['projectId'], project_number=project['projectNumber'],
                  checked_at=datetime.now(timezone.utc).isoformat(), authentication='Application Default Credentials')
    save(RUN / 'preflight.json', result)
    return result


def submit():
    if (RUN / 'batch.json').exists():
        return read('batch.json')
    if (RUN / 'submission-started.json').exists():
        raise RuntimeError('Submission may already have reached Vertex; reconcile before retrying')
    preflight()
    config, job, manifest = read('submission-config.json'), read('job-config.json'), read('manifest.json')
    expected_prefix = 'gs://dz_batch_data/' + RUN.name.removeprefix('vertex-')
    if (not re.fullmatch(r'vertex-((literature|literature-repair|random-baselines)-oss120b-v\d+|baseline-deltas-oss120b-v1-part\d{3})', RUN.name)
            or config['project'] != 'smg-cluster' or config['gcs_prefix'] != expected_prefix
            or config['region'] != 'us-central1'
            or config['endpoint'] != 'https://us-central1-aiplatform.googleapis.com/v1/projects/smg-cluster/locations/us-central1/batchPredictionJobs'
            or job['model'] != 'publishers/openai/models/gpt-oss-120b-maas'
            or job['inputConfig']['gcsSource']['uris'] != [expected_prefix + '/input.jsonl']
            or job['outputConfig']['gcsDestination']['outputUriPrefix'] != expected_prefix + '/output/'):
        raise RuntimeError('Unexpected project or storage destination')
    data = (RUN / 'input.jsonl').read_bytes()
    if hashlib.sha256(data).hexdigest() != manifest['input_sha256']:
        raise RuntimeError('Frozen batch input hash mismatch')
    if not (RUN / 'upload.json').exists():
        name = RUN.name.removeprefix('vertex-') + '/input.jsonl'
        url = 'https://storage.googleapis.com/upload/storage/v1/b/dz_batch_data/o?' + urllib.parse.urlencode(
            {'uploadType': 'media', 'name': name, 'ifGenerationMatch': '0'})
        # ifGenerationMatch=0 refuses to overwrite an existing object.
        uploaded = api(url, data, 'application/jsonl')
        save(RUN / 'upload.json', uploaded)
    uploaded = read('upload.json')
    object_name = urllib.parse.quote(uploaded['name'], safe='')
    verify_url = f'https://storage.googleapis.com/storage/v1/b/dz_batch_data/o/{object_name}?alt=media'
    if hashlib.sha256(api(verify_url, raw=True)).hexdigest() != manifest['input_sha256']:
        raise RuntimeError('Uploaded input verification failed')
    save(RUN / 'submission-started.json', dict(endpoint=config['endpoint'], body=job,
                                             started_at=datetime.now(timezone.utc).isoformat()))
    response = api(config['endpoint'], json.dumps(job).encode())
    save(RUN / 'batch.json', response)
    manifest['status'] = 'submitted'
    manifest['batch_name'] = response['name']
    save(RUN / 'manifest.json', manifest)
    return response


def status():
    config, batch = read('submission-config.json'), read('batch.json')
    response = api(f'https://{config["region"]}-aiplatform.googleapis.com/v1/{batch["name"]}')
    save(RUN / 'batch.json', response)
    return response


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', type=Path, default=RUN, help='Run directory to submit or inspect')
    parser.add_argument('action', choices=['preflight', 'submit', 'status'])
    args = parser.parse_args()
    RUN = args.run.resolve()
    try:
        print(json.dumps(globals()[args.action](), indent=2))
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
