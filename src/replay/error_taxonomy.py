from __future__ import annotations

import time
from dataclasses import dataclass
from typing import List, Literal, Optional, Union

from src.surface.surface_adapter import SurfaceAdapter
from src.types.artifact import RecognizedOutcome


@dataclass
class NoOutcome:
    kind: Literal["none"] = "none"


@dataclass
class MatchedOutcome:
    kind: Literal["business", "recoverable", "escalate"]
    outcome: RecognizedOutcome


ClassifiedOutcome = Union[NoOutcome, MatchedOutcome]


def classify_outcome(adapter: SurfaceAdapter, step_id: str, recognized_outcomes: List[RecognizedOutcome]) -> ClassifiedOutcome:
    """
    THE ERROR TAXONOMY, made concrete.
    When a step's checkpoint doesn't materialize, this is the only place
    that decides what that means. It never guesses from exception messages
    or HTTP status codes -- it checks, in the live DOM, for the specific
    elements the artifact declared as meaningful (RecognizedOutcome.locator),
    matched to the exact step where they're expected. No match after all
    candidates = genuinely unrecognized = hard failure, by construction, not
    by omission.
    """
    candidates = [o for o in recognized_outcomes if o.check_after_step_id == step_id]
    for outcome in candidates:
        if adapter.is_visible(outcome.locator):
            return MatchedOutcome(kind=outcome.kind, outcome=outcome)
    return NoOutcome()


def attempt_recovery(adapter: SurfaceAdapter, outcome: RecognizedOutcome) -> bool:
    """Attempts the declared recovery action once (or up to max_attempts for wait_and_retry).
    Returns whether it ran without raising."""
    recovery = outcome.recovery_action
    if recovery is None:
        return False

    try:
        if recovery.action == "click":
            if recovery.locator is None:
                return False
            adapter.click(recovery.locator)
            return True
        if recovery.action == "wait_and_retry":
            for _ in range(recovery.max_attempts or 1):
                time.sleep(1.5)
            return True
        if recovery.action == "reauthenticate":
            # Deliberately not implemented against a real credential store here -- a bank-grade
            # implementation would call an internal re-auth flow with vaulted service-account
            # credentials. Documented as a cut in REPORT.md; wiring it in touches only this branch.
            return False
    except Exception:  # noqa: BLE001 - a failed recovery attempt is reported by the caller, not raised
        return False
    return False
