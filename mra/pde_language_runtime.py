"""Independent watched seal for the controlled classical PDE language."""

from copy import deepcopy
from pathlib import Path
import platform

from . import pde_energy_watched as prior
from .domain import canonical, digest
from .formalizer import strict_json
from .integrity import IntegrityError, file_hash
from .mathlib_lab import TRUST_FILES as LEDGER_FILES
from .pde_language import MODE, catalog
from .streaming_integrity_v2 import SCAN_POLICY, WatchedSnapshot

BASE = ".runtime/" + MODE
SNAPSHOT = BASE + "/snapshot.json"
LOCK = BASE + "/lock.json"
IMPORTS = (*prior.IMPORTS, "Mathlib.Analysis.InnerProductSpace.Basic")
TREES = prior.TREES
POLICY = {**deepcopy(prior.POLICY), "mode": MODE}
FILES = tuple(
    dict.fromkeys(
        (
            *prior.FILES,
            *LEDGER_FILES,
            prior.SNAPSHOT,
            prior.LOCK,
            "mra/analysis_language.py",
            "mra/calculus_language.py",
            "mra/pde_language.py",
            "mra/pde_language_runtime.py",
            "mra/pde_language_lab.py",
            BASE + "/adoption.json",
            BASE + "/acquisition.json",
        )
    )
)


def safe(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute():
        raise IntegrityError("PDE path must be relative to the project")
    path = root / relative
    if not path.absolute().is_relative_to(root) or path.resolve() != path.absolute():
        raise IntegrityError("PDE path was redirected or escaped")
    return path


def _context(root):
    adoption = strict_json(safe(root, BASE + "/adoption.json").read_text())
    acquired = strict_json(safe(root, BASE + "/acquisition.json").read_text())
    inherited_raw = safe(root, prior.SNAPSHOT).read_bytes()
    inherited = strict_json(inherited_raw.decode())
    lock = strict_json(safe(root, prior.LOCK).read_text())
    if (
        not isinstance(adoption, dict)
        or not isinstance(acquired, dict)
        or not isinstance(inherited, dict)
        or not isinstance(inherited.get("host_files"), dict)
    ):
        raise IntegrityError("Malformed inherited PDE language metadata")
    if (
        adoption.get("mode") != MODE
        or adoption.get("lean") != "4.35.0"
        or adoption.get("kernel") != platform.release()
        or adoption.get("mathlib") != "56a005855ea17e0b0958f73ec801ae140b0a1679"
        or adoption.get("import_scope") != list(IMPORTS)
        or adoption.get("inherited_fingerprint") != digest(inherited)
        or adoption.get("scan_policy") != SCAN_POLICY
        or lock != {"mode": prior.MODE, "sha256": prior.digest_bytes(inherited_raw)}
        or acquired.get("mode") != MODE
        or acquired.get("new_downloads") != 0
        or acquired.get("new_installs") != 0
        or acquired.get("immutable_mathlib_tree") != TREES[1]
        or acquired.get("inherited_snapshot_sha256") != prior.digest_bytes(inherited_raw)
    ):
        raise IntegrityError("Incomplete or incompatible PDE language adoption")
    provenance = {
        "mode": MODE,
        "import_scope": list(IMPORTS),
        "catalog": digest(catalog()),
        "policy": digest(POLICY),
        "scan_policy": digest(SCAN_POLICY),
        "inherited_runtime": digest(inherited),
    }
    return provenance, tuple(inherited["host_files"])


def context(root):
    try:
        return _context(root)
    except IntegrityError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise IntegrityError("PDE language adoption unavailable or malformed") from error


def seal(root):
    root = Path(root).resolve()
    if safe(root, LOCK).exists() or safe(root, SNAPSHOT).exists():
        raise IntegrityError("PDE language seal exists; replacement is forbidden")
    inherited = prior.load(root)
    provenance, hosts = context(root)
    snapshot = WatchedSnapshot.capture(root, TREES, FILES, hosts, provenance)
    snapshot.verify()
    raw = canonical(snapshot.data) + b"\n"
    with safe(root, SNAPSHOT).open("xb") as outgoing:
        outgoing.write(raw)
    with safe(root, LOCK).open("xb") as outgoing:
        outgoing.write(canonical({"mode": MODE, "sha256": prior.digest_bytes(raw)}) + b"\n")
    snapshot._seal_hashes = {SNAPSHOT: prior.digest_bytes(raw), LOCK: file_hash(root / LOCK)}
    snapshot.verify()
    inherited.verify()
    return {"mode": MODE, "fingerprint": snapshot.fingerprint, "monitor": snapshot.last_scan}


def _identity(root):
    """Validate the immutable seal and all explicit source pins; no tree-success cache."""
    root = Path(root).resolve()
    raw = safe(root, SNAPSHOT).read_bytes()
    lock = strict_json(safe(root, LOCK).read_text())
    if lock != {"mode": MODE, "sha256": prior.digest_bytes(raw)}:
        raise IntegrityError("PDE language snapshot lock differs")
    data = strict_json(raw.decode())
    provenance, hosts = context(root)
    if (
        not isinstance(data, dict)
        or data.get("schema_version") != "1"
        or data.get("provenance") != provenance
        or set(data.get("trees", {})) != set(TREES)
        or set(data.get("files", {})) != set(FILES)
        or set(data.get("host_files", {})) != set(hosts)
    ):
        raise IntegrityError("PDE language snapshot scope differs")
    for name, record in data["files"].items():
        if file_hash(safe(root, name)) != record["sha256"]:
            raise IntegrityError("PDE language trusted source changed: " + name)
    return data, {SNAPSHOT: prior.digest_bytes(raw), LOCK: file_hash(root / LOCK)}


def identity(root):
    try:
        return _identity(root)
    except IntegrityError:
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        raise IntegrityError("PDE language seal unavailable or malformed") from error


def load(root):
    data, hashes = identity(root)
    snapshot = WatchedSnapshot(Path(root).resolve(), data)
    snapshot._seal_hashes = hashes
    snapshot.verify()
    return snapshot


Runner = prior._Runner
read_log = prior._read_log
