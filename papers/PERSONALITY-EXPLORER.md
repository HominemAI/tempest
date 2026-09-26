# Personality explorer

Run `python3 papers/personality_app.py` and open http://127.0.0.1:8767.
This is a local-only companion to the research explorer, using its visual system.

Browse the validated generated profiles, search all profile text, and edit all 23
coordinates. Randomize all samples independent uniform hundredths in [-10,10];
Small variation adds up to one point per coordinate and clamps to that range.
Zero resets all coordinates. Neither randomizer claims a realistic population.
Pin a profile to compare its numbers while browsing another.

Values → description and Description → values each make one explicit, paid
online GCP gpt-oss-120b call using Application Default Credentials, the original
rubric and guide. No custom output-token cap or automatic retry is used.
Review the returned draft and Save to persist it. No paid generation occurs
merely by opening the interface. A description-to-vector proposal is not an
inverse measurement or a diagnosis.

## Originals and revisions

`personality-data/personalities.sqlite` holds the canonical editable originals.
Save updates the same ID, with the previous body retained in the history table.
Duplicate creates a new personality only when explicitly selected. Concurrent
edits are rejected using revision numbers. Refresh imports only missing IDs;
it never overwrites saved edits. Back up the SQLite file with the server stopped.
JSON export downloads the current editor contents, including unsaved edits.

Provider responses, submitted request files and their frozen baselines remain
immutable provenance. They are not the editable canonical records. An existing
delta remains associated with the state used to generate it even if the current
personality is subsequently edited. Changing a profile does not relabel old deltas.

## Delta documents and training

View event deltas selects that profile's collected labels. Neutral labels are
also available. Refresh results imports newly collected `labels.jsonl` files;
the interface itself does not poll GCP or download unfinished jobs. Empty results
mean no validated collected labels are available for that selected baseline.

Each delta editor exposes event text, all 23 adjustments, a readable summary,
and the full supporting extras JSON. Delta numbers are not capped at [-10,10].
Save validates the existing label schema and preserves starting-state/provenance.

The final `delta_workflow_training.py` assembly overlays edited canonical deltas
on the original source labels, records an edit fingerprint, and refuses to reuse
a training snapshot after edits change. It does not automatically retrain or
rewrite previously trained models. Other standalone training commands must use
the canonical edited data explicitly; they do not automatically read this store.

68 invalid generated baseline responses are excluded; the current import contains
1,932 validated profiles. The 2,000-vector source is preserved.

Tests: `python3 -m unittest discover -s papers -p test_personality_app.py`.
