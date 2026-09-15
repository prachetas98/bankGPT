import pytest

from src.policy.policy_engine import PolicyEngine, PolicyViolationError, ProposedAction
from src.types.action import LocatorBundle, LocatorCandidate


def make_locator(role_name: str) -> LocatorBundle:
    return LocatorBundle(
        primary=LocatorCandidate(strategy="role", value="button", role_name=role_name),
        fallbacks=[],
        robustness_note="",
    )


def test_allows_navigate_within_allowlisted_domain_and_route():
    policy = PolicyEngine()
    decision = policy.check_action(ProposedAction(action_type="navigate", url="http://localhost:4000/search"))
    assert decision.allowed is True
    assert decision.risk == "safe"


def test_blocks_navigate_to_domain_outside_allowlist():
    policy = PolicyEngine()
    with pytest.raises(PolicyViolationError):
        policy.check_action(ProposedAction(action_type="navigate", url="http://evil.example.com/search"))


def test_blocks_navigate_to_route_outside_allowlist():
    policy = PolicyEngine()
    with pytest.raises(PolicyViolationError):
        policy.check_action(ProposedAction(action_type="navigate", url="http://localhost:4000/admin/delete-everything"))


def test_blocks_action_type_outside_allowlist():
    policy = PolicyEngine()
    with pytest.raises(PolicyViolationError):
        policy.check_action(ProposedAction(action_type="download_file"))


def test_classifies_confirm_click_as_irreversible_and_requiring_approval():
    policy = PolicyEngine()
    decision = policy.check_action(ProposedAction(action_type="click", locator=make_locator("Confirm")))
    assert decision.risk == "irreversible"
    assert decision.requires_approval is True


def test_classifies_ordinary_click_as_safe():
    policy = PolicyEngine()
    decision = policy.check_action(ProposedAction(action_type="click", locator=make_locator("Search")))
    assert decision.risk == "safe"
    assert decision.requires_approval is False


def test_identifies_sensitive_field_names():
    policy = PolicyEngine()
    assert policy.is_sensitive_field_name("password") is True
    assert policy.is_sensitive_field_name("accountNumber") is True
    assert policy.is_sensitive_field_name("memberId") is False
