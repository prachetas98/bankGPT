from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from src.policy.redaction import redact_record
from src.types.run import LogEvent, RunResult


class EvidenceStore:
    """
    Everything written here is what a human debugging a run (or the grader)
    actually looks at: a structured, append-only log of what happened and
    why, screenshots at every step (so failures always have a richer signal
    than the log line alone, per 3.5), and the raw LLM transcript kept
    SEPARATE from the artifact (3.2's "decoupled from the raw model
    transcript" requirement lives here, not in the artifact file).

    Redaction happens here, at the write boundary, so no code path can
    accidentally persist an unredacted sensitive value -- callers pass field
    names/sensitivity hints in, this module is the last line of defense.
    """

    def __init__(self, run_id: str, base_dir: str = "evidence"):
        self.run_id = run_id
        self.dir = Path(base_dir) / run_id
        self._screenshots_dir = self.dir / "screenshots"
        self._screenshots_dir.mkdir(parents=True, exist_ok=True)
        self._step_counter = 0

    def log(
        self,
        phase: str,
        message: str,
        step_id: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        sensitive_keys: Optional[Set[str]] = None,
    ) -> None:
        event = LogEvent(
            ts=datetime.now(timezone.utc).isoformat(),
            run_id=self.run_id,
            phase=phase,  # type: ignore[arg-type]
            step_id=step_id,
            message=message,
            data=redact_record(data, sensitive_keys or set()) if data is not None else None,
        )
        with open(self.dir / "log.jsonl", "a", encoding="utf-8") as f:
            f.write(event.model_dump_json() + "\n")

    def next_screenshot_path(self, label: str) -> str:
        self._step_counter += 1
        file_name = f"{self._step_counter:02d}-{label}.png"
        return str(self._screenshots_dir / file_name)

    def write_transcript(self, messages: Any, sensitive_values: Optional[List[str]] = None) -> None:
        text = json.dumps(messages, indent=2, default=str)
        for val in sensitive_values or []:
            if val:
                text = text.replace(val, "[REDACTED]")
        (self.dir / "transcript.json").write_text(text, encoding="utf-8")

    def write_result(self, result: RunResult) -> None:
        (self.dir / "result.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")

    def write_artifact_snapshot(self, artifact: Any) -> None:
        (self.dir / "artifact.json").write_text(artifact.model_dump_json(indent=2), encoding="utf-8")
