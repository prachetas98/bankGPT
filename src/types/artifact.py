"""
ARTIFACT SCHEMA
---------------
A Capability Artifact is the contract between "an AI agent that wants
something done" and "the deterministic engine that does it." It is
deliberately NOT a transcript of the discovery run -- the raw LLM transcript
lives separately in /evidence. This is the distilled, reviewable, versioned,
typed thing an agent calls by name.

Design goals, in priority order:
 1. A human reviewer can read it and know exactly what it does, without
    replaying it or reading code.
 2. A calling agent can validate its inputs/outputs mechanically (hence
    Pydantic models, not free-form JSON).
 3. Replay can be deterministic: every locator has ranked fallbacks, every
    step has a checkpoint, and every known non-happy-path state the
    discovery run encountered (or the author anticipated) is declared up
    front as a "recognized outcome" rather than discovered as a crash.
 4. It travels across tenants: nothing here is baked to one tenant except
    target.base_url, which is the one field an override is expected to
    replace (see REPORT.md, Heterogeneity & multi-tenant).
"""
from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

from .action import LocatorBundle, WaitCondition, ValueSource

RiskLevel = Literal["safe", "irreversible"]
ParamType = Literal["string", "number", "boolean"]


class InputParamSpec(BaseModel):
    name: str
    type: ParamType
    required: bool = True
    description: str
    example: Optional[str] = None
    sensitive: bool = Field(
        default=False,
        description="If true, this value is redacted in logs/evidence and never echoed back in outputs.",
    )


class OutputFieldSpec(BaseModel):
    name: str
    type: ParamType
    description: str
    transform: Literal["none", "parse_currency", "parse_integer", "trim"] = "none"


class ArtifactStep(BaseModel):
    """A single deterministic step. Every step asserts a checkpoint after acting --
    "did we actually get where we expected," not "did the click not throw." """

    id: str
    description: str = Field(description="Human-readable purpose of this step, for reviewers.")
    action: Literal["navigate", "click", "type", "select_option", "extract", "wait_for"]
    risk: RiskLevel = "safe"
    requires_approval: bool = Field(
        default=False,
        description=(
            "If true, replay MUST pause and raise an intervention request before executing this "
            "step, regardless of policy config. Set on irreversible steps identified during "
            "recording (e.g. a final 'Confirm' submit)."
        ),
    )

    # navigate
    url: Optional[str] = Field(default=None, description="Used when action == 'navigate'. May contain {{base_url}}.")

    # click / type / select_option / extract target
    locator: Optional[LocatorBundle] = None

    # type / select_option
    value: Optional[ValueSource] = None

    # extract
    output_name: Optional[str] = Field(default=None, description="Name of the OutputFieldSpec this step populates.")

    # wait_for
    wait: Optional[WaitCondition] = None

    checkpoint: WaitCondition = Field(
        description="What must be true for this step to count as successful. Replay fails fast if this never becomes true."
    )

    timeout_ms: int = 10_000


class RecoveryAction(BaseModel):
    action: Literal["click", "reauthenticate", "wait_and_retry"]
    locator: Optional[LocatorBundle] = None
    max_attempts: int = 1


class RecognizedOutcome(BaseModel):
    """A state the flow can land in that ISN'T the happy path, but is known and named ahead of
    time. This is the schema-level answer to "business outcome vs. recoverable condition vs.
    hard failure" -- the taxonomy is data, not a pile of try/except guesses at replay time."""

    id: str
    description: str
    check_after_step_id: str = Field(description="Outcome is checked immediately after this step executes.")
    locator: LocatorBundle = Field(description="Element whose presence signals this outcome (e.g. an error banner).")
    kind: Literal["business", "recoverable", "escalate"]

    # business: a legitimate answer for the caller, e.g. MEMBER_NOT_FOUND.
    business_outcome_code: Optional[str] = None
    # recoverable: apply this action then re-check the ORIGINAL step's checkpoint once.
    recovery_action: Optional[RecoveryAction] = None
    # escalate: reason surfaced to the human in the intervention request.
    escalation_reason: Optional[str] = None


class Provenance(BaseModel):
    discovery_run_id: str
    model: str
    created_at: str
    recorded_goal: str


class Target(BaseModel):
    app_id: str
    base_url: str = Field(
        description="The ONE field expected to differ per tenant deployment of the same vendor app. See REPORT.md, Multi-tenant reuse."
    )
    entry_path: str


class CapabilityArtifact(BaseModel):
    schema_version: Literal["1.0"]
    id: str = Field(description="Stable capability id, e.g. 'member-savings-lookup'. Callable name for agents.")
    version: str = Field(description="Semver of this artifact. Bump on any change to steps/locators/contract.")
    name: str
    description: str
    approval_state: Literal["draft", "approved"] = Field(
        default="draft",
        description="Unattended replay should be gated on 'approved' (see REPORT.md stretch: confidence & approval).",
    )

    provenance: Provenance
    target: Target

    inputs: List[InputParamSpec]
    outputs: List[OutputFieldSpec]

    steps: List[ArtifactStep] = Field(min_length=1)
    recognized_outcomes: List[RecognizedOutcome] = Field(default_factory=list)

    final_checkpoint: WaitCondition = Field(description="Confirms the whole goal was reached, not just the last step.")


def validate_artifact(data: dict) -> CapabilityArtifact:
    return CapabilityArtifact.model_validate(data)
