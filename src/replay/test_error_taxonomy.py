from typing import List, Optional, Set

from src.replay.error_taxonomy import MatchedOutcome, NoOutcome, attempt_recovery, classify_outcome
from src.surface.surface_adapter import Snapshot, SurfaceAdapter
from src.types.action import LocatorBundle, LocatorCandidate, WaitCondition
from src.types.artifact import RecognizedOutcome, RecoveryAction


def locator(value: str) -> LocatorBundle:
    return LocatorBundle(primary=LocatorCandidate(strategy="text", value=value), fallbacks=[], robustness_note="test")


class FakeAdapter(SurfaceAdapter):
    def __init__(self, visible: Set[str]):
        self.visible = visible
        self.clicked: List[str] = []

    def perceive(self) -> Snapshot:
        return Snapshot(url="http://x", title="x", elements=[])

    def navigate(self, url: str) -> None:
        pass

    def click(self, loc: LocatorBundle) -> None:
        self.clicked.append(loc.primary.value)

    def type_text(self, loc: LocatorBundle, value: str) -> None:
        pass

    def select_option(self, loc: LocatorBundle, value: str) -> None:
        pass

    def extract_text(self, loc: LocatorBundle) -> str:
        return ""

    def is_visible(self, loc: LocatorBundle) -> bool:
        return loc.primary.value in self.visible

    def wait_for(self, condition: WaitCondition) -> bool:
        return True

    def resolve_element_ref(self, ref: str) -> LocatorBundle:
        raise NotImplementedError("not used in this test")

    def current_url(self) -> str:
        return "http://x"

    def screenshot(self, dest_path: str) -> None:
        pass

    def get_handoff_url(self) -> Optional[str]:
        return None

    def close(self) -> None:
        pass


not_found = RecognizedOutcome(
    id="not-found",
    description="no member",
    check_after_step_id="step-2",
    kind="business",
    business_outcome_code="MEMBER_NOT_FOUND",
    locator=locator("No member found"),
)

session_expired = RecognizedOutcome(
    id="session-expired",
    description="expired",
    check_after_step_id="step-2",
    kind="escalate",
    escalation_reason="needs a human",
    locator=locator("Your session has expired"),
)

dismiss_interstitial = RecognizedOutcome(
    id="cookie-banner",
    description="cookie consent banner blocking the flow",
    check_after_step_id="step-2",
    kind="recoverable",
    locator=locator("We use cookies"),
    recovery_action=RecoveryAction(action="click", locator=locator("Dismiss"), max_attempts=1),
)


def test_classify_outcome_returns_none_when_nothing_visible():
    adapter = FakeAdapter(visible=set())
    result = classify_outcome(adapter, "step-2", [not_found, session_expired])
    assert isinstance(result, NoOutcome)


def test_classify_outcome_matches_business_outcome_by_locator():
    adapter = FakeAdapter(visible={"No member found"})
    result = classify_outcome(adapter, "step-2", [not_found, session_expired])
    assert isinstance(result, MatchedOutcome)
    assert result.kind == "business"
    assert result.outcome.business_outcome_code == "MEMBER_NOT_FOUND"


def test_classify_outcome_ignores_outcomes_attached_to_a_different_step():
    adapter = FakeAdapter(visible={"No member found"})
    result = classify_outcome(adapter, "step-99", [not_found])
    assert isinstance(result, NoOutcome)


def test_classify_outcome_matches_escalate_kind_outcome():
    adapter = FakeAdapter(visible={"Your session has expired"})
    result = classify_outcome(adapter, "step-2", [not_found, session_expired])
    assert isinstance(result, MatchedOutcome)
    assert result.kind == "escalate"


def test_attempt_recovery_clicks_the_declared_recovery_locator():
    adapter = FakeAdapter(visible=set())
    ok = attempt_recovery(adapter, dismiss_interstitial)
    assert ok is True
    assert adapter.clicked == ["Dismiss"]


def test_attempt_recovery_returns_false_when_no_recovery_action_declared():
    adapter = FakeAdapter(visible=set())
    ok = attempt_recovery(adapter, not_found)
    assert ok is False
