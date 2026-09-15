from __future__ import annotations

from typing import List

from src.recorder.recorder import KnownOutcomeSeed
from src.types.action import LocatorBundle, LocatorCandidate

# Recognized non-happy-path states for this target app, authored by whoever
# builds/reviews the capability (here: me, since I also built the app). A
# single successful discovery run never observes its own failure states by
# definition, so this is deliberately hand-authored knowledge attached to
# the artifact at record time -- see REPORT.md, "Determinism & error
# handling," for why that's the right seam rather than a gap.

MEMBER_LOOKUP_KNOWN_OUTCOMES: List[KnownOutcomeSeed] = [
    KnownOutcomeSeed(
        id="member-not-found",
        description="Search returned no matching member.",
        kind="business",
        business_outcome_code="MEMBER_NOT_FOUND",
        attach_after="last_click",
        locator=LocatorBundle(
            primary=LocatorCandidate(strategy="text", value="No member found matching ID"),
            fallbacks=[LocatorCandidate(strategy="css", value=".error")],
            robustness_note="Exact banner copy from views.py; falls back to the generic .error banner class if copy changes.",
        ),
    ),
    KnownOutcomeSeed(
        id="permission-denied",
        description="Operator does not have permission to view this member.",
        kind="business",
        business_outcome_code="PERMISSION_DENIED",
        attach_after="last_click",
        locator=LocatorBundle(
            primary=LocatorCandidate(strategy="text", value="Access Denied"),
            fallbacks=[LocatorCandidate(strategy="css", value=".error")],
            robustness_note="Exact banner copy from views.py; falls back to the generic .error banner class if copy changes.",
        ),
    ),
    KnownOutcomeSeed(
        id="session-expired",
        description="Session expired mid-flow and the app redirected to the login screen.",
        kind="escalate",
        escalation_reason="Session expired mid-flow; re-authentication requires a human or a provisioned service credential this artifact does not have.",
        attach_after="last_click",
        locator=LocatorBundle(
            primary=LocatorCandidate(strategy="text", value="Your session has expired"),
            fallbacks=[LocatorCandidate(strategy="css", value=".notice")],
            robustness_note="Exact notice copy from views.py on the login screen; falls back to the generic .notice banner class.",
        ),
    ),
]
