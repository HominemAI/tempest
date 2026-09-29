"""Export the local live-mode event source for the hosted service (no labels)."""
import argparse
import json
import sqlite3
from pathlib import Path

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database',type=Path)
    parser.add_argument('output',type=Path)
    args=parser.parse_args()
    with sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True) as db:
        events=[r[0] for r in db.execute('SELECT DISTINCT scenario FROM events WHERE length(trim(scenario)) BETWEEN 1 AND 1200 ORDER BY scenario')]
    if not events:raise ValueError('No events available')
    args.output.write_text(json.dumps({'version':1,'events':events},ensure_ascii=False)+'\n')
    print(f'Exported {len(events)} event texts; no labels, credentials, or personality records.')

if __name__=='__main__':main()
