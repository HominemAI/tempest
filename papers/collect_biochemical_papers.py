#!/usr/bin/env python3
"""Fetch a curated seed list using Europe PMC; preserve access gaps explicitly.

No paywall bypass. Source XML is retained; PDF downloads are accepted only after
magic-byte and parser validation. Existing source files are reused, not replaced.
"""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent / 'biochemical-behavior'
API = 'https://www.ebi.ac.uk/europepmc/webservices/rest/'
LOCK = threading.Lock()
LAST_REQUEST = 0.0


class ArticleText(HTMLParser):
    """Readable fallback for ordinary public PMC article HTML, not security pages."""
    def __init__(self):
        super().__init__()
        self.active = False
        self.skip = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag == 'article':
            self.active = True
        if not self.active:
            return
        if tag in ('script', 'style', 'button', 'svg'):
            self.skip += 1
        if self.skip:
            return
        if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.parts.append('\n\n' + '#' * int(tag[1]) + ' ')
        elif tag in ('p', 'section', 'div', 'tr', 'figure'):
            self.parts.append('\n\n')
        elif tag == 'li':
            self.parts.append('\n- ')
        elif tag in ('td', 'th'):
            self.parts.append(' | ')
        elif tag == 'br':
            self.parts.append('\n')

    def handle_endtag(self, tag):
        if tag == 'article':
            self.active = False
        if self.skip and tag in ('script', 'style', 'button', 'svg'):
            self.skip -= 1
        if self.active and not self.skip and tag in ('p', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
            self.parts.append('\n\n')

    def handle_data(self, data):
        if self.active and not self.skip:
            self.parts.append(re.sub(r'\s+', ' ', data))


def html_fallback(item):
    if item['access_status'] != 'Citation only' or not item.get('pmcid'):
        return item
    folder = ROOT / item['metadata']
    folder = folder.parent
    url = 'https://pmc.ncbi.nlm.nih.gov/articles/' + item['pmcid'] + '/'
    try:
        raw = fetch(url)
        parser = ArticleText()
        parser.feed(raw.decode('utf-8'))
        content = re.sub(r'\n[ \t]+', '\n', ''.join(parser.parts))
        content = re.sub(r'\n{3,}', '\n\n', content).strip()
        if len(content) < 5000 or item['pmcid'] not in content or 'References' not in content:
            raise ValueError('No verified full article in response (possibly access challenge)')
        scanned = re.search(r'## Full text\s+(.*?)## (?:Selected )?References', content, re.S)
        if scanned and len(scanned.group(1)) < 1000:
            raise ValueError('Scan-only article: HTML contains abstract/references but no article body')
        (folder / 'source.html').write_bytes(raw)
        md_path = folder / (folder.name + '.md')
        if not md_path.exists():
            md_path.write_text('# ' + item['title'] + '\n\n[Source](' + url + ')\n\n'
                              '> Full text extracted from public repository HTML, not PDF. '
                              'Figures are not embedded; tables, equations, and inline formatting '
                              'may be simplified. No PDF page numbers. Source copyright applies.\n\n'
                              + content + '\n')
        item['source_html'] = str((folder / 'source.html').relative_to(ROOT))
        item['source_html_sha256'] = hashlib.sha256(raw).hexdigest()
        item['markdown'] = str(md_path.relative_to(ROOT))
        item['access_status'] = 'HTML full text'
    except Exception as exc:
        item['access_errors'].append('HTML: ' + str(exc))
    write_json(folder / 'record.json', item)
    print(item['access_status'] + ': ' + item['seed_id'], flush=True)
    return item


def fetch(url):
    global LAST_REQUEST
    with LOCK:
        time.sleep(max(0, .3 - (time.monotonic() - LAST_REQUEST)))
        LAST_REQUEST = time.monotonic()
    req = urllib.request.Request(url, headers={'User-Agent': 'ResearchLibrary/1.0 (academic literature collection)'})
    with urllib.request.urlopen(req, timeout=20) as response:
        data = response.read(40 * 1024 * 1024 + 1)
    if len(data) > 40 * 1024 * 1024:
        raise ValueError('Download exceeds 40 MiB limit')
    return data


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n')


def text(element):
    return re.sub(r'\s+', ' ', ''.join(element.itertext())).strip() if element is not None else ''


def render_section(element, level=2):
    parts = []
    for child in element:
        tag = child.tag.split('}')[-1]
        if tag == 'title':
            parts.append('#' * min(level, 6) + ' ' + text(child))
        elif tag == 'sec':
            parts.append(render_section(child, level + 1))
        elif tag in ('p', 'disp-quote', 'statement', 'disp-formula'):
            parts.append(text(child))
        elif tag == 'list':
            parts.extend('- ' + text(item) for item in child.findall('list-item'))
        elif tag in ('fig', 'table-wrap'):
            parts.append('**' + text(child.find('label')) + '** ' + text(child.find('caption')))
            table = child.find('.//table')
            if table is not None:
                parts.extend(' | '.join(text(cell) for cell in row) for row in table.findall('.//tr'))
            if tag == 'fig':
                parts.append('[Figure image not embedded; consult the original source.]')
        elif tag == 'ref-list':
            parts.append('## References')
            parts.extend(text(ref) for ref in child.findall('ref'))
        elif tag not in ('label',):
            parts.append(render_section(child, level))
    return '\n\n'.join(p for p in parts if p)


def collect(seed):
    folder = ROOT / seed['topic'] / seed['id'].replace(':', '-').lower()
    folder.mkdir(parents=True, exist_ok=True)
    metadata_path = folder / 'metadata.json'
    previous_path = folder / 'record.json'
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    if metadata_path.exists():
        record = json.loads(metadata_path.read_text())
    else:
        query = ('EXT_ID:' + seed['id'][4:] + ' AND SRC:MED' if seed['id'].startswith('MED:')
                 else 'EXT_ID:' + seed['id'] + ' AND SRC:PMC')
        # PMCID search retrieves the richer MED record where present.
        if seed['id'].startswith('PMC'):
            query = 'PMCID:' + seed['id']
        url = API + 'search?' + urllib.parse.urlencode({'query': query, 'format': 'json', 'resultType': 'core'})
        matches = json.loads(fetch(url))['resultList']['result']
        if not matches:
            raise ValueError('No metadata: ' + seed['id'])
        record = next((r for r in matches if r['source'] == 'MED'), matches[0])
        write_json(metadata_path, record)
    mesh = [item['descriptorName'] for item in record.get('meshHeadingList', {}).get('meshHeading', [])]
    pubtypes = record.get('pubTypeList', {}).get('pubType', [])
    lower_types = ' '.join(pubtypes).lower()
    title = record['title']
    if 'meta-analysis' in lower_types or 'meta-analysis' in title.lower():
        kind = 'Systematic review / meta-analysis'
    elif 'review' in lower_types or 'review' in title.lower():
        kind = 'Review'
    elif 'comment' in lower_types or 'letter' in lower_types:
        kind = 'Commentary / correspondence'
    elif 'randomized controlled trial' in lower_types:
        kind = 'Randomized controlled trial'
    else:
        kind = 'Research article (design requires full-text review)'
    population = ('Human and animal evidence' if 'Humans' in mesh and 'Animals' in mesh else
                  'Human' if 'Humans' in mesh else 'Animal' if 'Animals' in mesh else
                  'Not established from indexing; inspect full text')
    pmcid = record.get('pmcid')
    item = {'seed_id': seed['id'], 'area': seed['topic'], 'title': title,
            'authors': record.get('authorString', ''), 'year': int(record['pubYear']),
            'type': kind, 'publication_types': pubtypes, 'population': population,
            'population_basis': 'Europe PMC MeSH indexing; not manual species confirmation',
            'doi': record.get('doi'), 'pmid': record.get('pmid'), 'pmcid': pmcid,
            'journal': record.get('journalInfo', {}).get('journal', {}).get('title'),
            'chemicals_indexed': [c['name'] for c in record.get('chemicalList', {}).get('chemical', [])],
            'mesh_terms': mesh, 'license': record.get('license', 'See source permissions'),
            'source_urls': ['https://doi.org/' + record['doi']] if record.get('doi') else [],
            'metadata': str(metadata_path.relative_to(ROOT)), 'access_errors': []}
    if pmcid:
        item['source_urls'].append('https://pmc.ncbi.nlm.nih.gov/articles/' + pmcid + '/')
        xml_path = folder / 'source.xml'
        xml_url = API + pmcid + '/fullTextXML'
        try:
            xml_data = xml_path.read_bytes() if xml_path.exists() else fetch(xml_url)
            article = ET.fromstring(xml_data)
            if article.find('body') is None:
                raise ValueError('No article body in XML response')
            if not xml_path.exists():
                xml_path.write_bytes(xml_data)
            item['source_xml'] = str(xml_path.relative_to(ROOT))
            item['source_xml_sha256'] = hashlib.sha256(xml_data).hexdigest()
            item['source_urls'].append(xml_url)
            md_path = folder / (folder.name + '.md')
            if not md_path.exists():
                abstract = article.find('.//article-meta/abstract')
                md = ('# ' + title + '\n\n' + item['authors'] + ' (' + str(item['year']) + ')\n\n'
                      + '[Source](' + item['source_urls'][0] + ') · [Original JATS XML](source.xml)\n\n'
                      + '> Full text converted from repository JATS XML, not PDF. No PDF page numbers. '
                      + 'Figure images are omitted; table layout, equations, and inline formatting may be simplified. '
                      + 'Check the source for exact formatting and permissions.\n\n'
                      + '## Abstract\n\n' + text(abstract) + '\n\n'
                      + render_section(article.find('body')) + '\n\n'
                      + (render_section(article.find('back')) if article.find('back') is not None else '') + '\n')
                md_path.write_text(md)
            item['markdown'] = str(md_path.relative_to(ROOT))
        except Exception as exc:
            item['access_errors'].append('XML: ' + str(exc))
    pdf_path = folder / (folder.name + '.pdf')
    urls = seed.get('pdf_urls', [])[:]
    doi = record.get('doi', '')
    if doi.startswith('10.3389/'):
        urls.append('https://www.frontiersin.org/articles/' + doi + '/pdf')
    if doi.startswith('10.1371/journal.'):
        journal = doi.split('journal.')[1].split('.')[0]
        urls.append('https://journals.plos.org/plos' + journal + '/article/file?id=' + doi + '&type=printable')
    if doi.startswith('10.1038/'):
        urls.append('https://www.nature.com/articles/' + doi.split('/')[1] + '.pdf')
    urls.extend(u['url'].replace('http://', 'https://', 1) for u in record.get('fullTextUrlList', {}).get('fullTextUrl', [])
                if u.get('documentStyle') == 'pdf' and u.get('availabilityCode') in ('F', 'OA', 'F6')
                and 'europepmc.org' not in u['url'])
    for url in ([] if seed.get('skip_pdf') else dict.fromkeys(urls)):
        if pdf_path.exists():
            break
        try:
            data = fetch(url)
            if not data.startswith(b'%PDF-'):
                raise ValueError('Not a PDF')
            import pymupdf
            with pymupdf.open(stream=data, filetype='pdf') as pdf:
                if not len(pdf) or not pdf[0].get_text().strip():
                    raise ValueError('PDF lacks readable first page')
                item['pages'] = len(pdf)
            pdf_path.write_bytes(data)
            item['downloaded_pdf_url'] = url
        except Exception as exc:
            item['access_errors'].append('PDF ' + url + ': ' + str(exc))
    if pdf_path.exists():
        import pymupdf
        with pymupdf.open(pdf_path) as pdf:
            item['pages'] = len(pdf)
        item['file'] = str(pdf_path.relative_to(ROOT))
        item['bytes'] = pdf_path.stat().st_size
        item['pdf_sha256'] = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    for key in ('markdown', 'source_html', 'source_html_sha256', 'downloaded_pdf_url'):
        if key not in item and key in previous:
            item[key] = previous[key]
    item['access_status'] = ('PDF and XML' if item.get('file') and item.get('source_xml') else
                            'PDF with Markdown' if item.get('file') and item.get('markdown') else
                            'PDF' if item.get('file') else 'XML full text' if item.get('source_xml') else
                            'HTML full text' if item.get('source_html') else 'Citation only')
    write_json(folder / 'record.json', item)
    print(item['access_status'] + ': ' + seed['id'] + ' ' + title[:85], flush=True)
    return item


def report(catalog):
    """Generate local navigation and a full-text-only catalog for the indexer."""
    papers = catalog['papers']
    assert len({p.get('doi') or p['seed_id'] for p in papers}) == len(papers), 'Duplicate articles'
    labels = {}
    for item in papers:
        if item.get('source_xml'):
            article = ET.parse(ROOT / item['source_xml']).getroot()
            if article.get('article-type') == 'review-article' and item['type'].startswith('Research article'):
                item['type'] = 'Review'
        if item['seed_id'] == 'PMC11461476':
            item['population'] = 'Animal (mice)'
            item['population_basis'] = 'Explicit in article title; species verified during screening'
        if item['seed_id'] in ('PMC3122071', 'PMC3181680'):
            item['type'] = 'Review'
        if item['seed_id'] == 'MED:28612993':
            item['type'] = 'Systematic review'
        item['labels'] = ['biochemical-behavior', item['area'], item['type'], item['population']]
        for key in ('file', 'markdown', 'source_xml', 'source_html', 'metadata'):
            if item.get(key):
                assert (ROOT / item[key]).is_file(), item[key]
        if item.get('markdown'):
            labels[item['markdown']] = {'labels': item['labels']}
            item['markdown_sha256'] = hashlib.sha256((ROOT / item['markdown']).read_bytes()).hexdigest()
        write_json((ROOT / item['metadata']).parent / 'record.json', item)
    full = [p for p in papers if p.get('markdown')]
    pdfs = [p for p in papers if p.get('file')]
    gaps = [p for p in papers if not p.get('markdown')]
    catalog['counts'] = {'articles': len(papers), 'full_text_markdown': len(full),
                         'pdfs': len(pdfs), 'citation_only': len(gaps)}
    write_json(ROOT / 'catalog.json', catalog)
    write_json(ROOT / 'index-catalog.json', {**catalog, 'count': len(full), 'papers': full})
    write_json(ROOT / 'document-labels.json', {'version': 1,
               'note': 'Topic labels curated; study type primarily publication indexing; population is a MeSH-derived screening aid unless explicitly stated otherwise.',
               'documents': labels})
    lines = ['# Biochemical influences on behavior: paper collection', '',
             f"Collected {catalog['collected']}. **{len(papers)} distinct articles: {len(full)} local full texts, including {len(pdfs)} PDFs; {len(gaps)} citation-only entries.**",
             '', 'The PDF count is a subset, not an additional set. XML/HTML full text is not presented as PDF conversion.', '',
             '[Reading guide and evidence cautions](READING-GUIDE.md) · [Complete catalog](catalog.json) · [Access gaps](ACCESS-GAPS.md)', '',
             'General-purpose selection spanning neurotransmitters, peptides, hormones, inflammation, and metabolic signals. '
             'Reviews, experiments, methods papers, null replications, and a published critique are distinguished. '
             'This is not an exhaustive or systematic review; abstracts and selected full texts were screened, not every study critically appraised.', '',
             '## Coverage', '', '| Topic | Articles | Local full texts |', '| --- | ---: | ---: |']
    topics = sorted({p['area'] for p in papers})
    for topic in topics:
        group = [p for p in papers if p['area'] == topic]
        lines.append(f"| {topic} | {len(group)} | {sum(bool(p.get('markdown')) for p in group)} |")
    lines += ['', '## Papers', '',
              'Population labels below come primarily from MeSH indexing and can omit species discussed in a review. '
              'They are screening aids, not a substitute for checking the methods. Publication year follows bibliographic indexing; online-first years may differ.', '']
    for topic in topics:
        lines += ['### ' + topic, '', '| Year | Paper | Type | Population (index-derived) | Local files |', '| --- | --- | --- | --- | --- |']
        for p in papers:
            if p['area'] != topic:
                continue
            local = ' · '.join(f'[{label}]({p[key]})' for key, label in [('markdown', 'MD'), ('file', 'PDF'), ('metadata', 'metadata')] if p.get(key))
            title = p['title'].replace('|', '\\|')
            url = p['source_urls'][0] if p['source_urls'] else 'https://pubmed.ncbi.nlm.nih.gov/' + p['pmid'] + '/'
            lines.append(f"| {p['year']} | [{title}]({url}) | {p['type']} | {p['population']} | {local} |")
        lines.append('')
    lines += ['## Reuse and provenance', '',
              '- Each article has its own folder, repository metadata, and an access record. Source XML/HTML is retained where downloaded.',
              '- XML/HTML-derived Markdown has no PDF page numbers. Figures are omitted; tables, equations, and formatting may be simplified. Check the original.',
              '- Three institutional PDFs were converted with the existing local PDF converter. PDFs with existing XML-derived Markdown retain that version.',
              '- Full-text-only `index-catalog.json` and `document-labels.json` are ready for the existing subject indexer. Citation-only records are deliberately excluded.',
              '- The existing explorer, original 52-paper catalog, SQLite annotations, and Ladybug store have not been changed or rebuilt.',
              '- Copyright and license conditions remain those of each source. Local access does not grant unrestricted redistribution or model-training rights.',
              '- Counts refer to distinct articles, not independent participant samples. Related reports and reviews can overlap.', '',
              'The reusable collector is [collect_biochemical_papers.py](../collect_biochemical_papers.py). '
              '`seeds.json` records the selected identifiers; `author-pdfs.json` records supplemental institutional sources. '
              'Install the existing `converter-requirements.txt` for PDF validation/conversion, then run:', '',
              '```sh', 'python papers/collect_biochemical_papers.py',
              'python papers/collect_biochemical_papers.py --html-fallback',
              'python papers/collect_biochemical_papers.py --author-pdfs',
              'python papers/collect_biochemical_papers.py --report', '```', '',
              '`--report` is local-only. Downloads respect access errors; none are bypassed. '
              'Do not run the old batch PDF converter with `--force` over this collection: that would replace XML/HTML-derived Markdown and its line references.', '']
    (ROOT / 'README.md').write_text('\n'.join(lines))
    gap_lines = ['# Full-text access gaps', '',
                 f'{len(gaps)} of {len(papers)} selected papers have metadata but no verified local full text. '
                 'A failed automated request is not proof of paywall status or unavailability to a reader.', '']
    for p in gaps:
        gap_lines += ['## ' + p['title'], '', f"{p['authors']} ({p['year']}). {p['type']}.", '',
                      ' · '.join('[Source](' + u + ')' for u in p['source_urls']), '',
                      '[Metadata and abstract](' + p['metadata'] + ') · [Access attempts](' + str(Path(p['metadata']).with_name('record.json')) + ')', '']
    (ROOT / 'ACCESS-GAPS.md').write_text('\n'.join(gap_lines))
    print(json.dumps(catalog['counts'], indent=2))


def main():
    seeds = json.loads((ROOT / 'seeds.json').read_text())
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workers', type=int, default=3)
    parser.add_argument('--html-fallback', action='store_true', help='Try ordinary public PMC pages for citation-only records')
    parser.add_argument('--author-pdfs', action='store_true', help='Try curated public institutional/lab PDF links')
    parser.add_argument('--report', action='store_true', help='Validate local file references and regenerate catalogs/navigation')
    args = parser.parse_args()
    if args.report:
        report(json.loads((ROOT / 'catalog.json').read_text()))
        return
    with ThreadPoolExecutor(max_workers=max(1, min(args.workers, 3))) as pool:
        if args.author_pdfs:
            results = json.loads((ROOT / 'catalog.json').read_text())['papers']
            links = json.loads((ROOT / 'author-pdfs.json').read_text())
            for item in results:
                if item['seed_id'] not in links or item.get('file'):
                    continue
                replacement = collect({'id': item['seed_id'], 'topic': item['area'], 'pdf_urls': links[item['seed_id']]})
                item.update(replacement)
                if item.get('file') and not item.get('markdown'):
                    from convert_papers import convert_one
                    pdf = ROOT / item['file']
                    conversion = convert_one(str(pdf), str(pdf), item['title'], False)
                    item['markdown'] = str(pdf.with_suffix('.md').relative_to(ROOT))
                    item['access_status'] = 'PDF with page-delimited Markdown'
                    write_json(pdf.parent / 'record.json', item)
        elif args.html_fallback:
            existing = json.loads((ROOT / 'catalog.json').read_text())['papers']
            results = list(pool.map(html_fallback, existing))
        else:
            results = list(pool.map(collect, seeds['papers']))
    catalog = {'collected': str(date.today()), 'scope': seeds['scope'], 'count': len(results), 'papers': results}
    write_json(ROOT / 'catalog.json', catalog)
    print(json.dumps(dict(Counter(p['access_status'] for p in results)), indent=2))


if __name__ == '__main__':
    main()
