# Optional Ladybug graph store

Ladybug **0.20.4** is installed in `papers/.venv`, with the official `vector`
and `fts` extensions. This is preparation for future work, not an app migration.
The explorer continues to use SQLite; existing annotations and embeddings are
unchanged. No graph schema or embedding model has been chosen for the real data.

## Recreate setup

Run from the repository root (Python 3.11 was tested on Apple Silicon macOS):

```sh
python3 -m venv papers/.venv
papers/.venv/bin/python -m pip install -r papers/ladybug-requirements.txt
papers/.venv/bin/python papers/ladybug_store.py init
papers/.venv/bin/python -m unittest discover -s papers -p test_ladybug_store.py -v
```

Package and extension installation require internet access. `init` can be rerun;
it opens the existing database without dropping tables or replacing records.
It installs/loads extensions but does not insert sample records. Installation
errors are surfaced; there is no fallback that silently disables search.

Normal readiness check, without extension downloads:

```sh
papers/.venv/bin/python papers/ladybug_store.py check
```

The new environment contains Ladybug only. To also run the existing indexer with
it, install `papers/chemical-requirements.txt`; the running app and its previous
Python environment have not been changed.

## Locations and usage

- Database: `papers/.ladybug/research.lbdb`
- Extension cache: under `papers/.ladybug/runtime/.lbdb/`
- Both are local, ignored by Git, and separate from the SQLite files.
- No background server, listening port, cloud service, or API key is needed.

Use the helper from Python (with `papers` on the module path):

```python
from ladybug_store import open_store

with open_store() as connection:
    result = connection.execute("RETURN $message AS message", {"message": "Ready"})
    try:
        print(result.get_all())
    finally:
        result.close()
```

`open_store(path)` accepts a different database path. It uses a 256 MiB buffer
pool and two execution threads as conservative local defaults. Keep a single
long-lived database owner in a future web server; do not open this writable file
independently in multiple workers. The helper deliberately leaves experimental
multi-writer behavior disabled. Additional connections should share the same
Ladybug database object rather than separately opening the path.

## Verification and future migration

The integration test uses a temporary database and synthetic three-dimensional
vectors, not model-generated embeddings or user data. It verifies a relationship
traversal, cosine nearest-neighbor retrieval, and BM25 full-text retrieval after
closing and reopening the database. Temporary test data is automatically removed.

For later integration, choose typed node/relationship tables, stable application
IDs, embedding dimensions/model metadata, and an explicit import from SQLite.
Do not treat database-internal IDs as portable identifiers. Rebuild and validate
search indexes during migration. Before upgrades, stop writers and back up the
database and its sidecar files; use a logical export for cross-version migration
where required. Do not assume newer packages can read older storage automatically.

References: [Python API](https://docs.ladybugdb.com/client-apis/python/),
[vector search](https://docs.ladybugdb.com/extensions/vector/),
[full-text search](https://docs.ladybugdb.com/extensions/full-text-search/),
[concurrency](https://docs.ladybugdb.com/concurrency/),
[logical export/import](https://docs.ladybugdb.com/migrate/).
