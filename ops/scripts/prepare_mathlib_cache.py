"""Build Cache using local archive pins, then restore upstream hash inputs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
RUNTIME = ROOT / ".runtime"
MATHLIB = RUNTIME / "mathlib"


def main() -> None:
    records = json.loads((RUNTIME / "mathlib-sources.json").read_text())
    # Codeload archives preserve the source commit without requiring Git smart-HTTP.
    # Lake path entries are temporary: cache keys must see the exact upstream bytes.
    for record in records:
        archive = (
            RUNTIME
            / "downloads"
            / (record["repository"].replace("/", "-") + "-" + record["commit"] + ".tar.gz")
        )
        hasher = hashlib.sha256()
        with archive.open("rb") as incoming:
            for block in iter(lambda: incoming.read(1024 * 1024), b""):
                hasher.update(block)
        assert hasher.hexdigest() == record["archive_sha256"]
    licenses = []
    for entry in json.loads((MATHLIB / "lake-manifest.json").read_text())["packages"]:
        folder = MATHLIB / ".lake/packages" / entry["name"]
        license_files = [
            p
            for p in folder.iterdir()
            if p.is_file() and p.name.lower().startswith(("license", "copying", "notice"))
        ]
        if not license_files:
            raise RuntimeError(f"Missing dependency license: {entry['name']}")
        destination = ROOT / "docs/security/upstream/mathlib-dependencies" / entry["name"]
        destination.mkdir(parents=True, exist_ok=True)
        for path in license_files:
            (destination / path.name).write_bytes(path.read_bytes())
        licenses.append(
            {
                "name": entry["name"],
                "url": entry["url"],
                "commit": entry["rev"],
                "licenses": [
                    {"file": p.name, "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                    for p in license_files
                ],
            }
        )
    (ROOT / "docs/security/mathlib-dependencies.json").write_text(
        json.dumps(licenses, indent=2) + "\n"
    )
    env = os.environ.copy()
    env.update(
        PATH=str(RUNTIME / "lean-4.35.0-linux/bin") + os.pathsep + env["PATH"],
        XDG_CACHE_HOME=str(ROOT / ".cache"),
        MATHLIB_CACHE_DIR=str(ROOT / ".cache/mathlib"),
        ELAN_HOME=str(RUNTIME / "elan"),
        TMPDIR=str(ROOT / ".tmp"),
    )
    (ROOT / ".tmp").mkdir(exist_ok=True)
    manifest_path = MATHLIB / "lake-manifest.json"
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    paths = [MATHLIB] + [MATHLIB / ".lake/packages" / p["name"] for p in manifest["packages"]]
    env["LEAN_SRC_PATH"] = os.pathsep.join(
        str(p) for p in paths + [RUNTIME / "lean-4.35.0-linux/src/lean"]
    )
    local_manifest = {
        **manifest,
        "packages": [
            {
                "type": "path",
                "name": p["name"],
                "scope": p.get("scope", ""),
                "dir": str(MATHLIB / ".lake/packages" / p["name"]),
                "inherited": p["inherited"],
                "configFile": p["configFile"],
                "manifestFile": p["manifestFile"],
            }
            for p in manifest["packages"]
        ],
    }
    try:
        manifest_path.write_text(json.dumps(local_manifest, indent=2) + "\n")
        subprocess.run(
            [str(RUNTIME / "lean-4.35.0-linux/bin/lake"), "build", "cache"],
            cwd=MATHLIB,
            env=env,
            check=True,
        )
    finally:
        manifest_path.write_bytes(original)
    if "--unpack-only" in sys.argv:
        downloaded = json.loads((RUNTIME / "mathlib-cache-files.json").read_text())
        for entry in downloaded:
            local_file = ROOT / ".cache/mathlib" / entry["file"]
            if hashlib.sha256(local_file.read_bytes()).hexdigest() != entry["sha256"]:
                raise RuntimeError("Local cache bundle changed: " + entry["file"])
    subprocess.run(
        [
            str(MATHLIB / ".lake/build/bin/cache"),
            "--repo=leanprover-community/mathlib4",
            "get",
            "Mathlib/Data/Nat/Basic.lean",
        ],
        cwd=MATHLIB,
        env=env,
        check=True,
    )
    # Also record acquisition hashes on the normal Linux download path.
    receipt_path = RUNTIME / "mathlib-cache-files.json"
    previous = (
        {entry["file"]: entry for entry in json.loads(receipt_path.read_text())}
        if receipt_path.exists()
        else {}
    )
    acquired = []
    for path in sorted((ROOT / ".cache/mathlib").glob("*.ltar")):
        current_hash = hashlib.sha256(path.read_bytes()).hexdigest()
        if path.name in previous and previous[path.name]["sha256"] != current_hash:
            raise RuntimeError("Previously acquired cache bundle changed: " + path.name)
        acquired.append(
            previous.get(
                path.name,
                {
                    "file": path.name,
                    "sha256": current_hash,
                    "source": "official Cache get; local acquisition digest",
                },
            )
        )
    if not acquired:
        raise RuntimeError("Official Cache get produced no cache bundles")
    receipt_path.write_text(json.dumps(acquired, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
