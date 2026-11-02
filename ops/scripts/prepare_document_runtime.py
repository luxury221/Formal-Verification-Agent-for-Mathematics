"""Prepare a project-local PDF/OCR toolchain without root or global installs."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request
import venv

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime/document-ingestion-v1"
CACHE = ROOT / ".cache/document-ingestion-v1"
PACKAGES = {
    "tesseract-ocr": "4.1.1-2.1build1",
    "libtesseract4": "4.1.1-2.1build1",
    "liblept5": "1.82.0-3build1",
    "tesseract-ocr-eng": "1:4.00~git30-7274cfa-1.1",
    "libgif7": "5.1.9-2ubuntu0.3",
    "libwebpmux3": "1.2.2-2ubuntu0.22.04.2",
}


def run(args, **options):
    return subprocess.run(args, check=True, **options)


def prepare(with_ocr):
    RUNTIME.mkdir(parents=True, exist_ok=True)
    CACHE.mkdir(parents=True, exist_ok=True)
    python = RUNTIME / "python/bin/python"
    if not python.exists():
        venv.EnvBuilder(with_pip=True).create(RUNTIME / "python")
    run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--cache-dir",
            str(CACHE / "pip"),
            "--only-binary=:all:",
            "-r",
            str(ROOT / "ops/requirements-documents.txt"),
        ]
    )
    if with_ocr:
        if sys.platform != "linux":
            raise RuntimeError("The bundled OCR preparation targets Ubuntu 22.04 amd64")
        for name, version in PACKAGES.items():
            meta = subprocess.check_output(["apt-cache", "show", name + "=" + version], text=True)
            fields = {}
            for line in meta.splitlines():
                if line and not line[0].isspace() and ": " in line:
                    key, value = line.split(": ", 1)
                    fields.setdefault(key, value)
            filename, expected = fields["Filename"], fields["SHA256"]
            if not filename.startswith("pool/") or ".." in filename.split("/"):
                raise RuntimeError("Invalid package repository path")
            target = CACHE / Path(filename).name
            if not target.exists() or hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                with urllib.request.urlopen(
                    "https://archive.ubuntu.com/ubuntu/" + filename, timeout=45
                ) as response:
                    raw = response.read(32 * 1024 * 1024 + 1)
                if len(raw) > 32 * 1024 * 1024 or hashlib.sha256(raw).hexdigest() != expected:
                    raise RuntimeError("OCR package hash mismatch")
                target.write_bytes(raw)
            run(["dpkg-deb", "-x", str(target), str(RUNTIME / "ocr")])
    env = dict(os.environ)
    env["LD_LIBRARY_PATH"] = str(RUNTIME / "ocr/usr/lib/x86_64-linux-gnu")
    binary = RUNTIME / "ocr/usr/bin/tesseract"
    ocr_version = None
    if binary.exists():
        ocr_version = subprocess.check_output(
            [str(binary), "--version"], env=env, stderr=subprocess.STDOUT, text=True
        ).splitlines()[0]
    script = "import importlib.metadata as m,json;print(json.dumps({n:m.version(n) for n in ['pypdf','pypdfium2','Pillow','typing_extensions']}))"
    versions = json.loads(subprocess.check_output([str(python), "-c", script], text=True))
    manifest = {
        "schema": "document-runtime-v1",
        "python_packages": versions,
        "ocr": ocr_version,
        "packages": {
            p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in CACHE.glob("*.deb")
        },
        "uses": "Local document extraction/rendering; no mathematical authority",
        "limits": "English text OCR; mathematical formulas require review; see docs/dependencies/document-ingestion-v1.md",
    }
    (RUNTIME / "installation.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(json.dumps(manifest, indent=2))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--with-ocr", action="store_true", help="Also unpack pinned Ubuntu OCR packages locally"
    )
    prepare(parser.parse_args().with_ocr)
