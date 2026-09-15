from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from src.types.action import LocatorBundle

_DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "allowlist.json"


class PolicyViolationError(Exception):
    def __init__(self, message: str, detail: Optional[dict] = None):
        super().__init__(message)
        self.detail = detail or {}


@dataclass
class ProposedAction:
    action_type: str
    url: Optional[str] = None
    locator: Optional[LocatorBundle] = None


@dataclass
class PolicyDecision:
    allowed: bool
    risk: str  # "safe" | "irreversible"
    requires_approval: bool
    reason: Optional[str] = None


class PolicyEngine:
    """
    Every action taken by the Discovery Agent OR the Replay Executor passes
    through here before it touches the live UI. This is the single seam
    where "the agent must not act outside the allowlist" (3.4) is actually
    enforced -- it is not a convention the caller has to remember to honor.
    """

    def __init__(self, config_path: Path = _DEFAULT_CONFIG_PATH):
        config = json.loads(config_path.read_text(encoding="utf-8"))
        self._allowed_domains = set(config["allowed_domains"])
        self._allowed_routes = [re.compile(p) for p in config["allowed_route_patterns"]]
        self._allowed_action_types = set(config["allowed_action_types"])
        self._risky_name_patterns = [re.compile(p, re.IGNORECASE) for p in config["risky_locator_name_patterns"]]
        self._sensitive_field_patterns = [re.compile(p, re.IGNORECASE) for p in config["sensitive_field_name_patterns"]]

    def check_action(self, action: ProposedAction, explicit_risk: Optional[str] = None) -> PolicyDecision:
        if action.action_type not in self._allowed_action_types:
            raise PolicyViolationError(
                f"Action type '{action.action_type}' is not in the allowlist.", {"action": action}
            )

        if action.action_type == "navigate" and action.url:
            self.assert_url_allowed(action.url)

        risk = explicit_risk or self.classify_risk(action)
        return PolicyDecision(allowed=True, risk=risk, requires_approval=risk == "irreversible")

    def assert_url_allowed(self, raw_url: str) -> None:
        parts = urlsplit(raw_url)
        if not parts.scheme or not parts.netloc:
            raise PolicyViolationError(f"Not a valid absolute URL: '{raw_url}'.")
        if parts.netloc not in self._allowed_domains:
            raise PolicyViolationError(f"Domain '{parts.netloc}' is not in the allowlist.", {"url": raw_url})
        path = parts.path or "/"
        if not any(pattern.search(path) for pattern in self._allowed_routes):
            raise PolicyViolationError(f"Route '{path}' is not in the allowlist.", {"url": raw_url})

    def classify_risk(self, action: ProposedAction) -> str:
        name = ""
        if action.locator is not None:
            name = action.locator.primary.role_name or action.locator.primary.value or ""
        is_risky = any(pattern.search(name) for pattern in self._risky_name_patterns)
        return "irreversible" if is_risky else "safe"

    def is_sensitive_field_name(self, field_name: str) -> bool:
        return any(pattern.search(field_name) for pattern in self._sensitive_field_patterns)
