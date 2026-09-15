from __future__ import annotations

from typing import Dict, List


def parse_args(argv: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    i = 0
    while i < len(argv):
        token = argv[i]
        if token.startswith("--"):
            key = token[2:]
            nxt = argv[i + 1] if i + 1 < len(argv) else None
            if nxt is not None and not nxt.startswith("--"):
                out[key] = nxt
                i += 2
                continue
            out[key] = "true"
        i += 1
    return out


def required(args: Dict[str, str], key: str) -> str:
    v = args.get(key)
    if not v:
        raise ValueError(f"Missing required --{key} argument.")
    return v
