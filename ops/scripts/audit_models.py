"""Read pinned model cards/licenses only; never download weights or private data."""

import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request

root = Path(__file__).resolve().parents[2]
models = [
    "deepseek-ai/DeepSeek-Prover-V2-7B",
    "deepseek-ai/DeepSeek-Prover-V2-671B",
    "Goedel-LM/Goedel-Prover-V2-8B",
    "Goedel-LM/Goedel-Prover-V2-32B",
]


def fetch(url):
    with urllib.request.urlopen(
        urllib.request.Request(url, headers={"User-Agent": "MRA-P0-license-audit"}), timeout=30
    ) as response:
        return response.read()


def audit(model):
    record = {"model": model, "date": "2026-10-19", "adopted": False, "weights_downloaded": False}
    try:
        metadata = json.loads(fetch(f"https://huggingface.co/api/models/{model}"))
        pin = metadata["sha"]
        record.update(
            revision=pin, declared_license=metadata.get("cardData", {}).get("license"), files=[]
        )
        destination = root / "docs/security/upstream/model-cards" / model.replace("/", "--")
        destination.mkdir(parents=True, exist_ok=True)
        for entry in metadata.get("siblings", []):
            name = entry["rfilename"]
            if "/" in name or not (
                name.lower().startswith(("license", "copying", "notice")) or name == "README.md"
            ):
                continue
            url = f"https://huggingface.co/{model}/resolve/{pin}/{name}"
            data = fetch(url)
            (destination / name).write_bytes(data)
            record["files"].append(
                {"name": name, "url": url, "sha256": hashlib.sha256(data).hexdigest()}
            )
        record["status"] = "metadata_audited_not_approved_for_deployment"
    except Exception as error:
        record.update(status="unavailable_not_approved", error=str(error))
    print(model + ": " + record["status"], flush=True)
    return record


with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    results = list(executor.map(audit, models))
(root / "docs/security/model-inventory.json").write_text(
    json.dumps(results, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
