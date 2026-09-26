# PDF to Markdown conversion

Each paper has its own directory inside its subject category:

```text
subject/paper-slug/
  paper-slug.pdf
  paper-slug.md
  conversion.json
```

The single Markdown file has numbered PDF-page headings and `---` separators. The original PDF is retained unchanged. The adjacent manifest records source/output SHA-256 checksums, converter version, page count, OCR pages, and pages with sparse or missing extracted text.

## Run again

From the repository root:

```sh
python3 -m venv /private/tmp/consciousness-pdf-converter-venv
/private/tmp/consciousness-pdf-converter-venv/bin/python -m pip install -r papers/converter-requirements.txt
/private/tmp/consciousness-pdf-converter-venv/bin/python papers/convert_papers.py --dry-run
/private/tmp/consciousness-pdf-converter-venv/bin/python papers/convert_papers.py --workers 2
```

The environment is temporary; recreate it if the operating system clears it. Use another virtual-environment location if preferred. The script defaults to the directory containing it; `--root PATH` selects another library.

To recover image-only text, enable local Tesseract OCR. On this machine the English language data is already installed at `/opt/homebrew/share/tessdata`:

```sh
/private/tmp/consciousness-pdf-converter-venv/bin/python papers/convert_papers.py --ocr --tessdata /opt/homebrew/share/tessdata
```

OCR runs only on pages with fewer than 40 extracted word characters, and only replaces the native extraction when it recovers more text. It does not correct all font-encoding errors in otherwise text-rich pages. Existing unchanged conversions with sparse pages can be upgraded using `--ocr` without `--force`.

Unchanged conversions are skipped on reruns. Modified or unrecognized Markdown is not overwritten without explicit `--force`. PDF collisions are rejected. After conversion, the script updates local PDF links, adds Markdown links in `README.md`, updates catalog paths, and writes `conversion-report.json`. It also discovers PDFs not yet in the catalog, but does not invent bibliographic records for them.

## Conversion limitations

Conversion runs locally using [PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/), pinned in the requirements file. It uses the legacy deterministic layout engine for headings, multi-column reading order, tables, and text formatting. No document content is sent to an external conversion service. No generative model rewrites or summarizes the papers.

This collection uses native PDF text with selective local OCR for image-only/sparse appendix pages. Figure images are not embedded. Mathematical notation, tables, unusual fonts, scanned pages, OCR accuracy, and reading order can be imperfect. Empty pages are explicitly marked; sparse pages, OCR pages, and plain-text fallbacks are recorded in manifests. The PDF remains the authoritative source. Page labels refer to the physical PDF sequence, not journal pagination.
