from __future__ import annotations

import json
from pathlib import Path
from typing import List

from src.types.artifact import CapabilityArtifact, validate_artifact


class ArtifactStore:
    """Flat-file storage: one JSON file per capability id (latest version wins on disk, prior
    versions are whatever the artifact's own `version` field says they were -- see REPORT.md for
    why a full version-history store is a cut, not a missing requirement). No database: a
    handful of files is the right amount of infrastructure for a single-tenant take-home."""

    def __init__(self, directory: str = "artifacts"):
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def save(self, artifact: CapabilityArtifact) -> Path:
        file = self._dir / f"{artifact.id}.json"
        file.write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
        return file

    def load(self, capability_id: str) -> CapabilityArtifact:
        file = self._dir / f"{capability_id}.json"
        if not file.exists():
            raise FileNotFoundError(f"No artifact found for capability '{capability_id}' at {file}.")
        return validate_artifact(json.loads(file.read_text(encoding="utf-8")))

    def list(self) -> List[str]:
        if not self._dir.exists():
            return []
        return [f.stem for f in self._dir.glob("*.json")]
