import re

from src.agent.trace import DiscoveredStep, ParameterHint
from src.recorder.recorder import RecordParams, Recorder
from src.types.action import LocatorBundle, LocatorCandidate, ValueParam

BASE_URL = "http://localhost:4000"


def loc(css: str) -> LocatorBundle:
    return LocatorBundle(primary=LocatorCandidate(strategy="css", value=css), fallbacks=[], robustness_note="test")


def test_templatizes_checkpoint_url_pattern_so_it_isnt_hardcoded_to_this_runs_value():
    trace = [
        DiscoveredStep(
            index=0,
            action="type",
            rationale="type member id",
            risk="safe",
            locator=loc('input[name="memberId"]'),
            value=ValueParam(source="param", param="member_id"),
            parameter_hint=ParameterHint(name="member_id", description="member id"),
            raw_value="12345",
            url_after=f"{BASE_URL}/search",
        ),
        DiscoveredStep(
            index=1,
            action="click",
            rationale="submit search",
            risk="safe",
            locator=loc('input[type="submit"]'),
            url_after=f"{BASE_URL}/member/12345",
        ),
    ]

    artifact = Recorder().build(
        RecordParams(
            capability_id="test-capability",
            name="Test",
            description="Test",
            discovery_run_id="run-1",
            model="test-model",
            goal="look up member 12345",
            app_id="bankgpt-member-services",
            base_url=BASE_URL,
            entry_path="/search",
            trace=trace,
        )
    )

    click_step = artifact.steps[1]
    assert click_step.checkpoint.kind == "url_matches"
    assert click_step.checkpoint.pattern == "/member/[^/]+"
    assert "12345" not in click_step.checkpoint.pattern
    assert re.search(click_step.checkpoint.pattern, f"{BASE_URL}/member/67890")


def test_trusts_parameter_hint_to_build_declared_inputs_once_per_name():
    trace = [
        DiscoveredStep(
            index=0,
            action="type",
            rationale="type member id",
            risk="safe",
            locator=loc('input[name="memberId"]'),
            value=ValueParam(source="param", param="member_id"),
            parameter_hint=ParameterHint(name="member_id", description="member id"),
            raw_value="12345",
            url_after=f"{BASE_URL}/search",
        )
    ]

    artifact = Recorder().build(
        RecordParams(
            capability_id="test-capability",
            name="Test",
            description="Test",
            discovery_run_id="run-1",
            model="test-model",
            goal="look up member 12345",
            app_id="bankgpt-member-services",
            base_url=BASE_URL,
            entry_path="/search",
            trace=trace,
        )
    )

    assert len(artifact.inputs) == 1
    assert artifact.inputs[0].name == "member_id"


def test_flags_parameter_sensitive_when_name_matches_sensitive_pattern():
    trace = [
        DiscoveredStep(
            index=0,
            action="type",
            rationale="type password",
            risk="safe",
            locator=loc('input[name="password"]'),
            value=ValueParam(source="param", param="password"),
            parameter_hint=ParameterHint(name="password", description="sensitive value"),
            raw_value="hunter2",
            url_after=f"{BASE_URL}/search",
        )
    ]

    artifact = Recorder().build(
        RecordParams(
            capability_id="test-capability",
            name="Test",
            description="Test",
            discovery_run_id="run-1",
            model="test-model",
            goal="log in",
            app_id="bankgpt-member-services",
            base_url=BASE_URL,
            entry_path="/login",
            trace=trace,
        )
    )

    assert artifact.inputs[0].sensitive is True
