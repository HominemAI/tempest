"""Run exactly two real crowd-enVENT records through GPT-5 nano.

Uses OPENAI_API_KEY only from the environment; never logs credentials or HTTP
error bodies. No automatic retries or batch submission. Standard API pricing.
Usage: python3 papers/run_emotion_label_pilot.py --run
"""
import argparse
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
PROMPT = ROOT / "docs/emotional-response-labeling-sample-prompt.md"
MODEL = "gpt-5-nano-2025-08-07"
SOURCE = "https://www.romanklinger.de/data-sets/crowd-enVent2023.zip"


def obj(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def array(items):
    return {"type": "array", "items": items}


STRING = {"type": "string"}
INDEX = {"type": "integer", "minimum": 1, "maximum": 23}
SCHEMA = obj({
    "adjustments": {**array({"type": "number"}), "minItems": 23, "maxItems": 23},
    "extras": obj({
        "response_summary": STRING,
        "adjustment_notes": array(obj({"dimension_index": INDEX, "justification": STRING})),
        "physical_responses": array(STRING),
        "action_tendencies": array(STRING),
        "attention_changes": array(STRING),
        "possible_reactions": array(obj({"action": STRING, "expression": {"type": ["string", "null"]}})),
        "time_course": obj({"onset": STRING, "possible_persistence": STRING}),
        "regulation_opportunities": array(obj({"action": STRING, "possible_effect": STRING})),
        "alternative_response_profiles": {**array(obj({
            "summary": STRING,
            "differences": array(obj({"dimension_index": INDEX,
                "relative_to_primary": {"type": "string", "enum": ["higher", "lower"]},
                "explanation": STRING}))})), "maxItems": 3},
        "uncertainty": array(STRING), "assumptions": array(STRING),
        "annotation_tensions": array(STRING),
    }),
})


def validate(value, schema):
    kind = schema["type"]
    if isinstance(kind, list):
        assert value is None or isinstance(value, str)
    elif kind == "object":
        assert isinstance(value, dict) and set(value) == set(schema["properties"])
        for key, child in schema["properties"].items():
            validate(value[key], child)
    elif kind == "array":
        assert isinstance(value, list)
        assert schema.get("minItems", 0) <= len(value) <= schema.get("maxItems", math.inf)
        for item in value:
            validate(item, schema["items"])
    elif kind in ("number", "integer"):
        assert type(value) in ((int,) if kind == "integer" else (int, float))
        assert math.isfinite(value)
        assert schema.get("minimum", -math.inf) <= value <= schema.get("maximum", math.inf)
    else:
        assert isinstance(value, str)
        if "enum" in schema:
            assert value in schema["enum"]


def write_json(path, data):
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=Path("/private/tmp/crowd-enVent2023-inspect.zip"))
    parser.add_argument("--run", action="store_true", help="Make two paid API requests")
    parser.add_argument("--record-id", help="Run only one specific corpus record")
    parser.add_argument("--reasoning", choices=["low", "medium", "high"], default="medium")
    parser.add_argument("--max-output-tokens", type=int, default=16000,
                        help="Budget includes reasoning and final output (default: 16000)")
    args = parser.parse_args()
    template = PROMPT.read_text()
    with zipfile.ZipFile(args.archive) as archive:
        rows = list(csv.DictReader(io.StringIO(archive.read(
            "corpus/crowd-enVent_generation.tsv").decode()), delimiter="\t"))
    selected = [next(r for r in rows if r["text_id"] == "216"),
                next(r for r in rows if r["emotion"] == "joy")]
    if args.record_id:
        selected = [next(r for r in rows if r["text_id"] == args.record_id)]
    print(f"Corpus: {len(rows)} events; selected IDs: {[r['text_id'] for r in selected]}", flush=True)
    if not args.run:
        print("Dry run; add --run to submit exactly two requests.")
        return
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY is unavailable; no requests sent.")
    out = ROOT / "papers/emotion-labeling/pilots" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    out.mkdir(parents=True, exist_ok=False)
    names = [line.split("|")[2].strip() for line in template.splitlines()
             if re.match(r"\| \d+ \|", line)]
    assert len(names) == 23
    (out / "rubric-prompt.md").write_text(template)
    write_json(out / "schema.json", SCHEMA)
    reports = []
    for row in selected:
        rid = row["text_id"]
        # Do not transmit participant identifiers or demographics. These are
        # deliberately outside the neutral-start model's personality layer.
        appraisal_keys = list(row)[list(row).index("suddenness"):list(row).index("effort") + 1]
        evidence = {key: row[key] for key in
                    ["emotion", "intensity", "event_duration", "emotion_duration", *appraisal_keys]}
        event = {"source": SOURCE, "source_record_id": rid,
                 "scenario": row["generated_text"], "perspective": "The first-person event narrator",
                 "starting_state": [0.0] * 23, "source_annotations": evidence}
        event_text = "## Event\n\n" + json.dumps(event, indent=2) + (
            "\n\nSource appraisal ratings use 1 (not at all) to 5 (extremely). "
            "They are not target adjustments. Participant demographics, personality, "
            "and survey-time emotions are intentionally excluded.\n\n")
        prompt = template.split("## Event\n", 1)[0] + event_text + "## Output\n" + template.split("## Output\n", 1)[1]
        payload = {"model": MODEL, "store": False, "reasoning": {"effort": args.reasoning},
                   "max_output_tokens": args.max_output_tokens, "input": prompt,
                   "text": {"format": {"type": "json_schema", "name": "emotion_label",
                                         "strict": True, "schema": SCHEMA}}}
        write_json(out / f"{rid}-input.json", event)
        write_json(out / f"{rid}-request.json", payload)
        print(f"Submitting {rid} ({row['emotion']})", flush=True)
        request = urllib.request.Request("https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode(), method="POST",
            headers={"Authorization": "Bearer " + os.environ["OPENAI_API_KEY"],
                     "Content-Type": "application/json"})
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                raw = json.load(response)
        except urllib.error.HTTPError as exc:
            raise SystemExit(f"HTTP {exc.code}; stopped without retry. Error body suppressed to protect credentials.") from None
        except Exception as exc:
            raise SystemExit(f"Request failed ({type(exc).__name__}); stopped without retry.") from None
        elapsed = time.monotonic() - started
        write_json(out / f"{rid}-response.json", raw)
        if raw.get("status") != "completed":
            raise SystemExit(f"Record {rid}: status {raw.get('status')}; saved response, no retry.")
        content = [c for item in raw.get("output", []) if item.get("type") == "message"
                   for c in item.get("content", [])]
        if any(c.get("type") == "refusal" for c in content):
            raise SystemExit(f"Record {rid}: refusal; saved response, no retry.")
        label = json.loads("".join(c["text"] for c in content if c.get("type") == "output_text"))
        validate(label, SCHEMA)
        write_json(out / f"{rid}-label.json", label)
        usage = raw["usage"]
        cached = usage.get("input_tokens_details", {}).get("cached_tokens", 0)
        cost = ((usage["input_tokens"] - cached) * .05 + cached * .005 + usage["output_tokens"] * .4) / 1_000_000
        reports.append({"id": rid, "model": raw["model"], "seconds": round(elapsed, 2),
                        "usage": usage, "estimated_standard_cost_usd": cost,
                        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest()})
        write_json(out / "run-summary.json", reports)
        print(json.dumps(reports[-1]), flush=True)
        lines = [f"# Record {rid}: {row['emotion']}", "", row["generated_text"], "",
                 label["extras"]["response_summary"], "", "| Dimension | Adjustment |", "|---|---:|"]
        lines += [f"| {name} | {value:+.3f} |" for name, value in zip(names, label["adjustments"])]
        lines += ["", "## Generated extras", "", "```json", json.dumps(label["extras"], indent=2), "```", ""]
        (out / f"{rid}-review.md").write_text("\n".join(lines))
    print(f"Results: {out}", flush=True)


if __name__ == "__main__":
    main()
