"""Download public source datasets; never reads API credentials or runs labeling.

Archives are retained and data/documentation extracted without executing code.
Existing verified downloads are reused; errors are recorded per file.
"""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parent / "emotion-labeling/datasets"
JOBS = [
    ("crowd-envent", "source.zip", "https://www.romanklinger.de/data-sets/crowd-enVent2023.zip"),
    ("atomic2020", "source.zip", "https://drive.google.com/uc?export=download&id=1uuY0Y_s8dhxdsoOe8OHRgsqf-9qJIai7"),
    ("isear", "source.zip", "https://www.unige.ch/cisa/download_file/view/132/908"),
    ("empathetic-dialogues", "source.tar.gz", "https://dl.fbaipublicfiles.com/parlai/empatheticdialogues/empatheticdialogues.tar.gz"),
]
JOBS += [("goemotions", f"goemotions_{i}.csv",
          f"https://storage.googleapis.com/gresearch/goemotions/data/full_dataset/goemotions_{i}.csv") for i in (1, 2, 3)]
JOBS += [("goemotions", name,
          f"https://raw.githubusercontent.com/google-research/google-research/master/goemotions/data/{name}")
         for name in ("train.tsv", "dev.tsv", "test.tsv", "emotions.txt")]
JOBS += [
    ("goemotions", "SOURCE_README.md", "https://raw.githubusercontent.com/google-research/google-research/master/goemotions/README.md"),
    ("empathetic-dialogues", "SOURCE_LICENSE", "https://raw.githubusercontent.com/facebookresearch/EmpatheticDialogues/main/LICENSE"),
    ("atomic2020", "SOURCE_README.md", "https://raw.githubusercontent.com/allenai/comet-atomic-2020/master/README.md"),
]


def unpack(path, dest):
    def copy_member(name, stream):
        target = (dest / name).resolve()
        if not target.is_relative_to(dest.resolve()):
            raise ValueError("Unsafe archive path")
        if target.suffix.lower() not in {".tsv", ".csv", ".txt", ".md", ".pdf", ".doc", ".docx", ".mdb", ".sav", ".zip"}:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        if not target.exists():
            with target.open("wb") as handle:
                shutil.copyfileobj(stream, handle)
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError("Archive CRC failure")
            for member in archive.infolist():
                if not member.is_dir():
                    with archive.open(member) as stream:
                        copy_member(member.filename, stream)
    elif path.name.endswith(".tar.gz"):
        with tarfile.open(path) as archive:
            for member in archive:
                if member.isfile():
                    with archive.extractfile(member) as stream:
                        copy_member(member.name, stream)


def download(job):
    dataset, name, url = job
    folder = ROOT / dataset
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    result = {"dataset": dataset, "file": str(path.relative_to(ROOT)), "url": url}
    try:
        if not path.exists():
            partial = path.with_name(path.name + ".partial")
            request = urllib.request.Request(url, headers={"User-Agent": "ResearchDatasetDownload/1.0"})
            with urllib.request.urlopen(request, timeout=90) as response, partial.open("wb") as output:
                shutil.copyfileobj(response, output)
            if name.endswith(".zip") and not zipfile.is_zipfile(partial):
                raise ValueError("Expected ZIP; received another format")
            partial.rename(path)
        unpack(path, folder / "extracted")
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        result.update(status="complete", bytes=path.stat().st_size, sha256=digest.hexdigest())
    except Exception as exc:
        result.update(status="failed", error=f"{type(exc).__name__}: {exc}")
    print(f"{dataset}/{name}: {result['status']}", flush=True)
    return result


def main():
    ROOT.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(download, JOBS))
    (ROOT / "download-manifest.json").write_text(json.dumps(results, indent=2) + "\n")
    failed = [r for r in results if r["status"] != "complete"]
    print(f"Completed {len(results) - len(failed)}/{len(results)} files. Manifest: {ROOT / 'download-manifest.json'}")
    if failed:
        print(json.dumps(failed, indent=2))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
