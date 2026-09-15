"""
RESULT CONTRACT
---------------
The one thing a calling AI agent actually reads. Four disjoint shapes,
matching the taxonomy the assignment calls out explicitly:
 - SuccessResult:         the happy path, with typed outputs.
 - BusinessOutcomeResult: a legitimate, named answer that isn't the happy
                          path (e.g. "no such member") -- NOT an error.
 - FailureResult:         a hard failure -- unrecognized state, or a
                          recognized one whose recovery didn't work.
                          Debuggable by design: always says which step, what
                          we expected, what we saw.
 - EscalatedResult:       replay/discovery paused and handed the live
                          session to a human; the caller gets an
                          intervention_id to track, not a final answer yet.
"""
from __future__ import annotations

from typing import Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, Field


class SuccessResult(BaseModel):
    status: Literal["success"] = "success"
    run_id: str
    capability_id: str
    outputs: Dict[str, Any]
    evidence_dir: str


class BusinessOutcomeResult(BaseModel):
    status: Literal["business_outcome"] = "business_outcome"
    run_id: str
    capability_id: str
    outcome: str = Field(description="e.g. MEMBER_NOT_FOUND, PERMISSION_DENIED")
    detail: Optional[str] = None
    outputs: Dict[str, Any] = Field(default_factory=dict)
    evidence_dir: str


class ErrorDetail(BaseModel):
    step_id: str
    expected: str
    observed: str
    message: str


class FailureResult(BaseModel):
    status: Literal["failure"] = "failure"
    run_id: str
    capability_id: str
    error: ErrorDetail
    evidence_dir: str


class EscalatedResult(BaseModel):
    status: Literal["escalated"] = "escalated"
    run_id: str
    capability_id: str
    intervention_id: str
    reason: str
    step_id: str
    evidence_dir: str
    operator_url: str


RunResult = Union[SuccessResult, BusinessOutcomeResult, FailureResult, EscalatedResult]


class InterventionRequest(BaseModel):
    intervention_id: str
    run_id: str
    capability_id: str
    mode: Literal["discovery", "replay"]
    step_id: Optional[str] = None
    reason: str
    current_url: str
    screenshot_path: Optional[str] = None
    created_at: str
    status: Literal["pending", "resolved"] = "pending"


class InterventionResolution(BaseModel):
    intervention_id: str
    resolved_at: str
    operator_note: str
    action: Literal["resume", "abort"]


class LogEvent(BaseModel):
    ts: str
    run_id: str
    phase: Literal["observe", "decide", "act", "checkpoint", "outcome", "policy", "error", "control"]
    step_id: Optional[str] = None
    message: str
    data: Optional[Dict[str, Any]] = None
