"""Fixed isolated entry point for kernel-only paper candidates."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from mra.paper_kernel import worker_main

if __name__ == "__main__":
    worker_main()
