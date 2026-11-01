"""Extract official SHA-256-verified Lean archive using Ubuntu's libzstd."""

from pathlib import Path
import ctypes
import ctypes.util
import hashlib
import json
import subprocess

root = Path(__file__).resolve().parents[2]
runtime = root / ".runtime"
archive = runtime / "lean-4.35.0-linux.tar.zst"
hasher = hashlib.sha256()
with archive.open("rb") as incoming:
    for block in iter(lambda: incoming.read(4 * 1024 * 1024), b""):
        hasher.update(block)
record = json.loads((runtime / "lean-release.json").read_text())
assert "sha256:" + hasher.hexdigest() == record["digest"]


class Buffer(ctypes.Structure):
    _fields_ = [("src", ctypes.c_void_p), ("size", ctypes.c_size_t), ("pos", ctypes.c_size_t)]


lib = ctypes.CDLL(ctypes.util.find_library("zstd"))
lib.ZSTD_createDStream.restype = ctypes.c_void_p
lib.ZSTD_initDStream.argtypes = [ctypes.c_void_p]
lib.ZSTD_initDStream.restype = ctypes.c_size_t
lib.ZSTD_decompressStream.argtypes = [
    ctypes.c_void_p,
    ctypes.POINTER(Buffer),
    ctypes.POINTER(Buffer),
]
lib.ZSTD_decompressStream.restype = ctypes.c_size_t
lib.ZSTD_isError.argtypes = [ctypes.c_size_t]
lib.ZSTD_isError.restype = ctypes.c_uint
lib.ZSTD_freeDStream.argtypes = [ctypes.c_void_p]
stream = lib.ZSTD_createDStream()
assert stream
assert not lib.ZSTD_isError(lib.ZSTD_initDStream(stream))
process = subprocess.Popen(["tar", "-xf", "-", "-C", str(runtime)], stdin=subprocess.PIPE)
try:
    remaining = 1
    with archive.open("rb") as incoming:
        while block := incoming.read(1024 * 1024):
            raw = ctypes.create_string_buffer(block)
            source = Buffer(ctypes.cast(raw, ctypes.c_void_p), len(block), 0)
            while source.pos < source.size:
                output_raw = ctypes.create_string_buffer(1024 * 1024)
                output = Buffer(ctypes.cast(output_raw, ctypes.c_void_p), len(output_raw), 0)
                remaining = lib.ZSTD_decompressStream(
                    stream, ctypes.byref(output), ctypes.byref(source)
                )
                if lib.ZSTD_isError(remaining):
                    raise RuntimeError("zstd decompression failed")
                process.stdin.write(output_raw.raw[: output.pos])
    assert remaining == 0, "Truncated zstd frame"
finally:
    process.stdin.close()
    lib.ZSTD_freeDStream(stream)
assert process.wait() == 0
print(
    subprocess.check_output([str(runtime / "lean-4.35.0-linux/bin/lean"), "--version"], text=True)
)
