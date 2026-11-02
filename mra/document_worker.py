"""Fixed child entrypoint; never executes uploaded TeX, HTML or model code."""

from pathlib import Path
import resource
import signal
import sys


def main():
    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024**3, 2 * 1024**3))
    resource.setrlimit(resource.RLIMIT_CPU, (120, 120))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32 * 1024**2, 32 * 1024**2))
    resource.setrlimit(resource.RLIMIT_NOFILE, (128, 128))
    signal.alarm(290)
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from mra.document_extract import extract
    from mra.document_intake import DocumentStore
    from mra.research_flow import atomic

    root, runtime, key, work_name = sys.argv[1:]
    store = DocumentStore(root, runtime_root=runtime)
    directory = store.directory(key)
    if not work_name.startswith(".work-") or "/" in work_name or "\\" in work_name:
        raise ValueError("Invalid document work directory")
    work = directory / work_name
    if work.resolve() != work:
        raise ValueError("Document work directory was redirected")
    try:
        row, inputs = store.checked_inputs(key)
        if row["work"] != work_name or row["state"] != "RUNNING":
            raise ValueError("Document extraction is not the active request")

        def progress(stage, page, total):
            atomic(work / "progress.json", {"stage": stage, "page": page, "total": total})

        value = extract(runtime, directory, inputs, work, progress)
        atomic(work / "draft.json", value)
        return 0
    except Exception as error:
        atomic(work / "error.json", {"error": type(error).__name__ + ": " + str(error)[:1800]})
        return 2


if __name__ == "__main__":
    sys.exit(main())
