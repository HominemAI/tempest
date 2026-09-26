# Local passage transformation tools

Two independent CLI tools, with no hosted inference or API keys. Originals and
the cancelled Vertex batch remain untouched. TXT outputs contain passage text only;
metadata and change logs are separate.

## Setup

From the repository root:

```sh
python3 -m venv papers/.venv-passage-tools
papers/.venv-passage-tools/bin/pip install -r papers/passage-tools-requirements.txt
papers/.venv-passage-tools/bin/python -m spacy download en_core_web_sm
papers/.venv-passage-tools/bin/python -m nltk.downloader -d papers/.nltk-data wordnet
```

## Replace names, then randomize verbs

```sh
papers/.venv-passage-tools/bin/python papers/replace_passage_names.py \
  --input papers/emotion-labeling/datasets/public-domain-works/passages-by-book \
  --output papers/emotion-labeling/datasets/public-domain-works/passages-names-v2

papers/.venv-passage-tools/bin/python papers/swap_passage_verbs.py \
  --input papers/emotion-labeling/datasets/public-domain-works/passages-names-v2 \
  --output papers/emotion-labeling/datasets/public-domain-works/passages-names-verbs-v3 \
  --seed 42
```

Each tool can also run independently against the original passage directory.
Output directories must be new; neither tool overwrites existing runs.

Names are detected using spaCy PERSON entities with at least one capitalized proper
noun. Rejected candidates are saved separately for review. A separate mapping is maintained
per relative parent directory (one directory per book in this corpus). Numbering
starts at person1 in each book. Exact repeats and unambiguous detected short/full
forms share an ID. Ambiguous surnames and titled names are not automatically merged.
Possessives remain intact. This is **not guaranteed anonymization or coreference**:
fictional names, aliases and unusual typography may be missed or misclassified.
Pronouns, places and organizations are not intentionally substituted.

Optional `--aliases aliases.json` accepts an object keyed by book folder, e.g.
`{"sherlock-holmes": {"Holmes": "Sherlock Holmes", "Sherlock Holmes": "Sherlock Holmes"}}`.
Keys are literal name spellings; values group aliases into identities. Review
`name-map.json` and rerun into a new directory to correct detections. When two
books are later paired, person IDs must be understood as passage/book-local,
not evidence that person1 in each book is the same character.

Verbs are identified by their contextual VERB part-of-speech tag. Auxiliaries
(such as “was” in “was running”), the copula “be”, contraction fragments, named
entities, and placeholders are left alone. The contraction guard prevents a
misclassified “’s” from being spliced into the preceding word as a new verb.
Random single-word verb lemmas come from WordNet, excluding be/have/do as targets;
`--dictionary verbs.txt` instead accepts your own one-lemma-per-line dictionary.
LemmInflect attempts to preserve the original verb form (past, participle, etc.).
The replacement differs from the original lemma and surface form. The random
seed is scoped by relative file path, so adding other files does not reshuffle it.

These are random replacements, **not synonyms**. Dictionary words can be obscure,
and verb valency, phrasal verbs, meaning and grammatical agreement are not fully
preserved. Names-first avoids damaging name recognition with randomized context.
Review the copies as inspiration material before generating new situations.

## Audit files

- `changes.jsonl`: each edit's original/replacement text, source and output
  character offsets (zero-based, end-exclusive, Unicode code points), file hashes.
- `passages.jsonl`: updated text plus original source provenance and transform chain.
  Original source line numbers describe the original book, not the transformed text.
- `name-map.json`: per-book alias-to-placeholder table (name tool).
- `rejected-name-candidates.json`: PERSON spans rejected by the proper-noun filter.
- `verb-dictionary.json`: exact candidate dictionary snapshot (verb tool).
- `manifest.json`: counts, seed, model/version and dictionary information.

Detection uses [spaCy's linguistic annotations](https://spacy.io/usage/linguistic-features),
the dictionary uses [NLTK WordNet](https://www.nltk.org/howto/wordnet), and verb forms
use [LemmInflect](https://pypi.org/project/lemminflect/).

## Tests

```sh
python3 -m unittest discover -s papers -p test_passage_transforms.py
```

Running these tools does not prepare, upload or submit a replacement Vertex batch.
Initial transformation folders are retained as trial outputs. Use `passages-names-v2`
for names-only and `passages-names-verbs-v3` for the combined result. These filter
observed name false positives such as “Said” and “bush” and protect contractions.
