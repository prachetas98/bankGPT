from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Tuple

from src.escalation.operator_server import start_operator_server
from src.evidence.evidence_store import EvidenceStore
from src.storage.run_store import RunStore
from src.surface.surface_adapter import SurfaceAdapter
from src.types.run import InterventionRequest, InterventionResolution


@dataclass
class EscalationContext:
    run_id: str
    capability_id: str
    mode: str  # "discovery" | "replay"
    reason: str
    step_id: Optional[str] = None


class EscalationManager:
    """
    CONTROL-TRANSFER MODEL
    -----------------------
    A run's control lives in exactly one place at a time: this process,
    driving `adapter`, either holds control (AGENT) or has explicitly ceded
    it (HUMAN). `raise_intervention()` is the only way to move AGENT ->
    HUMAN; it freezes the calling thread (the Discovery/Replay loop is a
    plain blocking call, it does not spin up a second actor that could race
    the human), captures evidence of exactly where things stood, and opens a
    real channel (operator server + live CDP handoff URL) into the SAME
    session object the automation was using -- not a new browser, not a
    replayed snapshot. `wait_for_resolution()` blocks until a human writes a
    resolution, then control returns to whoever called it, which
    re-perceives the page before doing anything else (the human may have
    changed the state).
    """

    def __init__(self, run_store: RunStore, operator_port: int):
        self._run_store = run_store
        self._operator_port = operator_port

    def raise_intervention(
        self, ctx: EscalationContext, adapter: SurfaceAdapter, evidence: EvidenceStore
    ) -> Tuple[InterventionRequest, str]:
        intervention_id = str(uuid.uuid4())
        screenshot_path = evidence.next_screenshot_path("escalation")
        adapter.screenshot(screenshot_path)

        req = InterventionRequest(
            intervention_id=intervention_id,
            run_id=ctx.run_id,
            capability_id=ctx.capability_id,
            mode=ctx.mode,  # type: ignore[arg-type]
            step_id=ctx.step_id,
            reason=ctx.reason,
            current_url=adapter.current_url(),
            screenshot_path=screenshot_path,
            created_at=datetime.now(timezone.utc).isoformat(),
            status="pending",
        )
        self._run_store.write_intervention(req)
        evidence.log("control", f"Escalating: {ctx.reason}", step_id=ctx.step_id, data={"intervention_id": intervention_id})

        handle = start_operator_server(req, adapter.get_handoff_url(), self._run_store, self._operator_port)
        evidence.log("control", f"Operator server up at {handle.url}. Live handoff: {adapter.get_handoff_url() or 'n/a'}.")
        print(
            f"\n[ESCALATION] Human input needed.\n"
            f"  Operator page: {handle.url}\n"
            f"  Live session:  {adapter.get_handoff_url() or 'n/a'}\n"
        )

        return req, handle.url

    def wait_for_resolution(self, run_id: str, timeout_s: float = 20 * 60, poll_s: float = 1.0) -> InterventionResolution:
        """Blocks (polling) until the operator server records a resolution, or the timeout elapses."""
        start = time.monotonic()
        while time.monotonic() - start < timeout_s:
            resolution = self._run_store.read_resolution(run_id)
            if resolution is not None:
                return resolution
            time.sleep(poll_s)
        return InterventionResolution(
            intervention_id="timeout",
            resolved_at=datetime.now(timezone.utc).isoformat(),
            operator_note="No human responded within the timeout window.",
            action="abort",
        )
