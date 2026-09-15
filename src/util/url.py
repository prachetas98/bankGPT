from __future__ import annotations

from urllib.parse import urljoin


def resolve_url(base_url: str, url_or_path: str) -> str:
    if url_or_path.startswith("http://") or url_or_path.startswith("https://"):
        return url_or_path
    # entry paths in this project are always absolute-path references (start with "/"),
    # so plain urljoin resolves them against base_url exactly like `new URL(path, base)` in JS.
    return urljoin(base_url, url_or_path)
