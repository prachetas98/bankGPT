from __future__ import annotations

from dataclasses import dataclass

# Fake data + deterministic sentinel IDs. Kept in-memory and named, not
# randomized, so the SAME goal/replay produces the SAME evidence every time
# -- reproducibility matters more here than realism for a take-home demo.
#
# Sentinel IDs (checked in server.py before falling back to this table):
#   99999  -> always "not found"
#   4xxxx  -> always "permission denied"
#   50000  -> always simulates session expiry mid-flow
#   anything else not in this table -> synthesized deterministically


@dataclass
class Member:
    id: str
    name: str
    savings_balance: float


MEMBERS = {
    "12345": Member(id="12345", name="Jordan Rivera", savings_balance=4213.55),
    "67890": Member(id="67890", name="Casey Nguyen", savings_balance=812.0),
}


def find_or_synthesize_member(member_id: str) -> Member:
    known = MEMBERS.get(member_id)
    if known:
        return known
    digit_sum = sum(int(ch) for ch in member_id if ch.isdigit())
    return Member(id=member_id, name=f"Member {member_id}", savings_balance=round(digit_sum * 137.42, 2))
