"""
Shared action/locator/wait vocabulary used by three consumers that must never
diverge: the Discovery Agent (the LLM picks one of these per turn), the
Recorder (turns a sequence of these into artifact steps), and the Replay
Executor (executes artifact steps built from these). One shared vocabulary is
what makes "record what the LLM did, replay it later" a direct translation
instead of a lossy reinterpretation.
"""
from __future__ import annotations

from typing import Annotated, List, Literal, Optional, Union

from pydantic import BaseModel, Field

ActionType = Literal[
    "navigate", "click", "type", "select_option", "extract", "wait_for", "finish", "give_up"
]

# One concrete way to find an element. Ranked strategies are tried in order at
# replay time until one resolves to exactly one element.
LocatorStrategyType = Literal["role", "label", "text", "css", "xpath"]


class LocatorCandidate(BaseModel):
    strategy: LocatorStrategyType
    value: str = Field(description="Strategy-specific locator value, e.g. role name, CSS selector, XPath.")
    role_name: Optional[str] = Field(
        default=None, description="Accessible name, only meaningful when strategy == 'role'."
    )


class LocatorBundle(BaseModel):
    """A ranked bundle of ways to find the same element, plus why it was ranked that way."""

    primary: LocatorCandidate
    fallbacks: List[LocatorCandidate] = Field(default_factory=list)
    robustness_note: str = Field(description="Why the primary strategy was chosen over the fallbacks.")
    element_ref: Optional[str] = Field(
        default=None,
        description="Adapter-internal ref (e.g. 'e7') used only during a live discovery run; not meaningful at replay.",
    )


class ValueLiteral(BaseModel):
    source: Literal["literal"]
    value: str


class ValueParam(BaseModel):
    source: Literal["param"]
    param: str


# Where a value written into a field comes from. "param" makes the field a
# declared input of the capability; "literal" bakes in a fixed value that was
# part of the flow itself (e.g. a fixed dropdown choice), not caller data.
ValueSource = Annotated[Union[ValueLiteral, ValueParam], Field(discriminator="source")]


class WaitNetworkIdle(BaseModel):
    kind: Literal["network_idle"]
    timeout_ms: int = 10_000


class WaitElementVisible(BaseModel):
    kind: Literal["element_visible"]
    locator: LocatorBundle
    timeout_ms: int = 10_000


class WaitElementHidden(BaseModel):
    kind: Literal["element_hidden"]
    locator: LocatorBundle
    timeout_ms: int = 10_000


class WaitUrlMatches(BaseModel):
    kind: Literal["url_matches"]
    pattern: str
    timeout_ms: int = 10_000


WaitCondition = Annotated[
    Union[WaitNetworkIdle, WaitElementVisible, WaitElementHidden, WaitUrlMatches],
    Field(discriminator="kind"),
]
