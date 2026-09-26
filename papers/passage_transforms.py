"""Local, reproducible passage transformations; no hosted model calls."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
import re

BASE = Path(__file__).resolve().parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def apply_edits(text, edits):
    """Keep exact source/output offsets; leave all other bytes of text alone."""
    output, cursor, length = [], 0, 0
    for edit in sorted(edits, key=lambda e: e['start']):
        start, end = edit['start'], edit['end']
        if not cursor <= start < end <= len(text):
            raise ValueError('Overlapping or invalid edit')
        if text[start:end] != edit['original']:
            raise ValueError('Source span mismatch')
        unchanged = text[cursor:start]
        output.extend([unchanged, edit['replacement']])
        length += len(unchanged)
        edit['output_start'] = length
        length += len(edit['replacement'])
        edit['output_end'] = length
        cursor = end
    output.append(text[cursor:])
    return ''.join(output)


def person_name(text):
    return re.sub(r"(?:['’]s|['’])$", '', text.strip())


def plausible_person(entity):
    # NER alone labels some ordinary words as people in historical prose.
    return entity.label_ == 'PERSON' and any(
        t.pos_ == 'PROPN' and t.text[:1].isupper() for t in entity)


def name_mapping(names, aliases=None):
    """Exact names plus unambiguous short forms; never guess shared surnames."""
    aliases = aliases or {}
    names = list(dict.fromkeys(person_name(n) for n in names if person_name(n)))
    names.extend(n for n in aliases if n not in names)
    canonical = {}
    for name in names:
        if name in aliases:
            canonical[name] = aliases[name]
            continue
        # Only link a one-word name to a unique untitled full name. Titles can
        # distinguish family members (Mr. Bennet / Mrs. Bennet), so don't merge.
        candidates = [n for n in names if len(n.split()) > 1 and name in n.split()
                      and not re.match(r'^(Mr|Mrs|Ms|Miss|Dr|Sir|Lady|Lord|Captain)\.?\s', n)]
        canonical[name] = candidates[0] if len(name.split()) == 1 and len(candidates) == 1 else name
    ids, mapping = {}, {}
    for name in names:
        identity = canonical[name]
        if identity not in ids:
            ids[identity] = f'person{len(ids) + 1}'
        mapping[name] = ids[identity]
    return mapping


def name_edits(text, mapping):
    if not mapping:
        return []
    pattern = r'(?<!\w)(?:' + '|'.join(re.escape(n) for n in sorted(mapping, key=len, reverse=True)) + r')(?!\w)'
    return [dict(start=m.start(), end=m.end(), original=m.group(), replacement=mapping[m.group()])
            for m in re.finditer(pattern, text)]


def verb_dictionary(path=None):
    if path:
        words = Path(path).read_text().splitlines()
    else:
        import nltk
        nltk.data.path.insert(0, str(BASE / '.nltk-data'))
        from nltk.corpus import wordnet
        words = wordnet.all_lemma_names(pos='v')
    # Single-word dictionary verbs only; avoid auxiliary replacements.
    return sorted({w.strip().lower() for w in words if re.fullmatch('[a-zA-Z]+', w.strip())}
                  - {'be', 'have', 'do'})


def verb_edits(doc, words, rng, inflect):
    edits = []
    for token in doc:
        if (token.pos_ != 'VERB' or token.ent_type_ or not token.text.isalpha()
                or token.lemma_.lower() == 'be' or re.fullmatch(r'person\d+', token.text)):
            continue
        candidates = [w for w in words if w != token.lemma_.lower()]
        rng.shuffle(candidates)
        for lemma in candidates:
            forms = inflect(lemma, tag=token.tag_)
            if not forms:
                continue
            replacement = forms[0]
            if replacement.lower() == token.text.lower():
                continue
            if token.text.isupper():
                replacement = replacement.upper()
            elif token.text[0].isupper():
                replacement = replacement.capitalize()
            edits.append(dict(start=token.idx, end=token.idx + len(token.text),
                              original=token.text, replacement=replacement,
                              original_lemma=token.lemma_, replacement_lemma=lemma, tag=token.tag_))
            break
        else:
            raise ValueError(f'No usable replacement for {token.text!r} ({token.tag_})')
    return edits


def run(mode):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Directory of TXT passages, recursively')
    parser.add_argument('--output', required=True, type=Path, help='New output directory (must not exist)')
    parser.add_argument('--model', default='en_core_web_sm')
    if mode == 'verbs':
        parser.add_argument('--seed', type=int, default=42)
        parser.add_argument('--dictionary', type=Path, help='Optional verb lemmas, one per line; default WordNet')
    else:
        parser.add_argument('--aliases', type=Path, help='Optional JSON: {book_folder: {alias: canonical_name}}')
    args = parser.parse_args()
    source, dest = args.input.resolve(), args.output.resolve()
    if not source.is_dir() or dest.exists() or dest.is_relative_to(source) or source.is_relative_to(dest):
        raise ValueError('Use an existing input directory and a separate, new output directory')
    paths = sorted(source.rglob('*.txt'))
    if not paths or any(not p.resolve().is_relative_to(source) for p in paths):
        raise ValueError('No TXT files, or a source symlink leaves the input directory')
    import spacy
    nlp = spacy.load(args.model)
    texts = [p.read_text() for p in paths]
    docs = list(nlp.pipe(texts, batch_size=32))
    rels = [p.relative_to(source).as_posix() for p in paths]
    groups = [Path(rel).parent.as_posix() for rel in rels]
    mappings, rejected = {}, []
    if mode == 'names':
        aliases = json.loads(args.aliases.read_text()) if args.aliases else {}
        names = defaultdict(list)
        for rel, group, doc in zip(rels, groups, docs):
            names[group].extend(e.text for e in doc.ents if plausible_person(e))
            rejected.extend(dict(document_path=rel, text=e.text, start=e.start_char, end=e.end_char)
                            for e in doc.ents if e.label_ == 'PERSON' and not plausible_person(e))
        mappings = {g: name_mapping(names[g], aliases.get(g, {})) for g in sorted(set(groups))}
    else:
        from lemminflect import getInflection
        words = verb_dictionary(args.dictionary)
        if len(words) < 2:
            raise ValueError('Dictionary needs at least two single-word verb lemmas')
    original_rows = {}
    if (source / 'passages.jsonl').exists():
        original_rows = {r['document_path']: r for r in
                         map(json.loads, (source / 'passages.jsonl').read_text().splitlines())}
    rows, logs, outputs = [], [], []
    for rel, group, text, doc in zip(rels, groups, texts, docs):
        if mode == 'names':
            edits = name_edits(text, mappings[group])
        else:
            # Per-file seed: adding a different passage won't reshuffle this one.
            rng = random.Random(digest(f'{args.seed}:{rel}'.encode()))
            edits = verb_edits(doc, words, rng, getInflection)
        transformed = apply_edits(text, edits)
        outputs.append((rel, transformed))
        log = dict(document_path=rel, source_sha256=digest(text.encode()),
                   output_sha256=digest(transformed.encode()), edits=edits)
        logs.append(log)
        original = original_rows.get(rel, {})
        if original and text.strip() != original['text'].strip():
            raise ValueError(f'Input differs from its provenance: {rel}')
        rows.append({**original, 'id': original.get('id', rel), 'document_path': rel,
                     'text': transformed.strip(), 'word_count': len(transformed.split()),
                     'transforms': original.get('transforms', []) + [dict(operation=mode,
                         input_directory=str(source), input_sha256=log['source_sha256'],
                         output_sha256=log['output_sha256'])]})
    # Complete analysis before creating output; never change source files.
    dest.mkdir(parents=True)
    for rel, text in outputs:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    for filename, values in [('passages.jsonl', rows), ('changes.jsonl', logs)]:
        (dest / filename).write_text(''.join(json.dumps(v, ensure_ascii=False) + '\n' for v in values))
    if mode == 'names':
        write_json(dest / 'name-map.json', mappings)
        write_json(dest / 'rejected-name-candidates.json', rejected)
    else:
        (dest / 'verb-dictionary.json').write_text(json.dumps(words, indent=2) + '\n')
    manifest = dict(operation=mode, input_directory=str(source), files=len(paths),
                    replacements=sum(len(l['edits']) for l in logs),
                    spacy_version=spacy.__version__, model=args.model,
                    model_version=nlp.meta.get('version'),
                    seed=getattr(args, 'seed', None),
                    notes='Heuristic NLP, not guaranteed anonymization or semantic/grammatical preservation.')
    if mode == 'verbs':
        manifest.update(dictionary_size=len(words), dictionary_source=str(args.dictionary or 'WordNet'),
                        dictionary_sha256=digest('\n'.join(words).encode()), auxiliaries='preserved')
    write_json(dest / 'manifest.json', manifest)
    print(json.dumps(manifest, indent=2))
