#!/usr/bin/env python3
"""Organize PDFs and produce adjacent, page-delimited Markdown locally.

Install converter-requirements.txt, then run this file. No external document API
is used. PDFs are moved byte-for-byte, never rewritten. Markdown is generated
with PyMuPDF4LLM's deterministic legacy layout engine; optional local OCR
recovers text from sparse/image-only pages.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import importlib.metadata
import json
from pathlib import Path
import re
import sys
import tempfile


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def atomic_write(path: Path, text: str) -> None:
    """Replace only an explicitly selected output, never expose a partial file."""
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                     prefix=".convert-", delete=False) as stream:
        temporary = Path(stream.name)
        try:
            stream.write(text)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def destination(pdf: Path) -> Path:
    return pdf if pdf.parent.name == pdf.stem else pdf.parent / pdf.stem / pdf.name


def plan(root: Path) -> list[tuple[Path, Path]]:
    jobs = []
    targets = set()
    for pdf in sorted(root.rglob("*.pdf")):
        if pdf.is_symlink() or not pdf.resolve().is_relative_to(root):
            raise ValueError(f"Refusing symlink/outside-root PDF: {pdf}")
        target = destination(pdf)
        if target.parent.is_symlink():
            raise ValueError(f"Refusing symlink destination: {target.parent}")
        if target in targets or (target != pdf and target.exists()):
            raise FileExistsError(f"PDF destination collision: {target}")
        if target.parent.exists() and not target.parent.is_dir():
            raise FileExistsError(f"Destination is not a directory: {target.parent}")
        targets.add(target)
        jobs.append((pdf, target))
    return jobs


def render_markdown(title: str, pdf_name: str, pages: list[str], ocr_pages: list[int] | None = None) -> str:
    title = " ".join(title.split())
    ocr_note = ("Local OCR was used on PDF pages " + ", ".join(map(str, ocr_pages)) + "."
                if ocr_pages else "No OCR was used.")
    header = (f"# {title}\n\n[Original PDF]({pdf_name})\n\n"
              "> Automatically converted with PyMuPDF4LLM. Page numbers follow the PDF file, "
              "not the printed journal numbering. Figures are not embedded; equations, tables, "
              f"and reading order may require checking against the original. {ocr_note}\n")
    sections = [f"## Page {number}\n\n{text.strip() or '[No extractable text on this page; see the original PDF.]'}"
                for number, text in enumerate(pages, 1)]
    return header + "\n---\n\n" + "\n\n---\n\n".join(sections) + "\n"


def convert_one(source_str: str, target_str: str, title: str, force: bool,
                ocr: bool = False, tessdata: str | None = None) -> dict:
    import pymupdf
    import pymupdf4llm

    source, target = Path(source_str), Path(target_str)
    md, manifest = target.with_suffix(".md"), target.parent / "conversion.json"
    source_hash = digest(source)
    version = importlib.metadata.version("pymupdf4llm")
    if md.exists() and not force:
        record = json.loads(manifest.read_text()) if manifest.exists() else {}
        if (record.get("pdf_sha256") == source_hash
                and record.get("markdown_sha256") == digest(md)
                and record.get("converter_version") == version):
            if source != target:
                raise FileExistsError(f"Existing Markdown beside a different PDF location: {md}")
            if not ocr or record.get("ocr") or not record.get("sparse_pages"):
                return {**record, "source": source_str, "target": target_str, "status": "skipped"}
            # Upgrade our unchanged generated output with newly requested OCR.
        else:
            raise FileExistsError(f"Existing/edited Markdown will not be overwritten: {md}; inspect before using --force")

    pymupdf4llm.use_layout(False)
    with pymupdf.open(source) as doc:
        if doc.needs_pass:
            raise ValueError(f"Encrypted PDF requires a password: {source}")
        chunks = pymupdf4llm.to_markdown(
            doc, page_chunks=True, show_progress=False,
            write_images=False, embed_images=False,
        )
        if len(chunks) != len(doc):
            raise ValueError(f"Page count mismatch for {source}: {len(chunks)} != {len(doc)}")
        pages, empty_pages, fallback_pages, sparse_pages, ocr_pages = [], [], [], [], []
        for number, chunk in enumerate(chunks, 1):
            text = chunk["text"].strip()
            if not text:
                text = doc[number - 1].get_text(sort=True).strip()
                if text:
                    fallback_pages.append(number)
            if ocr and len(re.sub(r"\W", "", text)) < 40:
                page = doc[number - 1]
                textpage = page.get_textpage_ocr(full=True, dpi=200, language="eng", tessdata=tessdata)
                recognized = page.get_text(textpage=textpage, sort=True).strip()
                if len(recognized) > len(text):
                    text = recognized
                    ocr_pages.append(number)
            if not text:
                empty_pages.append(number)
            if len(re.sub(r"\W", "", text)) < 40:
                sparse_pages.append(number)
            pages.append(text)
        if not any(pages):
            raise ValueError(f"No extractable text in {source}; OCR is required")

    markdown = render_markdown(title or source.stem, target.name, pages, ocr_pages)
    record = {
        "pdf": target.name, "markdown": md.name,
        "converter": "pymupdf4llm", "converter_version": version,
        "engine": "legacy", "ocr": ocr, "ocr_pages": ocr_pages, "images_embedded": False,
        "pdf_sha256": source_hash,
        "markdown_sha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "pages": len(pages), "characters": len(markdown),
        "empty_pages": empty_pages, "sparse_pages": sparse_pages,
        "plain_text_fallback_pages": fallback_pages,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    # Recheck immediately before moving; do not overwrite any PDF.
    if source != target:
        if target.exists():
            raise FileExistsError(target)
        source.rename(target)
    if digest(target) != source_hash:
        raise ValueError(f"PDF checksum changed: {target}")
    atomic_write(md, markdown)
    atomic_write(manifest, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return {**record, "source": source_str, "target": target_str, "status": "converted"}


def synchronize(root: Path, catalog: dict, results: list[dict]) -> None:
    """Repair old links as well as already-migrated paths after an interrupted run."""
    paths = {}
    records = {str(Path(r["target"]).relative_to(root)): r for r in results}
    for result in results:
        old = str(Path(result["source"]).relative_to(root))
        paths[old] = str(Path(result["target"]).relative_to(root))
    for paper in catalog.get("papers", []):
        old = paper["file"]
        target = str(destination(root / old).relative_to(root))
        if target in records:
            paths[old] = target
            paper["file"] = target
            paper["markdown"] = str(Path(target).with_suffix(".md"))
            paper["pdf_sha256"] = records[target]["pdf_sha256"]
    if (root / "catalog.json").exists():
        atomic_write(root / "catalog.json", json.dumps(catalog, indent=2, ensure_ascii=False) + "\n")
    readme = root / "README.md"
    if readme.exists():
        text = readme.read_text()
        for old, new in paths.items():
            text = text.replace(f"]({old})", f"]({new})")
        for new in records:
            pdf_link = f"[Local PDF]({new})"
            md_link = f"[Markdown]({Path(new).with_suffix('.md')})"
            if md_link not in text:
                text = text.replace(pdf_link, pdf_link + " · " + md_link)
        atomic_write(readme, text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    parser.add_argument("--workers", type=int, default=2, help="Independent local converter processes")
    parser.add_argument("--dry-run", action="store_true", help="Print paths without importing converter or writing")
    parser.add_argument("--force", action="store_true", help="Explicitly overwrite existing Markdown")
    parser.add_argument("--ocr", action="store_true", help="Use local Tesseract for sparse/image-only pages")
    parser.add_argument("--tessdata", type=Path, help="Directory containing local eng.traineddata; required with --ocr")
    args = parser.parse_args()
    root = args.root.resolve()
    if not root.is_dir() or args.workers < 1:
        parser.error("--root must be an existing directory and --workers must be positive")
    if args.ocr and (not args.tessdata or not (args.tessdata / "eng.traineddata").is_file()):
        parser.error("--ocr requires --tessdata pointing to local eng.traineddata (no automatic download)")
    jobs = plan(root)
    if args.dry_run:
        for source, target in jobs:
            print(f"{source.relative_to(root)} -> {target.relative_to(root)} + {target.stem}.md")
        print(f"{len(jobs)} PDFs; no files changed")
        return 0
    if not jobs:
        print("No PDFs found")
        return 0
    catalog_path = root / "catalog.json"
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {}
    titles = {}
    for paper in catalog.get("papers", []):
        titles[paper["file"]] = paper["title"]
        titles[str(destination(root / paper["file"]).relative_to(root))] = paper["title"]
    results, errors = [], []
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        pending = {pool.submit(convert_one, str(source), str(target),
                               titles.get(str(source.relative_to(root)), source.stem), args.force,
                               args.ocr, str(args.tessdata) if args.tessdata else None): source
                   for source, target in jobs}
        for future in as_completed(pending):
            source = pending[future]
            try:
                result = future.result()
                results.append(result)
                print(f"{result['status']}: {source.name} ({result['pages']} pages)", flush=True)
            except Exception as error:
                errors.append({"source": str(source.relative_to(root)), "error": str(error)})
                print(f"ERROR: {source.name}: {error}", file=sys.stderr, flush=True)
    synchronize(root, catalog, results)
    report = {"pdf_count": len(jobs), "successful": len(results), "failed": len(errors),
              "pages": sum(r["pages"] for r in results),
              "results": [{**r, "source": str(Path(r["source"]).relative_to(root)),
                           "target": str(Path(r["target"]).relative_to(root))}
                          for r in sorted(results, key=lambda r: r["target"])], "errors": errors}
    atomic_write(root / "conversion-report.json", json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    print(f"Finished: {len(results)}/{len(jobs)} successful; {len(errors)} failed; {report['pages']} pages")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
