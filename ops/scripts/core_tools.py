"""Local JSON tool bridge for optional agent frameworks; no worker is started."""

from pathlib import Path

from mra.core_tools import main


if __name__ == "__main__":
    raise SystemExit(main(Path(__file__).resolve().parents[2]))
