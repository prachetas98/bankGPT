from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

from src.types.action import LocatorBundle, ValueSource


@dataclass
class ParameterHint:
    name: str
    description: str


@dataclass
class DiscoveredStep:
    """One executed action from a discovery run, in the shared vocabulary the Recorder turns
    into ArtifactSteps. Deliberately a superset of ArtifactStep (it carries the LLM's rationale
    and raw parameter hints) -- the Recorder's job is projecting this down into the leaner,
    reviewable artifact shape."""

    index: int
    action: Literal["navigate", "click", "type", "select_option", "extract", "wait_for"]
    rationale: str
    risk: Literal["safe", "irreversible"]
    url_after: str

    url: Optional[str] = None
    locator: Optional[LocatorBundle] = None
    value: Optional[ValueSource] = None
    parameter_hint: Optional[ParameterHint] = None
    output_name: Optional[str] = None
    output_description: Optional[str] = None
    transform: Optional[Literal["none", "parse_currency", "parse_integer", "trim"]] = None

    # The literal value actually typed during THIS discovery run -- kept only for the
    # Recorder's own use (templatizing URL-based checkpoints so they don't hardcode this run's
    # value), and never copied into the artifact itself when parameter_hint is set (see recorder.py).
    raw_value: Optional[str] = None
