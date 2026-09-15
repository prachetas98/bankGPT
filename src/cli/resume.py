from __future__ import annotations

from datetime import datetime, timezone
from typing import List

from src.cli.argv import parse_args, required
from src.storage.run_store import RunStore
from src.types.run import InterventionResolution


def resume_command(argv: List[str]) -> None:
    """
    Scriptable alternative to clicking "Resume"/"Abort" on the operator page
    -- writes the exact same resolution file the operator server's POST
    handler writes, so a blocked discover/replay process picks it up
    identically either way. Useful for driving the handoff from a
    terminal/CI context instead of a browser click.
    """
    args = parse_args(argv)
    run_id = required(args, "run")
    action = "abort" if args.get("action") == "abort" else "resume"
    note = args.get("note", "")

    run_store = RunStore()
    intervention = run_store.read_intervention(run_id)
    if intervention is None:
        raise RuntimeError(f"No pending intervention found for run '{run_id}'. Check runs/{run_id}.intervention.json.")

    run_store.write_resolution(
        run_id,
        InterventionResolution(
            intervention_id=intervention.intervention_id,
            resolved_at=datetime.now(timezone.utc).isoformat(),
            operator_note=note,
            action=action,  # type: ignore[arg-type]
        ),
    )
    note_suffix = f' ("{note}")' if note else ""
    print(f"Wrote resolution for run '{run_id}': {action}{note_suffix}.")
