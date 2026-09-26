"""Sample reproducible, non-overlapping passages from downloaded literature.

Defaults: 100 total, balanced by book, 60–180 words, up to three paragraphs.
Produces passage-only TXT files grouped by book, plus separate provenance JSONL.
Use --format md for the earlier annotated Markdown layout.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import re

BASE = Path(__file__).resolve().parent / 'emotion-labeling/datasets/public-domain-works'
# Verified opening lines for the downloaded editions; skip prefaces and contents.
OPENINGS = {
    1342: 'It is a truth universally acknowledged',
    1661: 'To Sherlock Holmes she is always',
    84: 'You will rejoice to hear that no disaster',
    11: 'Alice was beginning to get very tired',
    514: 'Christmas won’t be Christmas without any presents',
    1400: 'My father’s family name being Pirrip',
    1260: 'There was no possibility of taking a walk that day',
    120: 'Squire Trelawney, Dr. Livesey',
    2701: 'Call me Ishmael.',
    345: '_3 May. Bistritz._',
    174: 'The studio was filled with the rich odour of roses',
    33: 'A throng of bearded men',
    76: 'You don’t know about me without you have read a book',
    74: '“Tom!”',
    45: 'MRS. Rachel Lynde lived just where',
    113: 'When Mary Lennox was sent to Misselthwaite Manor',
    146: "Once on a dark winter's day",
    55: 'Dorothy lived in the midst of the great Kansas prairies',
    35: 'The Time Traveller (for so it will be convenient',
    36: 'No one would have believed',
    215: 'Buck did not read the newspapers',
    910: 'Dark spruce forest frowned on either side',
    271: 'The first place that I can well remember',
    289: 'The Mole had been working very hard',
    209: 'The story had held us, round the fire',
    160: 'A green and yellow parrot',
    550: 'In the days when the spinning-wheels hummed',
    4276: '“Edith!” said Margaret, gently',
    5230: 'The stranger came early in February',
    157: 'The first Wednesday in every month',
}


def paragraphs(lines, book_id):
    opening = OPENINGS.get(book_id)
    if not opening:
        raise ValueError(f'Add a verified narrative opening for book {book_id}')
    start = next((i for i, line in enumerate(lines) if opening in line), None)
    end = next((i for i, line in enumerate(lines) if line.startswith('*** END OF')), None)
    if start is None or end is None or start >= end:
        raise ValueError('Could not locate narrative boundaries')
    blocks, current = [], []
    for i in range(start, end + 1):
        if i == end or not lines[i].strip():
            if current:
                text = ' '.join(lines[n].strip() for n in current)
                # Treat headings/illustrations as barriers, not text to concatenate across.
                words = text.split()
                eligible = (len(words) >= 5 and not text.isupper()
                            and not any(c in text for c in '[]')
                            and not re.match(r'^(chapter|letter|contents|illustration)\b', text, re.I)
                            and 'gutenberg' not in text.casefold()
                            and not re.fullmatch(r'[\W\d_IVXLCDM]+', text))
                blocks.append(dict(start_line=current[0] + 1, end_line=current[-1] + 1,
                                   text=text, words=len(words), eligible=eligible))
                current = []
        else:
            current.append(i)
    return blocks


def candidates(blocks, minimum, maximum, max_paragraphs):
    result = []
    for i in range(len(blocks)):
        selected, count = [], 0
        for block in blocks[i:i + max_paragraphs]:
            if not block['eligible']:
                break
            selected.append(block)
            count += block['words']
            if count > maximum:
                break
            if count >= minimum:
                result.append(dict(start_line=selected[0]['start_line'], end_line=block['end_line'],
                                   text='\n\n'.join(p['text'] for p in selected),
                                   word_count=count, paragraph_count=len(selected)))
                break
    return result


def choose(pool, count, rng):
    shuffled = list(pool)
    rng.shuffle(shuffled)
    selected = []
    for item in shuffled:
        if any(item['start_line'] <= old['end_line'] and old['start_line'] <= item['end_line'] for old in selected):
            continue
        selected.append(item)
        if len(selected) == count:
            return selected
    raise ValueError(f'Only {len(selected)} non-overlapping candidates available; requested {count}')


def run(args):
    if args.count < 1 or args.min_words < 1 or args.max_words < args.min_words or args.max_paragraphs < 1:
        raise ValueError('Invalid sampling bounds')
    if args.out.exists():
        raise ValueError('Output folder already exists; choose a new --out to preserve it')
    books = json.loads((BASE / 'manifest.json').read_text())
    if args.per_book is not None:
        if args.per_book < 1:
            raise ValueError('--per-book must be positive')
        args.count = args.per_book * len(books)
    if args.count < len(books):
        raise ValueError('Balanced sampling requires at least one passage per book')
    rng = random.Random(args.seed)
    quotas = [args.count // len(books)] * len(books)
    for i in rng.sample(range(len(books)), args.count % len(books)):
        quotas[i] += 1
    results, stats = [], []
    reused = {}
    if args.reuse_samples:
        for line in args.reuse_samples.read_text().splitlines():
            item = json.loads(line)
            reused.setdefault(item['gutenberg_id'], []).append(item)
    for book, quota in zip(books, quotas):
        source = BASE / book['original_path']
        raw = source.read_bytes()
        fingerprint = hashlib.sha256(raw).hexdigest()
        if fingerprint != book['sha256']:
            raise ValueError(f'Source changed: {source}')
        lines = raw.decode('utf-8-sig').splitlines()
        if book['gutenberg_id'] in reused:
            selected = reused[book['gutenberg_id']]
            if len(selected) != quota or any(item['source_sha256'] != fingerprint for item in selected):
                raise ValueError('Reused samples do not match quota or current source')
            for item in selected:
                if item['original_text'] != '\n'.join(lines[item['start_line'] - 1:item['end_line']]):
                    raise ValueError('Reused sample line provenance mismatch')
            results.extend(dict(item) for item in selected)
            stats.append(dict(title=book['title'], selected=len(selected), reused=True))
            continue
        pool = candidates(paragraphs(lines, book['gutenberg_id']), args.min_words, args.max_words, args.max_paragraphs)
        selected = choose(pool, quota, rng)
        stats.append(dict(title=book['title'], candidates=len(pool), selected=len(selected)))
        for passage in selected:
            results.append(dict(**passage, title=book['title'], author=book['author'],
                                gutenberg_id=book['gutenberg_id'], source_path=book['original_path'],
                                source_url=book['catalog_url'], source_sha256=fingerprint,
                                copyright_status=book['copyright_status'],
                                original_text='\n'.join(lines[passage['start_line'] - 1:passage['end_line']])))
    rng.shuffle(results)
    args.out.mkdir(parents=True)
    if args.format == 'md':
        (args.out / 'passages').mkdir()
    combined = ['# Random literature passages', '', f'{len(results)} passages; random seed {args.seed}.', '',
                'Source excerpts, not generated events. Selection is balanced by book, not uniform over all words.', '',
                '[Project Gutenberg license](https://www.gutenberg.org/policy/license.html). '
                'Original files retain full notices; check redistribution conditions before sharing.', '']
    book_counts = {}
    for number, item in enumerate(results, 1):
        pid = f'passage-{number:03d}'
        item.update(id=pid, document_path=f'passages/{pid}.md')
        if args.format == 'txt':
            slug = Path(item['source_path']).parent.name
            book_counts[slug] = book_counts.get(slug, 0) + 1
            pid = f'{slug}-{book_counts[slug]:03d}'
            item.update(id=pid, document_path=f'{slug}/passage-{book_counts[slug]:03d}.txt')
            target = args.out / item['document_path']
            target.parent.mkdir(exist_ok=True)
            target.write_text(item['text'] + '\n', encoding='utf-8')
            continue
        section = [f'## {pid} — {item["title"]}', '',
                   f'Author: {item["author"]} · Source lines: {item["start_line"]}–{item["end_line"]} · Words: {item["word_count"]}', '',
                   f'[Source edition]({item["source_url"]}) · [License](https://www.gutenberg.org/policy/license.html)', '',
                   item['text'], '']
        (args.out / item['document_path']).write_text('\n'.join(section))
        combined.extend(section + ['---', ''])
    if args.format == 'md':
        (args.out / 'passages.md').write_text('\n'.join(combined))
    with (args.out / 'passages.jsonl').open('w') as output:
        for item in results:
            output.write(json.dumps(item, ensure_ascii=False) + '\n')
    metadata = dict(created_at=datetime.now(timezone.utc).isoformat(), seed=args.seed, count=len(results),
                    min_words=args.min_words, max_words=args.max_words, max_paragraphs=args.max_paragraphs,
                    output_format=args.format, reused_samples=str(args.reuse_samples) if args.reuse_samples else None,
                    sampling='Balanced per book; shuffled candidate starts; reject overlapping source line spans',
                    books=stats, limitations='Heuristic filtering; excerpts may depend on earlier context. No emotional-content filtering.',
                    source_root=str(BASE), source_line_numbering='1-based inclusive, decoded original.txt lines')
    (args.out / 'manifest.json').write_text(json.dumps(metadata, indent=2) + '\n')
    print(json.dumps(dict(output=str(args.out), count=len(results), books=stats), indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--per-book', type=int, help='Select this many per book, overriding --count')
    parser.add_argument('--format', choices=['txt', 'md'], default='txt')
    parser.add_argument('--reuse-samples', type=Path, help='Preserve selections from a previous passages.jsonl for matching books')
    parser.add_argument('--seed', type=int, default=42)
    parser.add_argument('--min-words', type=int, default=60)
    parser.add_argument('--max-words', type=int, default=180)
    parser.add_argument('--max-paragraphs', type=int, default=3)
    parser.add_argument('--out', type=Path, default=BASE / 'passages-txt-seed42')
    arguments = parser.parse_args()
    try:
        run(arguments)
    except ValueError as exc:
        parser.error(str(exc))
