"""Optional host-network fallback: download the official Cache-generated URL plan."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
from pathlib import Path
import re
import urllib.request

root = Path(__file__).resolve().parents[2]
cache = root / ".cache/mathlib"
plan = cache / "download-plan.cfg"
if not plan.exists():
    source = max(cache.glob("curl-*.cfg"), key=lambda p: p.stat().st_mtime)
    plan.write_bytes(source.read_bytes())
urls = re.findall(
    r"^url = (https://cache\.mathlib\.org/[^\s]+/f/[0-9a-f]{16}\.ltar)$",
    plan.read_text(),
    re.MULTILINE,
)
assert urls, "No official cache URLs in plan"


def download(url):
    target = cache / url.rsplit("/", 1)[1]
    if not target.exists():
        request = urllib.request.Request(
            url, headers={"User-Agent": "MRA-P0-official-cache-bootstrap"}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read()
        temporary = target.with_suffix(".ltar.host-partial")
        temporary.write_bytes(data)
        temporary.replace(target)
    return {
        "file": target.name,
        "url": url,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }


with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
    results = []
    for index, result in enumerate(executor.map(download, urls), 1):
        results.append(result)
        if index % 20 == 0 or index == len(urls):
            print(f"Official mathlib cache: {index}/{len(urls)}", flush=True)
(root / ".runtime/mathlib-cache-files.json").write_text(json.dumps(results, indent=2) + "\n")
