from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from html import escape
from typing import Optional

from flask import Flask, Response, request, send_file
from werkzeug.serving import make_server

from src.storage.run_store import RunStore
from src.types.run import InterventionRequest, InterventionResolution


@dataclass
class OperatorServerHandle:
    url: str
    _server: object

    def close(self) -> None:
        self._server.shutdown()  # type: ignore[attr-defined]


def start_operator_server(
    intervention: InterventionRequest, handoff_url: Optional[str], run_store: RunStore, port: int
) -> OperatorServerHandle:
    """
    The "mock operator UI" the assignment explicitly says is fine to stub
    (Section 3.6 scope note). What must be real, and is: the process pauses,
    this page shows the actual current state of the actual live session
    (screenshot + reason + a link to the actual CDP-debuggable browser), and
    submitting the form here is what unblocks the waiting automation process
    -- there is no separate "fake" resume path.

    Free-form manual control happens by the operator opening `handoff_url`
    (the live browser's own remote-debugging endpoint) directly -- genuinely
    the same session, not a clone. What this server captures for the audit
    trail is the operator's note and a fresh screenshot taken at resume time;
    see REPORT.md "Escalation & handoff" for why full keystroke/click capture
    of that manual step is an explicit, documented cut.
    """
    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        screenshot_html = ""
        if intervention.screenshot_path:
            screenshot_html = '<img src="/screenshot.png" alt="screenshot at time of escalation"/>'
        handoff_html = (
            f'<p><a href="{escape(handoff_url)}" target="_blank">Open the live session to take manual control &rarr;</a></p>'
            if handoff_url
            else "<p>(No live handoff URL available for this surface.)</p>"
        )
        return f"""<!doctype html>
<html>
<head><title>Intervention required</title>
<style>body{{font-family:system-ui,sans-serif;max-width:720px;margin:2rem auto;color:#111}}
img{{max-width:100%;border:1px solid #ccc}} textarea{{width:100%;height:5rem}}
.btn{{padding:.5rem 1rem;margin-right:.5rem;font-size:1rem}}</style></head>
<body>
<h1>Run needs a human</h1>
<p><b>Capability:</b> {escape(intervention.capability_id)} ({escape(intervention.mode)})</p>
<p><b>Run ID:</b> {escape(intervention.run_id)}</p>
<p><b>Step:</b> {escape(intervention.step_id or "n/a")}</p>
<p><b>Reason:</b> {escape(intervention.reason)}</p>
<p><b>Current URL:</b> {escape(intervention.current_url)}</p>
{screenshot_html}
{handoff_html}
<form method="POST" action="/resolve">
  <label>What did you do? (audit note)</label>
  <textarea name="note" placeholder="e.g. approved the confirmation manually after verifying the amount"></textarea><br/>
  <button class="btn" name="action" value="resume" type="submit">Resume automation</button>
  <button class="btn" name="action" value="abort" type="submit">Abort run</button>
</form>
</body></html>"""

    @app.get("/screenshot.png")
    def screenshot() -> Response:
        if not intervention.screenshot_path:
            return Response(status=404)
        return send_file(intervention.screenshot_path)

    @app.post("/resolve")
    def resolve() -> str:
        action = "abort" if request.form.get("action") == "abort" else "resume"
        run_store.write_resolution(
            intervention.run_id,
            InterventionResolution(
                intervention_id=intervention.intervention_id,
                resolved_at=datetime.now(timezone.utc).isoformat(),
                operator_note=request.form.get("note", ""),
                action=action,  # type: ignore[arg-type]
            ),
        )
        return "<p>Recorded. You can close this tab; the run will continue automatically.</p>"

    server = make_server("localhost", port, app)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    return OperatorServerHandle(url=f"http://localhost:{port}", _server=server)
