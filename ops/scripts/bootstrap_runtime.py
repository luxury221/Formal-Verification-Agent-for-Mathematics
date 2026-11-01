"""Download an official pinned Lean binary to D: and verify release SHA-256."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime"


def get_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": "MRA-P0-bootstrap"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return json.load(response)


def main() -> None:
    RUNTIME.mkdir(exist_ok=True)
    release = get_json("https://api.github.com/repos/leanprover/lean4/releases/tags/v4.35.0")
    asset = next(a for a in release["assets"] if a["name"] == "lean-4.35.0-linux.tar.zst")
    target = RUNTIME / asset["name"]
    expected = asset.get("digest")
    if not expected or not expected.startswith("sha256:"):
        raise RuntimeError("Official asset has no SHA-256; refusing unverified bootstrap")
    if not target.exists() or target.stat().st_size != asset["size"]:
        partial = target.with_suffix(target.suffix + ".partial")
        request = urllib.request.Request(
            asset["browser_download_url"], headers={"User-Agent": "MRA-P0-bootstrap"}
        )
        previous = time.monotonic()
        total = 0
        with urllib.request.urlopen(request, timeout=120) as response, partial.open("wb") as output:
            while block := response.read(4 * 1024 * 1024):
                output.write(block)
                total += len(block)
                if time.monotonic() - previous > 10:
                    print(
                        f"Lean download: {total / 1048576:.0f}/{asset['size'] / 1048576:.0f} MiB",
                        flush=True,
                    )
                    previous = time.monotonic()
        if total != asset["size"]:
            raise RuntimeError("Incomplete Lean download")
        partial.replace(target)
    hasher = hashlib.sha256()
    with target.open("rb") as incoming:
        for block in iter(lambda: incoming.read(4 * 1024 * 1024), b""):
            hasher.update(block)
    actual = "sha256:" + hasher.hexdigest()
    if actual != expected:
        raise RuntimeError("Lean release digest mismatch")
    evidence = {
        "tag": release["tag_name"],
        "asset": asset["name"],
        "url": asset["browser_download_url"],
        "size": target.stat().st_size,
        "digest": actual,
    }
    (RUNTIME / "lean-release.json").write_text(
        json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(evidence), flush=True)


if __name__ == "__main__":
    main()
