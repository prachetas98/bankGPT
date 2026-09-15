from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional

from src.types.action import LocatorBundle, WaitCondition


@dataclass
class PerceivedElement:
    ref: str  # ephemeral id, valid only for this Snapshot ("e1", "e2", ...)
    role: str
    name: str
    value: Optional[str] = None


@dataclass
class Snapshot:
    url: str
    title: str
    elements: List[PerceivedElement] = field(default_factory=list)
    banner_text: Optional[str] = None  # salient non-interactive text (errors, confirmations)


class SurfaceAdapter(ABC):
    """
    SURFACE ABSTRACTION
    -------------------
    Everything above this interface (Discovery Agent, Recorder, Replay
    Executor, Policy Engine) is surface-agnostic. Everything below it knows
    about one concrete way of perceiving/acting on a UI.

    This implementation ships one adapter -- PlaywrightAdapter, backed by the
    DOM of a Chromium page. The seam is deliberately narrow
    (perceive/act/wait/extract/screenshot) so a future adapter -- a legacy
    frameset walker, or an OS-level accessibility adapter for a desktop app --
    can implement the same interface without any change to the layers above.
    See REPORT.md, "Heterogeneity & multi-tenant."
    """

    @abstractmethod
    def perceive(self) -> Snapshot: ...

    @abstractmethod
    def navigate(self, url: str) -> None: ...

    @abstractmethod
    def click(self, locator: LocatorBundle) -> None: ...

    @abstractmethod
    def type_text(self, locator: LocatorBundle, value: str) -> None: ...

    @abstractmethod
    def select_option(self, locator: LocatorBundle, value: str) -> None: ...

    @abstractmethod
    def extract_text(self, locator: LocatorBundle) -> str: ...

    @abstractmethod
    def is_visible(self, locator: LocatorBundle) -> bool: ...

    @abstractmethod
    def wait_for(self, condition: WaitCondition) -> bool: ...

    @abstractmethod
    def resolve_element_ref(self, ref: str) -> LocatorBundle:
        """Resolves an ephemeral perception ref (from the most recent Snapshot) to a full,
        ranked LocatorBundle."""
        ...

    @abstractmethod
    def current_url(self) -> str: ...

    @abstractmethod
    def screenshot(self, dest_path: str) -> None: ...

    @abstractmethod
    def get_handoff_url(self) -> Optional[str]:
        """For the escalation handoff: a URL a human can open to drive the SAME live session.
        None if not supported."""
        ...

    @abstractmethod
    def close(self) -> None: ...
