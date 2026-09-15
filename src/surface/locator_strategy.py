from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from src.types.action import LocatorBundle, LocatorCandidate

_CSS_ESCAPE_RE = re.compile(r'([ #.;?%&,+*~\':"!^$\[\]()=>|/])')


@dataclass
class ElementDescriptor:
    """Everything this module needs to know about one DOM element, gathered by the surface
    adapter (Playwright-specific extraction lives there). Kept framework-free here so the
    ranking policy itself is portable to a future non-Playwright adapter (desktop AX tree,
    legacy frameset adapter, etc.)."""

    tag_name: str
    css_path: str  # structural fallback, always computable
    xpath: str  # last-resort fallback, always computable
    role: Optional[str] = None
    accessible_name: Optional[str] = None
    id_attr: Optional[str] = None
    name_attr: Optional[str] = None
    test_id_attr: Optional[str] = None
    text: Optional[str] = None


def build_locator_bundle(desc: ElementDescriptor) -> LocatorBundle:
    """
    Robustness ranking, most to least stable, and why:
     1. test_id      -- present only when devs added it on purpose. Essentially
                         never true in the legacy apps this system targets, but
                         when present it's the strongest signal, so check first.
     2. role+name     -- survives CSS/DOM refactors and re-theming because it's
                         derived from what a screen reader would announce, not
                         from markup structure. This is the default primary
                         locator for the environment described in the brief.
     3. label         -- form controls found by their associated <label> text;
                         stable under markup changes, common in legacy forms
                         that have visible labels but no ARIA.
     4. id attribute  -- stable IF the app assigns meaningful ids; some legacy
                         server-rendered apps do, many don't.
     5. text content  -- brittle under copy changes but readable and often the
                         only thing available on non-semantic legacy markup.
     6. css structural path -- brittle under any layout change; kept as a
                         fallback of last resort before...
     7. xpath         -- absolute positional path; most brittle, but always
                         computable, so it guarantees the bundle is never empty.
    """
    candidates: List[LocatorCandidate] = []

    if desc.test_id_attr:
        candidates.append(LocatorCandidate(strategy="css", value=f'[data-testid="{desc.test_id_attr}"]'))
    # "text" is a synthetic role for read-only display data (see playwright_adapter.py) --
    # not a real ARIA role Playwright's getByRole understands, so it's never worth trying as
    # a locator strategy; skip straight to id/css/xpath for these instead of a doomed attempt.
    if desc.role and desc.role != "text" and desc.accessible_name:
        candidates.append(LocatorCandidate(strategy="role", value=desc.role, role_name=desc.accessible_name))
    if desc.accessible_name and desc.tag_name in ("input", "select", "textarea"):
        candidates.append(LocatorCandidate(strategy="label", value=desc.accessible_name))
    if desc.id_attr:
        candidates.append(LocatorCandidate(strategy="css", value=f"#{_css_escape(desc.id_attr)}"))
    if desc.name_attr:
        candidates.append(LocatorCandidate(strategy="css", value=f'{desc.tag_name}[name="{desc.name_attr}"]'))
    if desc.text and 0 < len(desc.text.strip()) < 80:
        candidates.append(LocatorCandidate(strategy="text", value=desc.text.strip()))
    candidates.append(LocatorCandidate(strategy="css", value=desc.css_path))
    candidates.append(LocatorCandidate(strategy="xpath", value=desc.xpath))

    primary = candidates[0]
    fallbacks = candidates[1:]

    return LocatorBundle(primary=primary, fallbacks=fallbacks, robustness_note=_explain(primary, desc))


def _explain(primary: LocatorCandidate, desc: ElementDescriptor) -> str:
    if primary.strategy == "css":
        if primary.value.startswith("[data-testid"):
            return "Explicit test id present -- strongest available signal."
        if primary.value.startswith("#"):
            return "Stable id attribute; degrades to name/structural CSS if the id is ever removed."
        return f"Structural/attribute CSS fallback used because no accessible role+name or id was available on this <{desc.tag_name}>."
    if primary.strategy == "role":
        return (
            "Accessible role + name: stable across CSS refactors and re-theming, which is the "
            "common failure mode on enterprise apps that reskin without changing behavior."
        )
    if primary.strategy == "label":
        return "Matched via associated <label> text -- reliable on legacy server-rendered forms that have visible labels but no ARIA attributes."
    if primary.strategy == "text":
        return "Matched by visible text content; brittle to copy changes, used because no structural signal was available."
    # xpath
    return "Absolute structural path -- last resort, most brittle to markup changes, kept only to guarantee a resolvable fallback."


def _css_escape(value: str) -> str:
    return _CSS_ESCAPE_RE.sub(r"\\\1", value)
