from __future__ import annotations

from pathlib import Path
from typing import Optional

from src.types.run import InterventionRequest, InterventionResolution


class RunStore:
    """Small run-state files used by the escalation seam to hand off between processes/commands."""

    def __init__(self, directory: str = "runs"):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def write_intervention(self, req: InterventionRequest) -> Path:
        file = self._dir / f"{req.run_id}.intervention.json"
        file.write_text(req.model_dump_json(indent=2), encoding="utf-8")
        return file

    def read_intervention(self, run_id: str) -> Optional[InterventionRequest]:
        file = self._dir / f"{run_id}.intervention.json"
        if not file.exists():
            return None
        return InterventionRequest.model_validate_json(file.read_text(encoding="utf-8"))

    def write_resolution(self, run_id: str, resolution: InterventionResolution) -> Path:
        file = self._dir / f"{run_id}.resolution.json"
        file.write_text(resolution.model_dump_json(indent=2), encoding="utf-8")
        return file

    def read_resolution(self, run_id: str) -> Optional[InterventionResolution]:
        file = self._dir / f"{run_id}.resolution.json"
        if not file.exists():
            return None
        return InterventionResolution.model_validate_json(file.read_text(encoding="utf-8"))
