"""Prepare, submit, inspect, and collect the crowd-enVENT OpenAI Batch run.

No custom token cap; no automatic inference retries. Reads OPENAI_API_KEY only
inside HTTP requests. Never prints headers or unredacted API error bodies.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import urllib.error
import urllib.request
import uuid

from run_emotion_label_pilot import MODEL, PROMPT, ROOT, SCHEMA, SOURCE, validate

BASE = ROOT / "papers/emotion-labeling"
RUN = BASE / "batches/crowd-envent-v1"
CORPUS = BASE / "datasets/crowd-envent/extracted/corpus/crowd-enVent_generation.tsv"


def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    tmp.replace(path)


def read(path):
    from portable_paths import relocate
    return relocate(json.loads(path.read_text()))


def api(path, data=None, content_type="application/json", raw=False):
    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise RuntimeError("OPENAI_API_KEY unavailable")
    request = urllib.request.Request("https://api.openai.com/v1/" + path, data=data,
        headers={"Authorization": "Bearer " + key, "Content-Type": content_type})
    try:
        with urllib.request.urlopen(request, timeout=240) as response:
            result = response.read()
    except urllib.error.HTTPError as exc:
        message = exc.read().decode(errors="replace").replace(key, "[REDACTED]")
        message = re.sub(r"sk-[A-Za-z0-9_-]+", "[REDACTED]", message)
        raise RuntimeError(f"HTTP {exc.code}: {message[:1800]}") from None
    except Exception as exc:
        raise RuntimeError(f"Network failure: {type(exc).__name__}; do not blindly resubmit") from None
    return result if raw else json.loads(result)


def build_event(row):
    keys = list(row)
    appraisal = keys[keys.index("suddenness"):keys.index("effort") + 1]
    return {"source": SOURCE, "source_record_id": row["text_id"],
        "scenario": row["generated_text"], "perspective": "The first-person event narrator",
        "starting_state": [0.0] * 23, "source_annotations": {key: row[key] for key in
            ["emotion", "intensity", "event_duration", "emotion_duration", *appraisal]}}


def build_body(template, event):
    section = "## Event\n\n" + json.dumps(event, indent=2) + (
        "\n\nSource appraisal ratings use 1 (not at all) to 5 (extremely). "
        "They are not target adjustments. Participant demographics, personality, "
        "and survey-time emotions are intentionally excluded.\n\n")
    prompt = template.split("## Event\n", 1)[0] + section + "## Output\n" + template.split("## Output\n", 1)[1]
    return {"model": MODEL, "store": False, "reasoning": {"effort": "medium"},
            "input": prompt, "text": {"format": {"type": "json_schema",
                "name": "emotion_label", "strict": True, "schema": SCHEMA}}}


def prepare():
    if (RUN / "manifest.json").exists():
        print("Prepared run already exists; reusing it.")
        return
    RUN.mkdir(parents=True, exist_ok=True)
    template = PROMPT.read_text()
    dimensions = [line.split("|")[2].strip() for line in template.splitlines()
                  if re.match(r"\| \d+ \|", line)]
    assert len(dimensions) == 23
    events, seen = [], set()
    path = RUN / "input.jsonl"
    with CORPUS.open(newline="") as source, path.open("w") as target:
        for row in csv.DictReader(source, delimiter="\t"):
            event = build_event(row)
            cid = "crowd-envent-" + row["text_id"]
            assert cid not in seen
            seen.add(cid)
            body = build_body(template, event)
            assert "max_output_tokens" not in body
            record = {"custom_id": cid, "method": "POST", "url": "/v1/responses", "body": body}
            target.write(json.dumps(record, ensure_ascii=False) + "\n")
            events.append({"custom_id": cid, **event})
    assert len(events) == 6600
    assert path.stat().st_size < 200_000_000
    save(RUN / "events.json", events)
    save(RUN / "schema.json", SCHEMA)
    save(RUN / "dimensions.json", dimensions)
    (RUN / "prompt.md").write_text(template)
    manifest = {"records": len(events), "model": MODEL, "reasoning_effort": "medium",
                "custom_output_token_cap": None, "input_bytes": path.stat().st_size,
                "input_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "prepared_at": datetime.now(timezone.utc).isoformat()}
    save(RUN / "manifest.json", manifest)
    print(json.dumps(manifest, indent=2))


def submit():
    prepare()
    if (RUN / "batch.json").exists():
        print("Already submitted:", read(RUN / "batch.json")["id"])
        return
    if (RUN / "submission-started.json").exists():
        raise RuntimeError("Previous submission may have reached server; reconcile before resubmitting.")
    path = RUN / "input.jsonl"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == read(RUN / "manifest.json")["input_sha256"]
    if not (RUN / "upload.json").exists():
        boundary = "emotion-" + uuid.uuid4().hex
        body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"purpose\"\r\n\r\nbatch\r\n"
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"emotion-input.jsonl\"\r\n"
                "Content-Type: application/jsonl\r\n\r\n").encode() + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
        print("Uploading batch input...", flush=True)
        save(RUN / "upload.json", api("files", body, "multipart/form-data; boundary=" + boundary))
    payload = {"input_file_id": read(RUN / "upload.json")["id"], "endpoint": "/v1/responses",
               "completion_window": "24h", "metadata": {"run": RUN.name, "records": str(read(RUN / "manifest.json")["records"])}}
    save(RUN / "submission-started.json", payload)
    result = api("batches", json.dumps(payload).encode())
    save(RUN / "batch.json", result)
    print(json.dumps(result, indent=2), flush=True)


def status():
    batch = read(RUN / "batch.json")
    result = api("batches/" + batch["id"])
    save(RUN / "batch.json", result)
    print(json.dumps(result, indent=2), flush=True)
    return result


def collect():
    batch = status()
    if batch["status"] not in {"completed", "failed", "expired", "cancelled"}:
        print("Not terminal yet; no results collected.")
        return
    for key, filename in [("output_file_id", "output.jsonl"), ("error_file_id", "errors.jsonl")]:
        if batch.get(key) and not (RUN / filename).exists():
            data = api("files/" + batch[key] + "/content", raw=True)
            (RUN / filename).write_bytes(data)
    events = {e["custom_id"]: e for e in read(RUN / "events.json")}
    names, schema = read(RUN / "dimensions.json"), read(RUN / "schema.json")
    reviews = RUN / "reviews"
    reviews.mkdir(exist_ok=True)
    valid, failures, seen = [], [], set()
    usage = {"input_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0, "cached_tokens": 0}
    for filename in ("output.jsonl", "errors.jsonl"):
        if not (RUN / filename).exists():
            continue
        with (RUN / filename).open() as source:
            for line in source:
                record = json.loads(line)
                cid = record["custom_id"]
                if cid not in events or cid in seen:
                    raise ValueError("Unknown or duplicate output custom_id")
                seen.add(cid)
                response = record.get("response") or {}
                body = response.get("body") or {}
                u = body.get("usage") or {}
                usage["input_tokens"] += u.get("input_tokens", 0)
                usage["output_tokens"] += u.get("output_tokens", 0)
                usage["reasoning_tokens"] += (u.get("output_tokens_details") or {}).get("reasoning_tokens", 0)
                usage["cached_tokens"] += (u.get("input_tokens_details") or {}).get("cached_tokens", 0)
                try:
                    if record.get("error") or response.get("status_code") != 200 or body.get("status") != "completed":
                        raise ValueError("API error or incomplete response")
                    contents = [c for item in body.get("output", []) if item.get("type") == "message"
                                for c in item.get("content", [])]
                    if any(c.get("type") == "refusal" for c in contents):
                        raise ValueError("Refusal")
                    label = json.loads("".join(c["text"] for c in contents if c.get("type") == "output_text"))
                    validate(label, schema)
                except (ValueError, AssertionError, KeyError, TypeError) as exc:
                    failures.append({"custom_id": cid, "reason": str(exc) or type(exc).__name__})
                    continue
                valid.append({**events[cid], "label": label, "model": body.get("model"), "usage": u})
                lines = [f"# {cid}", "", events[cid]["scenario"], "", label["extras"]["response_summary"],
                         "", "| Dimension | Adjustment |", "|---|---:|"]
                lines += [f"| {name} | {v:+.3f} |" for name, v in zip(names, label["adjustments"])]
                lines += ["", "## Generated extras", "", "```json", json.dumps(label["extras"], indent=2), "```", ""]
                (reviews / f"{cid}.md").write_text("\n".join(lines))
    with (RUN / "labels.jsonl").open("w") as target:
        for record in valid:
            target.write(json.dumps(record, ensure_ascii=False) + "\n")
    summary = {"valid": len(valid), "failed": len(failures), "missing": sorted(set(events) - seen),
               "failures": failures, "usage": usage, "automatically_retried": False}
    save(RUN / "collection-summary.json", summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "submit", "status", "collect"])
    args = parser.parse_args()
    try:
        globals()[args.action]()
    except RuntimeError as exc:
        raise SystemExit(str(exc)) from None
