from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import List, Optional
from urllib.parse import urljoin, urlsplit

from src.agent.trace import DiscoveredStep
from src.policy.policy_engine import PolicyEngine
from src.types.action import LocatorBundle, WaitCondition, WaitElementVisible, WaitNetworkIdle, WaitUrlMatches
from src.types.artifact import (
    ArtifactStep,
    CapabilityArtifact,
    InputParamSpec,
    OutputFieldSpec,
    Provenance,
    RecognizedOutcome,
    RecoveryAction,
    Target,
)

_policy = PolicyEngine()


@dataclass
class KnownOutcomeSeed:
    """Same shape as RecognizedOutcome minus check_after_step_id, plus where in the trace to
    attach it. The Recorder resolves attach_after into a concrete step id once the artifact's
    steps are built."""

    id: str
    description: str
    kind: str  # "business" | "recoverable" | "escalate"
    locator: LocatorBundle
    attach_after: str  # "last_click" | "last_step"
    business_outcome_code: Optional[str] = None
    recovery_action: Optional[RecoveryAction] = None
    escalation_reason: Optional[str] = None


@dataclass
class RecordParams:
    capability_id: str
    name: str
    description: str
    discovery_run_id: str
    model: str
    goal: str
    app_id: str
    base_url: str
    entry_path: str
    trace: List[DiscoveredStep]
    finish_summary: Optional[str] = None
    final_checkpoint_locator: Optional[LocatorBundle] = None
    known_outcomes: List[KnownOutcomeSeed] = field(default_factory=list)


class Recorder:
    """
    Projects a raw discovery trace down into a reviewable Capability Artifact.
    Three translations happen here, each addressing a concrete part of 3.2:

     1. Literal-vs-parameter: trusts the LLM's `parameter_hint` (set live, at
        the moment it typed a goal-derived value) to build `inputs`, rather
        than guessing post-hoc which typed values "look like" variables.
     2. Locator-as-recorded: every step keeps the FULL ranked locator bundle
        the adapter computed live, fallbacks included -- replay never has to
        re-derive a selector, only re-resolve one that was already reasoned
        about (see surface/locator_strategy.py).
     3. Checkpoint synthesis: the discovery loop only explicitly asserts ONE
        checkpoint (the final "finish" one). Per-step checkpoints are
        synthesized here from observed URL transitions ("did this click
        navigate somewhere new?") because that's the strongest generic
        signal available without over-fitting to this one app. See
        REPORT.md, "Determinism & error handling," for why this is a
        deliberate simplification and what a stronger version would add.
    """

    def build(self, params: RecordParams) -> CapabilityArtifact:
        inputs_by_name: dict[str, InputParamSpec] = {}
        outputs: List[OutputFieldSpec] = []
        steps: List[ArtifactStep] = []
        last_click_step_id: Optional[str] = None
        prev_url = urljoin(params.base_url, params.entry_path)
        # Raw values actually typed during THIS discovery run, longest-first so a value that
        # happens to be a substring of another doesn't get partially replaced first.
        param_raw_values: List[str] = []

        for i, discovered in enumerate(params.trace):
            step_id = f"step-{i + 1}"
            step = self._to_artifact_step(step_id, discovered, prev_url, params.base_url, param_raw_values)
            steps.append(step)
            prev_url = discovered.url_after
            if discovered.action == "click":
                last_click_step_id = step_id

            if discovered.parameter_hint:
                name = discovered.parameter_hint.name
                if name not in inputs_by_name:
                    inputs_by_name[name] = InputParamSpec(
                        name=name,
                        type="string",
                        required=True,
                        description=discovered.parameter_hint.description,
                        sensitive=_policy.is_sensitive_field_name(name),
                    )
                if discovered.raw_value:
                    param_raw_values.append(discovered.raw_value)
                    param_raw_values.sort(key=len, reverse=True)

            if discovered.action == "extract" and discovered.output_name:
                outputs.append(
                    OutputFieldSpec(
                        name=discovered.output_name,
                        type="number" if discovered.transform in ("parse_currency", "parse_integer") else "string",
                        description=discovered.output_description or "",
                        transform=discovered.transform or "none",
                    )
                )

        last_step_id = steps[-1].id if steps else None
        recognized_outcomes: List[RecognizedOutcome] = []
        for seed in params.known_outcomes:
            check_after = (last_click_step_id if seed.attach_after == "last_click" else last_step_id) or last_step_id
            recognized_outcomes.append(
                RecognizedOutcome(
                    id=seed.id,
                    description=seed.description,
                    check_after_step_id=check_after,
                    locator=seed.locator,
                    kind=seed.kind,
                    business_outcome_code=seed.business_outcome_code,
                    recovery_action=seed.recovery_action,
                    escalation_reason=seed.escalation_reason,
                )
            )

        final_checkpoint: WaitCondition = (
            WaitElementVisible(kind="element_visible", locator=params.final_checkpoint_locator, timeout_ms=10_000)
            if params.final_checkpoint_locator
            else WaitNetworkIdle(kind="network_idle", timeout_ms=10_000)
        )

        return CapabilityArtifact(
            schema_version="1.0",
            id=params.capability_id,
            version="1.0.0",
            name=params.name,
            description=params.description,
            approval_state="draft",
            provenance=Provenance(
                discovery_run_id=params.discovery_run_id,
                model=params.model,
                created_at=datetime.now(timezone.utc).isoformat(),
                recorded_goal=params.goal,
            ),
            target=Target(app_id=params.app_id, base_url=params.base_url, entry_path=params.entry_path),
            inputs=list(inputs_by_name.values()),
            outputs=outputs,
            steps=steps,
            recognized_outcomes=recognized_outcomes,
            final_checkpoint=final_checkpoint,
        )

    def _to_artifact_step(
        self,
        step_id: str,
        discovered: DiscoveredStep,
        prev_url: str,
        base_url: str,
        param_raw_values: List[str],
    ) -> ArtifactStep:
        templated_url = discovered.url.replace(base_url, "{{base_url}}") if discovered.url and discovered.url.startswith(base_url) else discovered.url

        return ArtifactStep(
            id=step_id,
            description=discovered.rationale,
            action=discovered.action,
            risk=discovered.risk,
            requires_approval=discovered.risk == "irreversible",
            url=templated_url,
            locator=discovered.locator,
            value=discovered.value,
            output_name=discovered.output_name,
            wait=WaitNetworkIdle(kind="network_idle", timeout_ms=8000) if discovered.action == "wait_for" else None,
            checkpoint=self._synthesize_checkpoint(discovered, prev_url, param_raw_values),
            timeout_ms=10_000,
        )

    def _synthesize_checkpoint(self, discovered: DiscoveredStep, prev_url: str, param_raw_values: List[str]) -> WaitCondition:
        if discovered.action == "navigate":
            return WaitUrlMatches(kind="url_matches", pattern=_templatized_path_pattern(discovered.url, param_raw_values), timeout_ms=10_000)
        if discovered.action == "click":
            if discovered.url_after != prev_url:
                return WaitUrlMatches(kind="url_matches", pattern=_templatized_path_pattern(discovered.url_after, param_raw_values), timeout_ms=10_000)
            return WaitNetworkIdle(kind="network_idle", timeout_ms=10_000)
        if discovered.action in ("type", "select_option", "extract"):
            return WaitElementVisible(kind="element_visible", locator=discovered.locator, timeout_ms=5000)
        # wait_for
        return WaitNetworkIdle(kind="network_idle", timeout_ms=8000)


def _pathname_of(url: str) -> str:
    try:
        return urlsplit(url).path or "/"
    except ValueError:
        return url


def _escape_regexp(s: str) -> str:
    return re.escape(s)


def _templatized_path_pattern(url: str, param_raw_values: List[str]) -> str:
    """
    Turns "this run's" concrete path into a reusable pattern by replacing any
    substring that matches a value we know was supplied as a PARAMETER (e.g.
    the member id typed a few steps earlier) with a wildcard. Without this, a
    checkpoint like "/member/12345" would only ever match a replay called
    with member_id=12345 -- the exact bug that would otherwise make every
    parameterized artifact non-reusable. Same idea as the canonicalization
    stretch goal (/item/12345 -> /item/:id), applied where correctness
    actually requires it rather than as an optional extra.
    """
    escaped = _escape_regexp(_pathname_of(url))
    for raw in param_raw_values:
        if not raw:
            continue
        escaped = escaped.replace(_escape_regexp(raw), "[^/]+")
    return escaped
