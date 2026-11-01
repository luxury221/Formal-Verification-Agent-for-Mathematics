"""Trusted Linux mount-namespace launcher. Only invoked through unshare by checker."""

from __future__ import annotations

import ctypes
import os
from pathlib import Path
import resource
import sys

MS_RDONLY, MS_NOSUID, MS_NODEV, MS_NOEXEC = 1, 2, 4, 8
MS_REMOUNT, MS_BIND, MS_REC, MS_PRIVATE = 32, 4096, 16384, 1 << 18
libc = ctypes.CDLL(None, use_errno=True)
libc.mount.argtypes = [
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_char_p,
    ctypes.c_ulong,
    ctypes.c_char_p,
]


def mount(
    source: str | None,
    target: Path | str,
    kind: str | None = None,
    flags: int = 0,
    data: str | None = None,
) -> None:
    def encoded(value):
        return None if value is None else str(value).encode()

    if libc.mount(encoded(source), encoded(target), encoded(kind), flags, encoded(data)) != 0:
        raise OSError(ctypes.get_errno(), f"mount {target}")


def bind(
    source: Path | str, target: Path, readonly: bool = True, allow_device: bool = False
) -> None:
    source = Path(source).resolve()
    if source.is_dir():
        target.mkdir(parents=True, exist_ok=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    mount(str(source), target, flags=MS_BIND | MS_REC)
    if readonly:
        flags = MS_BIND | MS_REMOUNT | MS_RDONLY | MS_NOSUID
        if not allow_device:
            flags |= MS_NODEV
        mount(None, target, flags=flags)


def main() -> None:
    runtime, mathlib, work, sandbox, cpu_seconds, memory_mb = sys.argv[1:]
    runtime, mathlib, work, sandbox = map(
        lambda p: Path(p).resolve(), (runtime, mathlib, work, sandbox)
    )
    mount(None, "/", flags=MS_REC | MS_PRIVATE)
    mount("tmpfs", sandbox, "tmpfs", MS_NOSUID | MS_NODEV, "size=80m,mode=755")
    for path in ("usr", "lib", "lib64"):
        if Path("/" + path).exists():
            bind("/" + path, sandbox / path)
    bind(runtime, sandbox / "opt/lean")
    bind(mathlib, sandbox / "opt/mathlib")
    bind(work, sandbox / "work")
    for name in ("null", "urandom", "zero"):
        bind("/dev/" + name, sandbox / "dev" / name, allow_device=True)
    (sandbox / "proc").mkdir()
    mount("proc", sandbox / "proc", "proc", MS_NOSUID | MS_NODEV | MS_NOEXEC)
    (sandbox / "tmp").mkdir()
    mount("tmpfs", sandbox / "tmp", "tmpfs", MS_NOSUID | MS_NODEV, "size=64m,mode=1777")
    resource.setrlimit(resource.RLIMIT_CPU, (int(cpu_seconds), int(cpu_seconds) + 1))
    resource.setrlimit(resource.RLIMIT_AS, (int(memory_mb) * 1048576,) * 2)
    resource.setrlimit(resource.RLIMIT_FSIZE, (1048576,) * 2)
    resource.setrlimit(resource.RLIMIT_NOFILE, (128,) * 2)
    resource.setrlimit(resource.RLIMIT_NPROC, (128,) * 2)
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.chroot(sandbox)
    os.chdir("/work")
    with open("/dev/urandom", "rb") as random_device:
        if len(random_device.read(16)) != 16:
            raise RuntimeError("Sandbox entropy device unavailable")
    lean_paths = ["/opt/mathlib/.lake/build/lib/lean"]
    lean_paths += [
        str(Path("/opt/mathlib/.lake/packages") / p.name / ".lake/build/lib/lean")
        for p in Path("/opt/mathlib/.lake/packages").iterdir()
        if p.is_dir()
    ]
    env = {
        "PATH": "/opt/lean/bin:/usr/bin:/bin",
        "HOME": "/tmp",
        "TMPDIR": "/tmp",
        "LANG": "C.UTF-8",
        "LEAN_PATH": ":".join(lean_paths),
    }
    os.execve("/opt/lean/bin/lean", ["lean", "-j2", "Candidate.lean"], env)


if __name__ == "__main__":
    main()
