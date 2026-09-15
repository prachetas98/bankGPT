from __future__ import annotations

import sys

from dotenv import load_dotenv

load_dotenv()

from src.cli.discover import discover_command
from src.cli.replay import replay_command
from src.cli.resume import resume_command
from src.storage.artifact_store import ArtifactStore


def main() -> None:
    argv = sys.argv[1:]
    command = argv[0] if argv else None
    rest = argv[1:]

    if command == "discover":
        discover_command(rest)
    elif command == "replay":
        replay_command(rest)
    elif command == "resume":
        resume_command(rest)
    elif command == "list-artifacts":
        ids = ArtifactStore().list()
        print("\n".join(ids) if ids else "(no artifacts saved yet -- run `python -m src.cli discover` first)")
    else:
        print("Usage: python -m src.cli <discover|replay|resume|list-artifacts> [--flags]")
        print("See README.md for the full command reference.")
        sys.exit(1 if command else 0)


if __name__ == "__main__":
    try:
        main()
    except Exception as err:  # noqa: BLE001 - top-level CLI error boundary
        print(f"Error: {err}", file=sys.stderr)
        sys.exit(1)
