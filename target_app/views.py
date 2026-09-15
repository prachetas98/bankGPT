from __future__ import annotations

from html import escape

from target_app.data.members import Member

# Deliberately legacy markup: nested tables for layout, no test ids, no
# ARIA attributes, inline styles, generic class names only where needed to
# signal a banner (.error / .success / .notice) -- the minimum a real
# legacy enterprise screen would give an automated agent to work with.


def _layout(title: str, body: str) -> str:
    return f"""<!doctype html>
<html>
<head><title>{title} - BankGPT Member Services</title>
<style>body{{font-family:Tahoma,Arial,sans-serif;font-size:13px;background:#eef1f5}}
table{{border-collapse:collapse}} td{{padding:4px 8px}}
.shell{{background:#fff;border:1px solid #99a;width:640px;margin:24px auto;padding:16px}}
.hdr{{background:#003366;color:#fff;padding:8px 16px;font-weight:bold;font-size:16px}}
.error{{background:#ffe0e0;border:1px solid #c00;color:#900;padding:8px;margin-bottom:10px}}
.success{{background:#e0ffe0;border:1px solid #0a0;color:#060;padding:8px;margin-bottom:10px}}
.notice{{background:#fff8dc;border:1px solid #cc0;color:#660;padding:8px;margin-bottom:10px}}
input[type=text],input[type=password],input[type=number],select{{padding:3px;font-size:13px}}
input[type=submit],button{{padding:4px 12px}}
</style></head>
<body>
<div class="hdr">BankGPT &mdash; Member Services (internal)</div>
<div class="shell">{body}</div>
</body></html>"""


def login_page(expired: bool = False) -> str:
    notice = '<div class="notice">Your session has expired. Please sign in again.</div>' if expired else ""
    return _layout(
        "Sign In",
        f"""{notice}
<h2>Operator Sign In</h2>
<form method="POST" action="/login">
<table>
<tr><td>Username</td><td><input type="text" name="username" /></td></tr>
<tr><td>Password</td><td><input type="password" name="password" /></td></tr>
<tr><td></td><td><input type="submit" value="Sign In" /></td></tr>
</table>
</form>""",
    )


def search_page(validation_error: str = "") -> str:
    banner = f'<div class="error">{escape(validation_error)}</div>' if validation_error else ""
    return _layout(
        "Member Search",
        f"""{banner}
<h2>Member Search</h2>
<form method="POST" action="/search">
<table>
<tr><td>Member ID</td><td><input type="text" name="memberId" /></td></tr>
<tr><td></td><td><input type="submit" value="Search" /></td></tr>
</table>
</form>""",
    )


def member_not_found_page(member_id: str) -> str:
    return _layout(
        "Member Search",
        f"""<div class="error">No member found matching ID {escape(member_id)}.</div>
<p><a href="/search">Back to search</a></p>""",
    )


def permission_denied_page(member_id: str) -> str:
    return _layout(
        "Access Denied",
        f"""<div class="error">Access Denied: you do not have permission to view member {escape(member_id)}.</div>
<p><a href="/search">Back to search</a></p>""",
    )


def member_detail_page(member: Member) -> str:
    return _layout(
        "Member Detail",
        f"""<h2>Member Detail</h2>
<table>
<tr><td>Member ID</td><td>{escape(member.id)}</td></tr>
<tr><td>Name</td><td>{escape(member.name)}</td></tr>
<tr><td>Savings Balance</td><td id="balance-cell">{_currency(member.savings_balance)}</td></tr>
</table>
<p><a href="/member/{member.id}/new-subaccount">Open New Sub-Account</a></p>
<p><a href="/search">Back to search</a></p>""",
    )


def new_sub_account_page(member: Member, validation_error: str = "") -> str:
    banner = f'<div class="error">{escape(validation_error)}</div>' if validation_error else ""
    return _layout(
        "New Sub-Account",
        f"""{banner}
<h2>Open New Sub-Account for {escape(member.name)} ({escape(member.id)})</h2>
<form method="POST" action="/member/{member.id}/new-subaccount">
<table>
<tr><td>Account Type</td><td>
<select name="accountType">
<option value="savings">Savings</option>
<option value="checking">Checking</option>
<option value="cd">Certificate of Deposit</option>
</select>
</td></tr>
<tr><td>Initial Deposit</td><td><input type="text" name="initialDeposit" /></td></tr>
<tr><td></td><td><input type="submit" value="Continue" /></td></tr>
</table>
</form>""",
    )


def confirm_sub_account_page(member: Member, account_type: str, initial_deposit: float) -> str:
    return _layout(
        "Confirm New Sub-Account",
        f"""<h2>Confirm New Sub-Account</h2>
<table>
<tr><td>Member</td><td>{escape(member.name)} ({escape(member.id)})</td></tr>
<tr><td>Account Type</td><td>{escape(account_type)}</td></tr>
<tr><td>Initial Deposit</td><td>{_currency(initial_deposit)}</td></tr>
</table>
<form method="POST" action="/member/{member.id}/new-subaccount/confirm">
<input type="hidden" name="accountType" value="{escape(account_type)}" />
<input type="hidden" name="initialDeposit" value="{initial_deposit}" />
<button type="submit">Confirm</button>
</form>
<p><a href="/member/{member.id}">Cancel</a></p>""",
    )


def sub_account_success_page(member: Member, account_number: str) -> str:
    return _layout(
        "Sub-Account Opened",
        f"""<div class="success">New sub-account opened.</div>
<table>
<tr><td>Account Number</td><td>{escape(account_number)}</td></tr>
<tr><td>Member</td><td>{escape(member.name)} ({escape(member.id)})</td></tr>
</table>
<p><a href="/member/{member.id}">Back to member</a></p>""",
    )


def _currency(n: float) -> str:
    return f"${n:,.2f}"
