"""Download a small, reusable literature seed collection; no model/API calls."""
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import time
import urllib.request

DEST = Path(__file__).resolve().parent / 'emotion-labeling/datasets/public-domain-works'
BOOKS = [
    (1342, 'pride-and-prejudice', 'Pride and Prejudice', 'Jane Austen'),
    (1661, 'adventures-of-sherlock-holmes', 'The Adventures of Sherlock Holmes', 'Arthur Conan Doyle'),
    (84, 'frankenstein', 'Frankenstein; or, the Modern Prometheus', 'Mary Wollstonecraft Shelley'),
    (11, 'alice-in-wonderland', "Alice's Adventures in Wonderland", 'Lewis Carroll'),
    (514, 'little-women', 'Little Women', 'Louisa May Alcott'),
    (1400, 'great-expectations', 'Great Expectations', 'Charles Dickens'),
    (1260, 'jane-eyre', 'Jane Eyre', 'Charlotte Brontë'),
    (120, 'treasure-island', 'Treasure Island', 'Robert Louis Stevenson'),
    (2701, 'moby-dick', 'Moby Dick', 'Herman Melville'),
    (345, 'dracula', 'Dracula', 'Bram Stoker'),
    (174, 'picture-of-dorian-gray', 'The Picture of Dorian Gray', 'Oscar Wilde'),
    (33, 'scarlet-letter', 'The Scarlet Letter', 'Nathaniel Hawthorne'),
    (76, 'huckleberry-finn', 'Adventures of Huckleberry Finn', 'Mark Twain'),
    (74, 'tom-sawyer', 'The Adventures of Tom Sawyer', 'Mark Twain'),
    (45, 'anne-of-green-gables', 'Anne of Green Gables', 'L. M. Montgomery'),
    (113, 'secret-garden', 'The Secret Garden', 'Frances Hodgson Burnett'),
    (146, 'little-princess', 'A Little Princess', 'Frances Hodgson Burnett'),
    (55, 'wonderful-wizard-of-oz', 'The Wonderful Wizard of Oz', 'L. Frank Baum'),
    (35, 'time-machine', 'The Time Machine', 'H. G. Wells'),
    (36, 'war-of-the-worlds', 'The War of the Worlds', 'H. G. Wells'),
    (5230, 'invisible-man', 'The Invisible Man', 'H. G. Wells'),
    (215, 'call-of-the-wild', 'The Call of the Wild', 'Jack London'),
    (910, 'white-fang', 'White Fang', 'Jack London'),
    (271, 'black-beauty', 'Black Beauty', 'Anna Sewell'),
    (289, 'wind-in-the-willows', 'The Wind in the Willows', 'Kenneth Grahame'),
    (157, 'daddy-long-legs', 'Daddy-Long-Legs', 'Jean Webster'),
    (209, 'turn-of-the-screw', 'The Turn of the Screw', 'Henry James'),
    (160, 'awakening', 'The Awakening, and Selected Short Stories', 'Kate Chopin'),
    (550, 'silas-marner', 'Silas Marner', 'George Eliot'),
    (4276, 'north-and-south', 'North and South', 'Elizabeth Cleghorn Gaskell'),
]


def download():
    DEST.mkdir(parents=True, exist_ok=True)
    manifest = []
    for index, (book_id, slug, title, author) in enumerate(BOOKS):
        folder = DEST / slug
        folder.mkdir(exist_ok=True)
        target = folder / 'original.txt'
        catalog_url = f'https://www.gutenberg.org/ebooks/{book_id}'
        text_url = f'https://www.gutenberg.org/cache/epub/{book_id}/pg{book_id}.txt'
        catalog_file = folder / 'catalog.html'
        if not catalog_file.exists():
            time.sleep(2)
            with urllib.request.urlopen(catalog_url, timeout=60) as response:
                catalog = response.read()
            if not re.search(r'Public domain in the USA', catalog.decode('utf-8'), re.I):
                raise ValueError(f'Catalog does not confirm U.S. public-domain status: {title}')
            catalog_file.write_bytes(catalog)
        elif not re.search(r'Public domain in the USA', catalog_file.read_text(), re.I):
            raise ValueError(f'Cached catalog lacks public-domain statement: {title}')
        if not target.exists():
            if index:
                time.sleep(2)
            request = urllib.request.Request(text_url, headers={'User-Agent': 'LiteratureSeedCollection/1.0 (research download)'})
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read()
            text = data.decode('utf-8-sig')
            if '*** START OF' not in text or '*** END OF' not in text or len(data) < 10000:
                raise ValueError(f'Unexpected content for {title}')
            temporary = folder / 'original.txt.tmp'
            temporary.write_bytes(data)
            temporary.replace(target)
        data = target.read_bytes()
        header = data.decode('utf-8-sig').split('*** START OF', 1)[0]
        actual_title = re.search(r'^Title: (.+)', header, re.M)
        norm = lambda value: re.sub(r'\W+', '', value.casefold())
        if not actual_title or not norm(actual_title.group(1)).startswith(norm(title)):
            raise ValueError(f'Wrong source title downloaded for {title}; inspect and quarantine before retrying')
        metadata_path = folder / 'source.json'
        previous = json.loads(metadata_path.read_text()) if metadata_path.exists() else {}
        metadata = dict(title=title, author=author, language='English', translation=False,
                        gutenberg_id=book_id, catalog_url=catalog_url, download_url=text_url,
                        copyright_status='Public domain in the USA (Project Gutenberg catalog)',
                        jurisdiction='United States; not independently cleared worldwide',
                        license_url='https://www.gutenberg.org/policy/license.html',
                        catalog_evidence_path=catalog_file.relative_to(DEST).as_posix(),
                        original_path=target.relative_to(DEST).as_posix(), bytes=len(data),
                        sha256=hashlib.sha256(data).hexdigest(),
                        downloaded_at=previous.get('downloaded_at', datetime.now(timezone.utc).isoformat()),
                        preservation='Complete original file, including Gutenberg notices and license; no cleaning applied')
        metadata_path.write_text(json.dumps(metadata, indent=2) + '\n')
        manifest.append(metadata)
        # Preserve progress if a later download fails; originals are never overwritten.
        previous_manifest = DEST / 'manifest.json'
        existing = json.loads(previous_manifest.read_text()) if previous_manifest.exists() else []
        by_id = {item['gutenberg_id']: item for item in existing}
        by_id[book_id] = metadata
        previous_manifest.write_text(json.dumps(list(by_id.values()), indent=2) + '\n')
        print(f'{title}: {len(data):,} bytes', flush=True)
    (DEST / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(f'Saved {len(manifest)} works in {DEST}')


if __name__ == '__main__':
    download()
