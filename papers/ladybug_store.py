"""Optional embedded graph infrastructure; does not migrate the SQLite index."""
from contextlib import contextmanager
from pathlib import Path
import argparse

import ladybug as lb

DEFAULT_PATH = Path(__file__).resolve().parent / ".ladybug" / "research.lbdb"
EXTENSION_ROOT = DEFAULT_PATH.parent / "runtime"


@contextmanager
def open_store(path=DEFAULT_PATH, *, install_extensions=False):
    """Own one database/connection; share its database object in a future server.

    Only explicit setup installs extensions. Normal use loads installed extensions
    without requesting downloads. No application schema is imposed here.
    """
    path = Path(path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = lb.Database(str(path), buffer_pool_size=256 * 1024 * 1024,
                     max_num_threads=2)
    try:
        conn = lb.Connection(db)
        try:
            EXTENSION_ROOT.mkdir(parents=True, exist_ok=True)
            runtime = str(EXTENSION_ROOT).replace("\\", "\\\\").replace("'", "\\'")
            conn.execute(f"CALL home_directory='{runtime}'").close()
            for extension in ("vector", "fts"):
                if install_extensions:
                    conn.execute(f"INSTALL {extension}").close()
                conn.execute(f"LOAD {extension}").close()
            yield conn
        finally:
            conn.close()
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "check"])
    parser.add_argument("--database", type=Path, default=DEFAULT_PATH)
    args = parser.parse_args()
    if args.command == "check" and not args.database.exists():
        parser.error("Database does not exist; run init first.")
    with open_store(args.database, install_extensions=args.command == "init") as conn:
        result = conn.execute("RETURN 1")
        try:
            assert result.get_next()[0] == 1
        finally:
            result.close()
    print(f"Ladybug {lb.__version__}: graph, vector and fts loaded; {args.database.resolve()}")


if __name__ == "__main__":
    main()
