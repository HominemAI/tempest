# Reusable subject index

An optional [Ladybug graph/vector store](LADYBUG.md) is installed for future use.
It is not yet connected to this index or viewer; SQLite remains the active backend.

Open [explorer.html](explorer.html) directly for offline, all-field text search, category and paper-label filters, CSV export, and a centered rendered-Markdown reader. The **Paper index** tab includes every cataloged document, including papers with no vocabulary matches.

## Reader and annotations

Paper links open a centered, nearly full-screen reader, preserving the underlying table position. Markdown is rendered as headings, prose, lists, and tables. The requested source line selects and highlights its containing passage. Raw HTML is displayed as text, not executed; remote images are not fetched. The bundled Marked lexer is MIT-licensed in `vendor/` and needs no CDN.

- **Bold word initials** slightly emphasizes each word's first letter. This toggle persists between papers within the current page session.
- **Search this paper** highlights literal words or phrases, with Previous/Next controls. Enter advances; Shift+Enter goes backward. Ctrl/Cmd+F targets this search while the reader is open.
- Selecting text opens **Look up definition**, **Highlight**, and an optional comment field. Definition lookup displays up to four dictionary senses directly in the selection popup, with source/license links; it never automatically opens another tab. Only the selected word or short term is sent to Free Dictionary API when clicked (maximum 100 characters/eight words), without credentials or a referrer. An internet connection is required for uncached terms. Results are cached in memory for the current page session. Missing entries, network failures, and overly long selections have explicit messages; this is dictionary lookup, not an AI-generated explanation of the paper.
- Click a saved green highlight (or focus it and press Enter/Space) to read or edit its comment. In the local web app (`subject_index.py serve`), highlights/comments are stored in **`annotations.sqlite`**, separately from the rebuildable vector index. When opening the HTML directly as a file, notes instead use browser `localStorage` under `research-explorer-highlights-v1`; those offline notes depend on the same browser/file origin and can be lost if browser data is cleared. Storage errors are displayed rather than silently falling back.
- **Notes backup / transfer** provides JSON export/import. To move previous offline notes to SQLite, export them from the file-opened explorer, open the local web app, and import the JSON. File and localhost browser storage cannot be read across origins automatically. Imports preserve annotation IDs and update matching IDs; source-mismatched notes are rejected with a partial-import count.
- Each annotation stores document ID, relative Markdown path, source SHA-256, selected text, comment, timestamps, and per-passage segments. Segments include original source offsets, 1-based lines, 0-based UTF-16 columns (exclusive end), exact source text, and rendered character offsets. Highlights are restored only when the source hash matches. If a complex selection cannot be precisely mapped back to Markdown, saving is refused with an explanation; try a smaller prose selection. No fuzzy relocation onto changed text is attempted.

After changing the viewer template, refresh the HTML without rebuilding vectors:

```sh
papers/.venv/bin/python papers/subject_index.py render
```

Browser regression tests: `node papers/test_explorer.cjs` (requires Playwright; optionally set `PLAYWRIGHT_MODULE` and `BROWSER_EXECUTABLE`). Tests use an isolated browser context and cover saved comments, highlighting, source coordinates, search, typography, keyboard behavior, and layout.

The small web app uses Python's HTTP server and SQLite, binds only to `127.0.0.1`, and rejects foreign hosts/origins. `GET /api/annotations` reads notes; `POST /api/annotations` validates source hash, quote, and UTF-16 line/column coordinates before saving. The embedding model loads only when semantic search is used. This is a local single-user tool, not a public multi-user deployment. `annotations.sqlite` is excluded from Git and never replaced by an index rebuild. Stop the server before copying it for a raw-file backup, or use JSON export while running.

The reusable pipeline is [subject_index.py](subject_index.py). It reads Markdown; it does not reconvert or modify PDFs or Markdown sources. Everything is local. A public embedding-model download is required once; paper text is never uploaded.

## Run again

To throttle embedding, pass `--embedding-delay 2` to `build_research_index.py`
(or `subject_index.py build`). This adds two seconds between vector contexts,
forces one embedding worker, and checkpoints each completed context before pausing.
Previously saved vectors are reused when restarting.

From the repository root:

```sh
python3 -m venv papers/.venv
papers/.venv/bin/python -m pip install -r papers/chemical-requirements.txt
```

Download and cache the small CPU embedding model once:

```sh
HF_HUB_DISABLE_TELEMETRY=1 HF_HUB_DISABLE_XET=1 papers/.venv/bin/python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='papers/.models', threads=2, providers=['CPUExecutionProvider'])"
```

Then build or serve:

```sh
papers/.venv/bin/python papers/build_research_index.py
papers/.venv/bin/python papers/subject_index.py serve
# Open http://127.0.0.1:8765 for free-text semantic search.
papers/.venv/bin/python papers/subject_index.py search 'anesthesia and loss of consciousness'
papers/.venv/bin/python -m unittest discover -s papers -p test_subject_index.py
```

Build and search require cached weights and use `local_files_only=True`. The initial build in this workspace used `/private/tmp/consciousness-pdf-converter-venv/bin/python`; create the durable `papers/.venv` above if that temporary environment disappears. Cached weights live in ignored `papers/.models/`.

`build_research_index.py` combines the original catalog with the biochemical-behavior,
biochemical-crime, and adrenaline-opioids full-text catalogs, preserves their paper labels, and calls the existing
`subject_index.py` pipeline. It writes `combined-index-catalog.json` and
`combined-document-labels.json` as reproducible inputs. Citation-only entries are
excluded. Original Markdown paths and hashes remain unchanged, preserving reader
annotation anchors. Running `subject_index.py build` without catalog arguments
still builds only the original collection; use the wrapper to rebuild all four collections.

Both the combined wrapper and generic builder default to one local embedding
worker (two CPU threads) and accept `--embedding-workers N`. The wrapper also
limits auxiliary numerical/tokenizer threads to avoid oversubscribing the machine.
Both use identical token windows and mean pooling. Completed embedding batches
are checkpointed in ignored `.subject-index-vectors/embeddings.sqlite`, so an
interrupted build can reuse them. This cache contains context embeddings, not
reader annotations; final index files are published only after embedding succeeds.
The optional `test_embedding_parallel.py` checks serial/parallel agreement using
the cached model, without a network download.

## Use a different subject or corpus

```sh
papers/.venv/bin/python papers/subject_index.py build \
  --corpus /path/to/new-corpus \
  --catalog /path/to/new-corpus/catalog.json \
  --vocabulary /path/to/new-subject.json \
  --labels /path/to/new-corpus/document-labels.json \
  --output /path/to/new-output \
  --title 'Your subject' --context 3

papers/.venv/bin/python papers/subject_index.py serve --output /path/to/new-output
```

Catalog schema (paths relative to `--corpus`):

```json
{"papers":[{"markdown":"field/paper.md","title":"Paper title","area":"primary field","type":"Empirical study","authors":"Author","year":2026}]}
```

Vocabulary schema (any subject, not chemistry-specific):

```json
{"terms":[{"id":"example","name":"Example concept","category":"Concept category","origin":"optional grouping","aliases":["alternative spelling"],"related_terms":["related adjective"]}]}
```

The original chemical vocabulary uses `chemicals` instead of `terms`; both keys work. Use unambiguous terms. Short aliases containing uppercase letters are matched case-sensitively. Other phrases are case-insensitive, bounded by word characters. Hyphen variants, line-end hyphenation, common ligatures, and basic Markdown emphasis are normalized for matching, while source line numbers remain exact. Every occurrence becomes one row; longest overlapping vocabulary match wins.

Paper-label schema:

```json
{"documents":{"field/paper.md":{"labels":["neuroscience","psychology","perception"]}}}
```

Edit [document-labels.json](document-labels.json), then rebuild. Labels are curated discovery metadata based on the catalog, not author-assigned classifications. A new paper without a label entry falls back to its primary field. Use an empty `{"documents":{}}` file to start a new corpus with field-only labels. Chemical categories and origins come from the vocabulary, independently of paper labels.

## Generated files

- `document-index.json`: document-level source of generated metadata, multiple labels, relative Markdown paths, and SHA-256 source hashes.
- `subject-index.sqlite`: normalized documents, document labels, terms, references, and local vectors. The `reference_index` SQL view joins metadata and labels for exploration.
- `references.csv`: flat reference export. Potential spreadsheet formulas are prefixed with an apostrophe; exact source remains in SQLite and the explorer.
- `explorer.html`: self-contained table and full embedded Markdown source. No CDN, application build, or main-product dependency.

Vectors are normalized 384-dimensional float32 little-endian blobs from `BAAI/bge-small-en-v1.5` through FastEmbed/ONNX Runtime on CPU. Inputs contain title, matched term, and surrounding context. Long inputs are split into overlapping 440-token windows and mean-pooled, then normalized. Identical inputs reuse vectors from the previous index. Queries use exact cosine comparison; this modest corpus does not need a separate vector database. Ranking is retrieval similarity, **not confidence or scientific evidence strength**.

## Interpretation and limits

This is a **vocabulary concordance**, not an exhaustive chemical ontology or causal-evidence extraction system. It does not infer concentrations, biochemical balances, effect directions, experimental support, or administered exposure. Related adjective/receptor-family mentions are labeled separately. Endogenous/exogenous is a vocabulary grouping: some compounds can be both endogenous and administered. Bibliography entries are included; a matched citation is not necessarily a finding of the paper. Document type uses the catalog classification before its first semicolon; the full original citation/type text is retained in the separately searchable `catalog_details` field.

Context is the matched Markdown paragraph plus up to three preceding and three following blocks. PDF extraction may split or merge true prose paragraphs; headings are retained as blocks, page markers and horizontal rules are excluded, and context can span physical PDF pages. PDF page is the converter's physical page index, not necessarily the printed page number. OCR errors can cause missed matches. Markdown source lines, not rendered line wraps, determine location.

The explorer inherits paper labels by document ID. Clicking a paper title opens its embedded source at the reference's exact start line. Relative Markdown links remain in records and link targets; the local server intentionally serves only the explorer and semantic API, not arbitrary filesystem paths. Direct source files are accessible through relative links when opening the HTML as a file. Semantic search requires the loopback server; ordinary search and the source viewer work completely offline.

Vocabulary sources: [NCBI neurotransmitters](https://www.ncbi.nlm.nih.gov/books/NBK10795/), [neurotransmitter categories](https://www.ncbi.nlm.nih.gov/books/NBK10960/), [HPA axis](https://www.ncbi.nlm.nih.gov/books/NBK278995/), [neurotrophic factors](https://www.ncbi.nlm.nih.gov/books/NBK6480/), and [IUPHAR/BPS Guide to Pharmacology](https://www.guidetopharmacology.org/). Embedding implementation: [FastEmbed documentation](https://qdrant.tech/documentation/fastembed/fastembed-quickstart/).
