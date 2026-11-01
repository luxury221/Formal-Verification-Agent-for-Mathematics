"""Fetch pinned public license/manifests for review, never import upstream code."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
DEST = ROOT / "docs/security/upstream"
REPOS = {
    "Euler": "canghaimeng/Euler",
    "Danus": "frenzymath/Danus",
    "lean4": "leanprover/lean4",
    "mathlib4": "leanprover-community/mathlib4",
    "LeanDojo-v2": "lean-dojo/LeanDojo-v2",
    "PyPantograph": "stanford-centaur/PyPantograph",
    "ProofFlow": "Huawei-AI4Math/ProofFlow",
    "DeepSeek-Prover-V2": "deepseek-ai/DeepSeek-Prover-V2",
    "Goedel-Prover-V2": "Goedel-LM/Goedel-Prover-V2",
}
PINS = {"lean4": "v4.35.0", "mathlib4": "56a005855ea17e0b0958f73ec801ae140b0a1679"}


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "MRA-P0-audit"})
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def audit(item: tuple[str, str]) -> dict:
    name, repo = item
    result = {"name": name, "repository": repo, "review_date": "2026-10-19", "files": []}
    try:
        ref = PINS.get(name, "HEAD")
        commit = json.loads(fetch(f"https://api.github.com/repos/{repo}/commits/{ref}"))
        sha = commit["sha"]
        result.update(commit=sha, commit_date=commit["commit"]["committer"]["date"])
        entries = json.loads(fetch(f"https://api.github.com/repos/{repo}/contents?ref={sha}"))
        chosen = [
            e
            for e in entries
            if e["type"] == "file"
            and (
                any(
                    word in e["name"].lower()
                    for word in ("license", "licensing", "copying", "notice")
                )
                or e["name"]
                in (
                    "pyproject.toml",
                    "requirements.txt",
                    "lean-toolchain",
                    "lakefile.lean",
                    "lakefile.toml",
                    "README.md",
                )
            )
        ]
        folder = DEST / name
        folder.mkdir(parents=True, exist_ok=True)
        for entry in chosen:
            url = f"https://raw.githubusercontent.com/{repo}/{sha}/{entry['name']}"
            data = fetch(url)
            (folder / entry["name"]).write_bytes(data)
            result["files"].append(
                {"name": entry["name"], "url": url, "sha256": hashlib.sha256(data).hexdigest()}
            )
        result["status"] = "fetched_requires_review"
    except Exception as error:
        result.update(status="unavailable", error=str(error))
    print(f"{name}: {result['status']}", flush=True)
    return result


def main() -> None:
    DEST.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(audit, REPOS.items()))
    (DEST / "inventory.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


if __name__ == "__main__":
    main()
