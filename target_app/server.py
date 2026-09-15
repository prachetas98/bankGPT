from __future__ import annotations

import os
import random
import time
import uuid
from typing import Dict

from flask import Flask, redirect, request

from target_app import views
from target_app.data.members import find_or_synthesize_member

PORT = int(os.environ.get("TARGET_APP_PORT", "4000"))
MIN_INITIAL_DEPOSIT = 25.0

app = Flask(__name__)
_sessions: Dict[str, float] = {}  # sid -> logged_in_at
_sub_accounts: Dict[str, dict] = {}


def _require_session() -> bool:
    sid = request.cookies.get("sid")
    return bool(sid and sid in _sessions)


@app.get("/login")
def login_page():
    return views.login_page(expired=request.args.get("expired") == "1")


@app.post("/login")
def login_submit():
    sid = str(uuid.uuid4())
    _sessions[sid] = time.time()
    resp = redirect("/search")
    resp.set_cookie("sid", sid, httponly=True)
    return resp


@app.get("/logout")
def logout():
    sid = request.cookies.get("sid")
    if sid:
        _sessions.pop(sid, None)
    resp = redirect("/login")
    resp.delete_cookie("sid")
    return resp


@app.get("/search")
def search_page():
    if not _require_session():
        return redirect("/login")
    return views.search_page()


@app.post("/search")
def search_submit():
    """Validation error scenario: empty member id."""
    if not _require_session():
        return redirect("/login")
    member_id = request.form.get("memberId", "").strip()
    if not member_id:
        return views.search_page(validation_error="Member ID is required.")
    return redirect(f"/member/{member_id}", code=303)


@app.get("/member/<member_id>")
def member_detail(member_id: str):
    if not _require_session():
        return redirect("/login")

    # Session-timeout scenario: a dedicated sentinel id, so the demo is reproducible
    # regardless of how much real wall-clock time has elapsed.
    if member_id == "50000":
        sid = request.cookies.get("sid")
        if sid:
            _sessions.pop(sid, None)
        resp = redirect("/login?expired=1")
        resp.delete_cookie("sid")
        return resp
    # Not-found scenario.
    if member_id == "99999":
        return views.member_not_found_page(member_id)
    # Permission-denied scenario.
    if member_id.startswith("4"):
        return views.permission_denied_page(member_id)

    member = find_or_synthesize_member(member_id)
    return views.member_detail_page(member)


@app.get("/member/<member_id>/new-subaccount")
def new_sub_account_page(member_id: str):
    if not _require_session():
        return redirect("/login")
    member = find_or_synthesize_member(member_id)
    return views.new_sub_account_page(member)


@app.post("/member/<member_id>/new-subaccount")
def new_sub_account_submit(member_id: str):
    if not _require_session():
        return redirect("/login")
    member = find_or_synthesize_member(member_id)
    account_type = request.form.get("accountType", "savings")
    try:
        initial_deposit = float(request.form.get("initialDeposit", ""))
    except ValueError:
        initial_deposit = float("nan")

    if not (initial_deposit == initial_deposit) or initial_deposit < MIN_INITIAL_DEPOSIT:  # NaN check + minimum
        return views.new_sub_account_page(
            member, validation_error=f"Initial deposit must be at least ${MIN_INITIAL_DEPOSIT:.2f}."
        )

    return views.confirm_sub_account_page(member, account_type, initial_deposit)


@app.post("/member/<member_id>/new-subaccount/confirm")
def new_sub_account_confirm(member_id: str):
    if not _require_session():
        return redirect("/login")
    member = find_or_synthesize_member(member_id)
    account_type = request.form.get("accountType", "savings")
    initial_deposit = float(request.form.get("initialDeposit", "0") or 0)

    account_number = f"SA-{random.randint(100000, 999999)}"
    _sub_accounts[account_number] = {"member_id": member.id, "account_type": account_type, "initial_deposit": initial_deposit}

    return views.sub_account_success_page(member, account_number)


if __name__ == "__main__":
    print(f"BankGPT target app listening on http://localhost:{PORT}")
    app.run(host="localhost", port=PORT)
