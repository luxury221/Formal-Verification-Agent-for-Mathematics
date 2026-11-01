"""Revalidate installed archives/cache, then explicitly seal a runtime baseline."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from mra.integrity import (  # noqa: E402
    ContentSnapshot,
    FILES,
    TREES,
    IntegrityError,
    file_hash,
)
from mra.datasets import load_dataset  # noqa: E402


class Buffer(ctypes.Structure):
    _fields_ = [("src", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]


class ZstdReader(io.RawIOBase):
    """Stream the official archive without a zstd executable or a multi-GB temp tar."""

    def __init__(self, path: Path) -> None:
        super().__init__()
        self.incoming = path.open("rb")
        self.lib = ctypes.CDLL(ctypes.util.find_library("zstd"))
        self.lib.ZSTD_createDStream.restype = ctypes.c_void_p
        self.lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
        self.lib.ZSTD_initDStream.restype = ctypes.c_size_t
        self.lib.ZSTD_decompressStream.argtypes = [
            ctypes.c_void_p,
            ctypes.POINTER(Buffer),
            ctypes.POINTER(Buffer),
        ]
        self.lib.ZSTD_decompressStream.restype = ctypes.c_size_t
        self.lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
        self.lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
        self.stream = self.lib.ZSTD_createDStream()
        if not self.stream or self.lib.ZSTD_isError(self.lib.ZSTD_initDStream(self.stream)):
            raise IntegrityError("Cannot initialize archive decompressor")
        self.source = Buffer(None, 0, 0)
        self.remaining = 1

    def readable(self) -> bool:
        return True

    def readinto(self, destination) -> int:
        if self.source.pos == self.source.size:
            block = self.incoming.read(1024 * 1024)
            if not block:
                if self.remaining:
                    raise IntegrityError("Truncated zstd archive")
                return 0
            self.raw = ctypes.create_string_buffer(block)
            self.source = Buffer(ctypes.cast(self.raw, ctypes.c_void_p), len(block), 0)
        output_raw = (ctypes.c_char * len(destination)).from_buffer(destination)
        output = Buffer(ctypes.cast(output_raw, ctypes.c_void_p), len(destination), 0)
        self.remaining = self.lib.ZSTD_decompressStream(
            self.stream, ctypes.byref(output), ctypes.byref(self.source)
        )
        if self.lib.ZSTD_isError(self.remaining):
            raise IntegrityError("Invalid zstd archive")
        if output.pos == 0:
            return self.readinto(destination)
        return output.pos

    def close(self) -> None:
        if not self.closed:
            self.lib.ZSTD_freeDStream(self.stream)
            self.incoming.close()
        super().close()


def verify_members(
    archive: tarfile.TarFile, destination: Path, *, source_archive: bool = False
) -> int:
    count = 0
    expected_paths = set()
    for member in archive:
        parts = Path(member.name).parts[1:]
        if not parts:
            continue
        if Path(member.name).is_absolute() or ".." in parts or member.isdev():
            raise IntegrityError(f"Unsafe archive member: {member.name}")
        installed = destination.joinpath(*parts)
        expected_paths.add(Path(*parts).as_posix())
        if member.isdir():
            if installed.is_symlink() or not installed.is_dir():
                raise IntegrityError(f"Archive directory changed: {installed}")
        elif member.issym():
            if source_archive and not installed.exists() and not installed.is_symlink():
                continue  # bootstrap_mathlib intentionally omits documentation aliases.
            if not installed.is_symlink() or os.readlink(installed) != member.linkname:
                raise IntegrityError(f"Archive symlink changed: {installed}")
        elif member.isfile():
            if installed.is_symlink() or not installed.is_file():
                raise IntegrityError(f"Archive file missing or redirected: {installed}")
            with archive.extractfile(member) as incoming:
                hasher = hashlib.sha256()
                for block in iter(lambda: incoming.read(1024 * 1024), b""):
                    hasher.update(block)
            if file_hash(installed) != hasher.hexdigest():
                raise IntegrityError(f"Installed file differs from its archive: {installed}")
            count += 1
        else:
            raise IntegrityError(f"Unsupported archive entry: {member.name}")
    for path in destination.rglob("*"):
        relative = path.relative_to(destination)
        if relative.as_posix() not in expected_paths:
            if not source_archive or (
                path.suffix == ".lean"
                and ".lake" not in relative.parts
                and ".git" not in relative.parts
            ):
                raise IntegrityError(f"Unexpected file outside source archive: {path}")
    return count


def validate_provenance() -> dict:
    runtime = ROOT / ".runtime"
    lean = runtime / "lean-4.35.0-linux"
    mathlib = runtime / "mathlib"
    config = json.loads((ROOT / "lean_workspace/environment.json").read_text())
    lean_archive = runtime / "lean-4.35.0-linux.tar.zst"
    if file_hash(lean_archive) != config["lean_archive_sha256"]:
        raise IntegrityError("Lean archive does not match the repository's official release pin")
    print("Revalidating installed Lean against the official archive...", flush=True)
    with ZstdReader(lean_archive) as raw, io.BufferedReader(raw) as incoming:
        with tarfile.open(fileobj=incoming, mode="r|") as archive:
            lean_files = verify_members(archive, lean)

    sources = json.loads((runtime / "mathlib-sources.json").read_text())
    manifest = json.loads((mathlib / "lake-manifest.json").read_text())
    expected = {"leanprover-community/mathlib4": (config["mathlib_commit"], mathlib)}
    expected.update(
        {
            p["url"].removeprefix("https://github.com/").removesuffix(".git"): (
                p["rev"],
                mathlib / ".lake/packages" / p["name"],
            )
            for p in manifest["packages"]
        }
    )
    if len(sources) != len(expected) or {s["repository"] for s in sources} != set(expected):
        raise IntegrityError("Source archive provenance does not cover the manifest")
    source_files = 0
    for record in sources:
        revision, target = expected[record["repository"]]
        if (
            record["commit"] != revision
            or (target / ".mra-source-pin").read_text().strip() != revision
        ):
            raise IntegrityError("Wrong source archive revision")
        source_path = (
            runtime
            / "downloads"
            / (record["repository"].replace("/", "-") + "-" + revision + ".tar.gz")
        )
        if file_hash(source_path) != record["archive_sha256"]:
            raise IntegrityError(f"Source archive changed: {source_path.name}")
        with tarfile.open(source_path, "r:gz") as archive:
            source_files += verify_members(archive, target, source_archive=True)
    print(
        f"Validated {source_files} installed source files; comparing official cache contents...",
        flush=True,
    )

    cache_records = json.loads((runtime / "mathlib-cache-files.json").read_text())
    if not cache_records:
        raise IntegrityError("No cache acquisition receipt; rerun prepare_mathlib_cache.py")
    cache_paths = []
    for record in cache_records:
        if not re.fullmatch(r"[0-9a-f]{16}\.ltar", record["file"]):
            raise IntegrityError("Invalid cache bundle name")
        local = ROOT / ".cache/mathlib" / record["file"]
        if file_hash(local) != record["sha256"]:
            raise IntegrityError(f"Cache bundle changed: {record['file']}")
        cache_paths.append(str(local))
    # leantar has now been compared byte-for-byte with the verified Lean release.
    cache_files = 0
    with tempfile.TemporaryDirectory(prefix="cache-verify-", dir=runtime) as temp:
        subprocess.run(
            [str(lean / "bin/leantar"), "-x", "-j", "-"],
            input=json.dumps(cache_paths),
            text=True,
            cwd=temp,
            check=True,
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            timeout=120,
        )
        for path in sorted(Path(temp).rglob("*")):
            if path.is_symlink():
                raise IntegrityError("Unexpected symlink in official cache")
            if path.is_file():
                installed = mathlib / path.relative_to(temp)
                if (
                    not installed.is_file()
                    or installed.is_symlink()
                    or file_hash(installed) != file_hash(path)
                ):
                    raise IntegrityError(f"Installed file differs from cache: {installed}")
                cache_files += 1
    if not cache_files:
        raise IntegrityError("Cache verification extracted no artifacts")
    return {
        "lean_archive_sha256": config["lean_archive_sha256"],
        "lean_archive_files_checked": lean_files,
        "source_files_checked": source_files,
        "cache_bundles_checked": len(cache_records),
        "cache_files_checked": cache_files,
        "source_and_cache_origin": "pinned HTTPS acquisition receipts; not independently signed upstream attestations",
        "cache_builder": "local build from archived sources; binary content bound by snapshot",
    }


def host_dependencies() -> tuple[str, ...]:
    binaries = [
        "/usr/bin/unshare",
        "/usr/bin/python3",
        "/bin/sh",
        str(ROOT / ".runtime/lean-4.35.0-linux/bin/lean"),
    ]
    dependencies = set(binaries[:-1])
    for binary in binaries:
        output = subprocess.check_output(
            ["/usr/bin/ldd", binary], text=True, env={"PATH": "/usr/bin:/bin", "LANG": "C"}
        )
        dependencies.update(re.findall(r"(?:=>\s+)?(/[^\s()]+)", output))
    return tuple(sorted(dependencies))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Explicitly revalidate archives and replace an existing seal",
    )
    args = parser.parse_args()
    started = time.monotonic()
    load_dataset(ROOT)  # Validate dataset scope/permissions/splits before accepting a new seal.
    provenance = validate_provenance()
    snapshot = ContentSnapshot.capture(ROOT, TREES, FILES, host_dependencies(), provenance)
    snapshot.write(replace=args.replace)
    print(
        json.dumps(
            {
                "snapshot_digest": snapshot.fingerprint,
                "entries": sum(len(t) for t in snapshot.data["trees"].values()),
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "provenance": provenance,
            },
            indent=2,
        ),
        flush=True,
    )


if __name__ == "__main__":
    main()
