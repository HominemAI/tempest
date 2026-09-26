#!/usr/bin/env python3
"""Collect curated crime/biochemistry metadata and accessible XML/HTML full texts."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
import json
import re
from pathlib import Path
import collect_biochemical_papers as collector

ROOT = Path(__file__).resolve().parent / 'biochemical-crime'


def report(items, seeds):
    by_id = {s['id']: s for s in seeds['papers']}
    labels = {}
    identities = set()
    for item in items:
        if item.get('source_html') and item.get('markdown'):
            md = ROOT / item['markdown']
            content = md.read_text()
            scanned = re.search(r'## Full text\s+(.*?)## (?:Selected )?References', content, re.S)
            if scanned and len(scanned.group(1)) < 1000:
                item['excerpt_markdown'] = item.pop('markdown')
                item.pop('markdown_sha256', None)
                item['access_status'] = 'Citation only'
                item['access_errors'].append('Scan-only HTML: abstract/references retained, excluded from full-text index')
                md.write_text(content.replace('Full text extracted from public repository HTML, not PDF.',
                    'PARTIAL TEXT ONLY: abstract and references; scanned article body is not extracted.'))
        seed = by_id[item['seed_id']]
        identity = item.get('doi') or item.get('pmid') or item['seed_id']
        assert identity not in identities, identity
        identities.add(identity)
        for key in ('evidence_scope', 'chemicals', 'screening_note'):
            item[key] = seed[key]
        item['labels'] = [ROOT.name, item['area'], item['type'],
                          item['population'], item['evidence_scope']]
        for key in ('markdown', 'metadata', 'source_xml', 'source_html'):
            if item.get(key):
                path = (ROOT / item[key]).resolve()
                assert path.is_relative_to(ROOT) and path.is_file(), path
        if item.get('markdown'):
            item['markdown_sha256'] = hashlib.sha256((ROOT / item['markdown']).read_bytes()).hexdigest()
            labels[item['markdown']] = {'labels': item['labels']}
        collector.write_json((ROOT / item['metadata']).parent / 'record.json', item)
    full = [p for p in items if p.get('markdown')]
    catalog = {'collected': str(date.today()), 'scope': seeds['scope'], 'count': len(items),
               'counts': {'articles': len(items), 'full_text_markdown': len(full),
                          'citation_only': len(items)-len(full)}, 'papers': items}
    collector.write_json(ROOT / 'catalog.json', catalog)
    collector.write_json(ROOT / 'index-catalog.json', {**catalog, 'count': len(full), 'papers': full})
    collector.write_json(ROOT / 'document-labels.json', {'version': 1, 'documents': labels})
    lines = ['# ' + seeds.get('title', 'Biochemicals, neurochemicals, and crime'), '',
             f"Collected {catalog['collected']}: **{len(items)} papers; {len(full)} local full texts; {len(items)-len(full)} citation-only records.**", '',
             '[Evidence guide](READING-GUIDE.md) · [Catalog](catalog.json) · [Access gaps](ACCESS-GAPS.md)', '',
             'Curated, non-systematic collection. Abstracts and selected full texts screened; not a complete risk-of-bias appraisal. '
             + seeds['scope'] + ' '
             'Study type/population labels are primarily bibliographic indexing, not independently adjudicated.', '',
             '## Coverage', '', '| Topic | Papers |', '| --- | ---: |']
    lines += [f'| {k} | {v} |' for k, v in sorted(Counter(p['area'] for p in items).items())]
    lines += ['', '## Papers', '', '| Year / paper | Chemicals or proxies | Outcome / scope | Screening note | Local source |',
              '| --- | --- | --- | --- | --- |']
    gaps = ['# Full-text access gaps', '', 'These records have metadata/abstracts only. Failed automated access does not establish paywall status.', '']
    for p in items:
        url = p['source_urls'][0] if p['source_urls'] else 'https://pubmed.ncbi.nlm.nih.gov/' + p['pmid'] + '/'
        title = p['title'].replace('|', '\\|')
        local = f"[MD]({p['markdown']})" if p.get('markdown') else 'Citation only'
        local += f" · [Metadata]({p['metadata']})"
        lines.append(f"| {p['year']} · [{title}]({url}) | {p['chemicals']} | {p['evidence_scope']} | {p['screening_note']} | {local} |")
        if not p.get('markdown'):
            gaps += [f"- [{title}]({url}) · [Metadata/abstract]({p['metadata']}) · [Access record]({Path(p['metadata']).with_name('record.json')})"]
    lines += ['', '## Reuse', '',
              'Full texts are repository XML/HTML-derived Markdown, not PDF conversions. Original XML/HTML is retained; figures and formatting may be omitted. '
              'Source copyright/license restrictions still apply. No access controls are bypassed.', '',
              'This collector does not rebuild the explorer, annotations, or vector index. '
              'citation-only records are excluded from `index-catalog.json`.', '',
              '```sh', f'python papers/collect_crime_papers.py --collection papers/{ROOT.name}',
              f'python papers/collect_crime_papers.py --collection papers/{ROOT.name} --html-fallback',
              f'python papers/collect_crime_papers.py --collection papers/{ROOT.name} --report', '```', '',
              'The saved seed list includes manually curated outcome labels and cautions. Downloads use the shared collector. '
              'PDF downloads are disabled for this text-first collection. `--report` only validates and regenerates local catalogs.', '']
    (ROOT / 'README.md').write_text('\n'.join(lines))
    (ROOT / 'ACCESS-GAPS.md').write_text('\n'.join(gaps) + '\n')
    print(json.dumps(catalog['counts']), flush=True)


def main():
    global ROOT
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--collection', type=Path, default=ROOT,
                        help='Collection folder containing a curated seeds.json')
    parser.add_argument('--report', action='store_true')
    parser.add_argument('--resume', action='store_true', help='Reuse saved article records; fetch only new seeds')
    parser.add_argument('--html-fallback', action='store_true')
    args = parser.parse_args()
    ROOT = args.collection.resolve()
    collector.ROOT = ROOT
    seeds = json.loads((ROOT / 'seeds.json').read_text())
    if args.report:
        items = json.loads((ROOT / 'catalog.json').read_text())['papers']
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            if args.html_fallback:
                previous = json.loads((ROOT / 'catalog.json').read_text())['papers']
                items = list(pool.map(collector.html_fallback, previous))
            else:
                def collect_seed(seed):
                    saved = ROOT / seed['topic'] / seed['id'].replace(':', '-').lower() / 'record.json'
                    if args.resume and saved.is_file():
                        return json.loads(saved.read_text())
                    return collector.collect(seed)
                items = list(pool.map(collect_seed, seeds['papers']))
    report(items, seeds)


if __name__ == '__main__':
    main()
