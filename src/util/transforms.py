from __future__ import annotations

import re
from typing import Literal, Union

Transform = Literal["none", "parse_currency", "parse_integer", "trim"]

_NON_NUMERIC = re.compile(r"[^0-9.\-]")
_NON_INTEGER = re.compile(r"[^0-9\-]")


def apply_transform(raw: str, transform: Transform = "none") -> Union[str, float, int]:
    """Shared by discovery (extract tool) and replay (extract step) so a recorded artifact
    behaves identically in both."""
    if transform == "none":
        return raw
    if transform == "trim":
        return raw.strip()
    if transform == "parse_currency":
        cleaned = _NON_NUMERIC.sub("", raw)
        try:
            return float(cleaned)
        except ValueError as exc:
            raise ValueError(f'Could not parse currency from "{raw}".') from exc
    if transform == "parse_integer":
        cleaned = _NON_INTEGER.sub("", raw)
        try:
            return int(cleaned)
        except ValueError as exc:
            raise ValueError(f'Could not parse integer from "{raw}".') from exc
    raise ValueError(f"Unknown transform '{transform}'.")
