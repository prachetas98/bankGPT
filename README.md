# BankGPT — Computer-Use Automation System

An LLM drives a real (mock) bank back-office UI once to accomplish a goal ("discovery"), the
successful run is distilled into a typed, reviewable **capability artifact**, and that artifact
replays afterward **without the LLM** — deterministically, with typed inputs/outputs, explicit
error handling, and a real human-escalation seam. See [REPORT.md](./REPORT.md) for the design
write-up (architecture, schema, error taxonomy, escalation model, safety, and cuts).

Implemented in Python (Playwright's synchronous API, Pydantic for the schemas, Flask for both the
target app and the mock operator page). Discovery is powered by a local LLM — Microsoft's
Phi-3.5-mini-instruct (3.8B, MIT-licensed) via Hugging Face `transformers` — so there's no paid
API key, account, or per-call cost anywhere in the system.

## What's in this repo

```
target_app/         A small, deliberately "legacy" back-office web app (Flask, server-rendered
                     HTML tables, no test ids) — the proxy target this system automates.
src/types/           The Capability Artifact + Run Result schemas (Pydantic). Start here.
src/agent/           The discovery loop (LLM observe->decide->act) and its LLM/tool plumbing.
src/recorder/        Turns a discovery trace into a saved artifact.
src/replay/           Deterministic replay engine + the business/recoverable/escalate taxonomy.
src/escalation/       Human-in-the-loop handoff: intervention requests + a mock operator page.
src/policy/            Allowlist, risk classification, redaction.
src/evidence/, src/storage/   Flat-file logging/evidence and artifact/run persistence.
src/cli/               `discover`, `replay`, `resume`, `list-artifacts` commands.
artifacts/              Saved capability artifacts (one is hand-authored — see below).
evidence/               Per-run logs, screenshots, transcripts (git-ignored except curated samples).
```

## Setup

Requires Python 3.10+.

**Windows (PowerShell):**

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium
copy .env.example .env
```

**Linux/macOS (bash):**

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install --with-deps chromium   # --with-deps pulls the system libraries
                                           # Chromium needs on a bare Linux container
cp .env.example .env
```

**Lightning.ai specifically:** skip the `venv` step above — a Studio provides exactly one
built-in conda environment and does not allow creating a second one (`pip install` straight into
it is fine and gives the same isolation a venv would, just managed by the platform instead of you):

```bash
pip install -r requirements.txt
playwright install --with-deps chromium
cp .env.example .env
```

Strongly prefer a **GPU** Studio over CPU-only in practice: this was tested on both, and a CPU-only
instance took on the order of several minutes *per step* for this 3.8B model (a full discovery run
is ~7 steps) — technically correct, but slow enough to make troubleshooting painful. A GPU Studio
brought each step down to single-digit seconds.

**On a headless machine with no display** (any cloud container, including Lightning.ai),
set `HEADED=0` in `.env` before running anything — Playwright can't open a visible browser
window where there's no display to open it on.

No HuggingFace account or login is required for the default model
(`microsoft/Phi-3.5-mini-instruct` — MIT-licensed, publicly accessible, not gated). `HF_TOKEN` in
`.env` is only needed if you point `LOCAL_MODEL` at a gated checkpoint instead. The first
`discover` run downloads the model's weights (~7–8GB, cached under `~/.cache/huggingface/` for
every run after). **On CPU** (no GPU detected), weights load in float32, which needs roughly
**~15GB of free RAM** for this 3.8B-parameter model — check your Lightning.ai instance has that
much before running `discover`, or switch to a GPU instance, which loads in bfloat16 instead
(~8GB VRAM) and is faster besides.

All commands below are run **from the repo root** using `python -m ...` so the `src`/`target_app`
packages resolve without installing the project.

## Running without live services

Every part of this system — `discover`, `replay`, the target app, and the operator/escalation
server — runs entirely locally with no paid API key or account. `discover` needs internet access
the first time only, to download the model's weights; every run after that, including every `replay`,
needs no network access at all. `discover` is also slower the first time for this reason, plus
CPU inference per step.

## Demo path

**1. Start the target application** (leave running in its own terminal):

```powershell
python -m target_app.server
# BankGPT target app listening on http://localhost:4000
```

**2. Run a discovery goal against it** (first run downloads the model's weights — see above):

```powershell
python -m src.cli discover --goal "Look up member 12345 and read their current savings balance" --capability member-savings-lookup
```

This launches a real (headed, by default) Chromium window, lets the local model drive it step by
step, logs everything to `evidence/<runId>/`, and — on success — saves
`artifacts/member-savings-lookup.json`.

**3. Replay the saved capability**, with no LLM involved, for a different member:

```powershell
python -m src.cli replay --capability member-savings-lookup --params "{\"member_id\":\"67890\"}"
```

**4. See the error/business-outcome handling** by replaying with a sentinel id (see
`target_app/data/members.py` for the full list):

```powershell
# "not found" — a business outcome, not a crash
python -m src.cli replay --capability member-savings-lookup --params "{\"member_id\":\"99999\"}"

# permission denied — a different business outcome
python -m src.cli replay --capability member-savings-lookup --params "{\"member_id\":\"40000\"}"

# session expiry mid-flow — this one ESCALATES to a human (see step 5)
python -m src.cli replay --capability member-savings-lookup --params "{\"member_id\":\"50000\"}"
```

**5. Resolve an escalation.** When a run escalates, the process prints an operator URL and blocks:

```
[ESCALATION] Human input needed.
  Operator page: http://localhost:4100
  Live session:  http://localhost:9223
```

Open the operator page in a browser to see the reason, a screenshot, and a link to the *live*
session (open a second Chromium tab pointed at the printed CDP URL to drive it directly). Submit
the form to resume or abort. Or, from another terminal, resolve it without a browser:

```powershell
python -m src.cli resume --run <the-runId-printed-above> --action resume --note "Verified manually, continuing."
```

**6. Try the second, hand-authored capability**, which demonstrates the irreversible-action /
approval-gated replay path end-to-end (see REPORT.md, "Safety" and "Escalation & handoff"):

```powershell
python -m src.cli replay --capability open-sub-account --params "{\"operator_username\":\"demo\",\"operator_password\":\"demo\",\"member_id\":\"12345\",\"account_type\":\"savings\",\"initial_deposit\":100}"
```

Replay will run every step up to (but not including) the final "Confirm" click, then escalate for
approval — resolve it as in step 5. If you approve without actually clicking Confirm in the live
session yourself, the run will correctly report a hard failure (final checkpoint not met), since
resuming never auto-executes an irreversible step on your behalf — see REPORT.md for why.

## Other commands

```powershell
python -m src.cli list-artifacts     # see what's saved in artifacts/
pytest                                # unit tests (pure logic: policy, error taxonomy, recorder) — no browser needed
```

## Config

All in `.env` (see `.env.example`): `LOCAL_MODEL`, `TARGET_BASE_URL`, `OPERATOR_SERVER_PORT`,
`HEADED` (0 for headless discovery/replay — set this on a machine with no display, e.g. a cloud
GPU/CPU box), `DISCOVERY_TIMEOUT_S` (raise this if a discovery run escalates purely from slow
CPU inference rather than an actual problem). The action/domain/route allowlist and risky-action
name patterns are in `src/config/allowlist.json`.

## A note on `/evidence`

This repository ships the **code**, not a pre-baked run. The assignment is explicit that the
discovery run has to be real, so `/evidence` is populated by actually running step 2 above (no
API key needed — just the local model download described in Setup), not by anything checked in
ahead of time. Run the demo path once end to end (including one error/escalation case) before
treating the submission as complete — see `evidence/README.md`.
