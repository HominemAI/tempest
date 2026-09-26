#!/usr/bin/env python3
"""Combine the original and biochemical full-text catalogs, then run our indexer.

Source catalogs, source Markdown, annotations, and Ladybug are never modified.
Generated combined manifests make the exact indexing inputs reproducible.
"""
from argparse import Namespace, ArgumentParser
import json
import os
from pathlib import Path

import subject_index

ROOT = Path(__file__).resolve().parent


def main():
    parser = ArgumentParser(description=__doc__)
    parser.add_argument('--embedding-workers', type=int, default=1)
    parser.add_argument('--embedding-delay', type=float, default=0)
    args = parser.parse_args()
    if args.embedding_workers < 1:
        parser.error('--embedding-workers must be positive')
    if not 0 <= args.embedding_delay < float('inf'):
        parser.error('--embedding-delay must be finite and nonnegative')
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS'):
        os.environ.setdefault(name, '1')
    os.environ.setdefault('TOKENIZERS_PARALLELISM', 'false')
    papers = []
    labels = {}
    seen = set()
    for directory, catalog_name in [(ROOT, 'catalog.json'),
                                    (ROOT / 'biochemical-behavior', 'index-catalog.json'),
                                    (ROOT / 'biochemical-crime', 'index-catalog.json'),
                                    (ROOT / 'adrenaline-opioids', 'index-catalog.json')]:
        catalog = json.loads((directory / catalog_name).read_text())
        label_map = json.loads((directory / 'document-labels.json').read_text())['documents']
        for original in catalog['papers']:
            paper = dict(original)
            relative = (directory / paper['markdown']).relative_to(ROOT).as_posix()
            subject_index.safe_source(ROOT, relative)
            if relative in seen:
                raise ValueError(f'Duplicate document: {relative}')
            seen.add(relative)
            paper['markdown'] = relative
            if paper.get('file'):
                paper['file'] = (directory / paper['file']).relative_to(ROOT).as_posix()
            papers.append(paper)
            labels[relative] = label_map.get(original['markdown'], {'labels': [paper['area']]})
    catalog_path = ROOT / 'combined-index-catalog.json'
    labels_path = ROOT / 'combined-document-labels.json'
    catalog_path.write_text(json.dumps({'count': len(papers), 'papers': papers}, indent=2, ensure_ascii=False) + '\n')
    labels_path.write_text(json.dumps({'version': 1, 'documents': labels}, indent=2, ensure_ascii=False) + '\n')
    subject_index.build(Namespace(corpus=ROOT, catalog=catalog_path,
                                  vocabulary=ROOT / 'chemical-lexicon.json', labels=labels_path,
                                  context=3, title='Neurological chemicals', output=ROOT,
                                  cache=ROOT / '.models', embedding_workers=args.embedding_workers,
                                  embedding_delay=args.embedding_delay))


if __name__ == '__main__':
    main()
