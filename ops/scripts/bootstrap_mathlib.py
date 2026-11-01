"""Materialize mathlib and its manifest pins without global package installs."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
from pathlib import Path
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime"
PIN = "56a005855ea17e0b0958f73ec801ae140b0a1679"


def materialize(repo: str, revision: str, target: Path) -> dict:
    url = f"https://codeload.github.com/{repo}/tar.gz/{revision}"
    archive = RUNTIME / "downloads" / (repo.replace("/", "-") + "-" + revision + ".tar.gz")
    archive.parent.mkdir(parents=True, exist_ok=True)
    if not archive.exists():
        request = urllib.request.Request(url, headers={"User-Agent": "MRA-P0-bootstrap"})
        with urllib.request.urlopen(request, timeout=120) as response:
            archive.write_bytes(response.read())
    marker = target / ".mra-source-pin"
    if not marker.exists():
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(archive, "r:gz") as source:
            for member in source.getmembers():
                parts = Path(member.name).parts[1:]
                if not parts:
                    continue
                if member.isdev() or ".." in parts or Path(member.name).is_absolute():
                    raise RuntimeError(f"Unsafe archive member: {member.name}")
                if member.issym() or member.islnk():
                    continue  # Documentation aliases are not needed for the runtime.
                destination = target.joinpath(*parts)
                if not destination.resolve().is_relative_to(target.resolve()):
                    raise RuntimeError(f"Archive path escapes target: {member.name}")
                if member.isdir():
                    destination.mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source.extractfile(member) as incoming, destination.open("wb") as outgoing:
                        import shutil

                        shutil.copyfileobj(incoming, outgoing)
                    destination.chmod(member.mode & 0o777)
        marker.write_text(revision + "\n", encoding="utf-8")
    print(f"Materialized {repo}@{revision[:12]}", flush=True)
    hasher = hashlib.sha256()
    with archive.open("rb") as incoming:
        for block in iter(lambda: incoming.read(1024 * 1024), b""):
            hasher.update(block)
    return {
        "repository": repo,
        "commit": revision,
        "archive_sha256": hasher.hexdigest(),
        "url": url,
    }


def main() -> None:
    target = RUNTIME / "mathlib"
    records = [materialize("leanprover-community/mathlib4", PIN, target)]
    manifest = json.loads((target / "lake-manifest.json").read_text(encoding="utf-8"))

    def package(entry: dict) -> dict:
        repo = entry["url"].removeprefix("https://github.com/").removesuffix(".git")
        return materialize(repo, entry["rev"], target / ".lake/packages" / entry["name"])

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        records.extend(executor.map(package, manifest["packages"]))
    (RUNTIME / "mathlib-sources.json").write_text(
        json.dumps(records, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
