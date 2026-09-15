from __future__ import annotations

import re
from typing import Any, Dict, Optional, Set

from src.policy.policy_engine import PolicyEngine

_REDACTED = "[REDACTED]"

# Patterns for values that look sensitive regardless of the field name they came
# from (defense in depth -- field-name matching alone misses cases like a
# free-text field that happens to contain a card number).
_VALUE_PATTERNS = [
    re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),  # SSN-shaped
    re.compile(r"\b\d{13,19}\b"),  # card/account-number-shaped
    re.compile(r"\bsk-[A-Za-z0-9_-]{10,}\b"),  # API-key-shaped
]

_default_policy = PolicyEngine()


def redact_value(
    value: Any,
    field_name: Optional[str] = None,
    force_sensitive: bool = False,
    policy: PolicyEngine = _default_policy,
) -> Any:
    """Applied before ANYTHING is written to evidence, artifacts, or logs. Two independent
    checks: is the field itself declared sensitive (by name, via the allowlist config or an
    artifact InputParamSpec.sensitive flag), or does the value itself look sensitive regardless
    of what field it's in."""
    if value is None:
        return value
    text = str(value)

    field_flagged = force_sensitive or (field_name is not None and policy.is_sensitive_field_name(field_name))
    if field_flagged:
        return _REDACTED

    if any(pattern.search(text) for pattern in _VALUE_PATTERNS):
        return _REDACTED

    return value


def redact_record(
    record: Dict[str, Any],
    sensitive_names: Optional[Set[str]] = None,
    policy: PolicyEngine = _default_policy,
) -> Dict[str, Any]:
    """Shallow-redacts a plain dict of named fields (e.g. a params bag before logging)."""
    sensitive_names = sensitive_names or set()
    return {
        key: redact_value(val, field_name=key, force_sensitive=key in sensitive_names, policy=policy)
        for key, val in record.items()
    }
