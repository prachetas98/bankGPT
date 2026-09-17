# REPORT

## 1. Architecture

The system has two separate modes that share three components: a **Surface Adapter**
(`src/surface/`), a **Policy Engine** (`src/policy/`), and an **Evidence Store**
(`src/evidence/`). Nothing above the adapter knows it's talking to Playwright/Chromium. Nothing
touches the live UI without going through the policy engine first.

<img width="1400" height="900" alt="bankgpt_architecture" src="https://github.com/user-attachments/assets/15f84d4d-b9bb-43cb-afad-7d43e6e15f6f" />


Both modes use the same `SurfaceAdapter`, the same `PolicyEngine`, and the same `EvidenceStore`.
The only difference between them is who picks the next action: the LLM (once, during discovery)
or the saved artifact (every time after, during replay).

- **Discovery** (`src/agent/discovery_agent.py`): an LLM-driven loop that observes, decides, and
  acts. Each turn, the adapter reads the page — a short list of interactive and read-only
  elements with roles and names, not raw HTML (see §4). The model picks one tool call from a fixed
  action list (`src/agent/tools.py`). That action goes through the policy engine, then the adapter
  runs it. When the run succeeds, a **Recorder** (`src/recorder/recorder.py`) turns the trace into
  a **Capability Artifact**.
- **Replay** (`src/replay/replay_executor.py`): walks an artifact's steps using the same adapter
  and policy engine. After each action it checks a checkpoint, and if that checkpoint fails, it
  classifies the failure using an **error taxonomy** (`src/replay/error_taxonomy.py`) instead of
  guessing. No LLM call happens anywhere in this path.
- **Escalation** (`src/escalation/`) can pause either loop and hand the same live browser session
  to a human — through a small operator page plus the browser's own CDP debugging endpoint — then
  resume once a person resolves it.

**Key decisions, and why:**

- **Python + Pydantic for the schema.** The artifact is a contract other code depends on, so I
  wanted one source of truth with runtime validation (`src/types/artifact.py`) instead of a
  hand-maintained JSON Schema that could drift out of sync. Pydantic v2's discriminated unions do
  the same job Zod's `discriminatedUnion` does in TypeScript.
- **A local, free model — Microsoft Phi-3.5-mini-instruct (3.8B, MIT-licensed) — instead of a
  hosted API.** I picked it because it needs no HuggingFace license approval. Two Gemma checkpoints
  I looked at first both required accepting a license and logging in, which defeats the goal of a
  setup with zero accounts. `src/agent/llm_client.py` is the only file in the codebase that knows
  which model is running — `DiscoveryAgent` only depends on the `LlmDecision` shape that `decide()`
  returns, never on how that shape was produced. (None of the tests touch an LLM at all — see
  `test_error_taxonomy.py` and the rest of the suite.)

  The real cost of this choice: local models have no built-in tool-calling API, so
  `llm_client.py` writes its own. The prompt spells out the tool menu and the exact JSON shape the
  model must return (`_tool_menu_text`), and the response is parsed and checked by hand instead of
  arriving pre-validated. A small instruct model is also just less reliable at multi-step
  structured output than a frontier model built around tool use. So a discovery run is more likely
  to need a retry, or to hit `give_up`/escalation, than it would with a stronger hosted model. I
  accepted that cost on purpose, to keep the one required real run free of any API key, account, or
  per-call cost — it runs end to end on a plain CPU box with nothing but the model weights. (In
  practice a GPU is worth having: the same model took minutes per step on CPU and single-digit
  seconds per step on GPU.)

  That reliability cost wasn't hypothetical. Real discovery runs during this project hit three
  specific, fixable bugs, each one traced from an actual transcript, not guessed at:
  1. The model would sometimes reason correctly in `rationale` ("this field already has a value,
     don't type again") but then still pick the *wrong* tool call anyway. Cause: the JSON envelope
     originally asked for `tool_name` before `rationale`. Since generation runs left to right, the
     model committed to the action before it had generated the reasoning that should have driven
     it. Fix: reorder the envelope to `{rationale, tool_name, arguments}`.
  2. Greedy decoding (`do_sample=False`) is guaranteed to repeat the same action forever once the
     screen stops changing between turns — for example, after a redundant retype. Fix: switch to
     light sampling (`temperature=0.4`), which gives the model an actual chance to pick something
     different once a correction is needed.
  3. Malformed tool arguments — a `parameter_hint` sent as a bare string instead of an object, a
     `type` call missing `value` — were crashing with raw Python exceptions (`"Error: 'value'"`)
     that got fed straight back to the model. That's useless as a self-correction signal. Fix:
     `_require()` in `discovery_agent.py` replaces the raw exception with a clear, actionable
     message.
- **Playwright's synchronous API, and DOM-first perception** (not the accessibility-tree API, not
  screenshot+coordinates). I pull role, accessible name, and attributes directly via
  `page.evaluate` (`src/surface/playwright_adapter.py`), so the same code that shows the LLM the
  page also produces the ranked locator candidates the Recorder needs — one code path instead of
  two that could drift apart. Screenshot+coordinates is the documented fallback for a future
  surface with no DOM at all (§4).

  **A real gap this surfaced:** perception originally only matched elements you can click or type
  into — links, buttons, form fields. Read-only display data, like a balance sitting in a plain
  `<td>`, was invisible to the model. A goal that's purely "read this value" had nothing to look
  at, so the model wandered into an unrelated form instead. Fix: also match any element with an
  `id` attribute (a legacy app's closest thing to a test hook), and give those a synthetic
  `"text"` role whose value is its text content. Read-only data is now visible to the model, not
  just clickable controls.

  I used Playwright's **sync** API over the async one on purpose: this is a single-purpose CLI,
  not a concurrent server, so plain blocking calls keep the control flow — especially the
  escalation pause/resume — easy to follow. The one place that actually needs concurrency, the
  operator HTTP page staying reachable while the main flow is blocked waiting on a human, runs in
  a background thread (`src/escalation/operator_server.py`). That's the standard way to run a
  small server alongside an otherwise synchronous script.
- **Flat-file storage, single process, synchronous CLI.** Hundreds of tenants is a real constraint
  for the design (§4), but building queues and services for a one-tenant demo would be exactly the
  kind of unnecessary infrastructure the assignment says not to reward. Storage is just
  `artifacts/*.json`, `evidence/<runId>/`, `runs/<runId>.*.json`.
- **One target app, built by me, deliberately old-fashioned:** server-rendered tables, no test
  ids, no ARIA (`target_app/`, a small Flask app). This was the only way to reliably produce the
  exact runtime states the rubric asks for — not-found, permission-denied, validation error,
  session timeout — on demand. A public demo site wouldn't let me control that.

**A trade-off I made on purpose:** the discovery loop and the replay engine are separate classes,
not one "executor with an optional LLM." I considered merging them, but once an LLM is in the
loop, each step is "decide, then act, then see what happened." Replay's job is different: "assert,
don't decide." Forcing one abstraction over both would have hidden that difference instead of
making it clear.

## 2. Artifact schema

`src/types/artifact.py` is the most important file in the project. Design goals, in order: (1) a
human reviewer can understand the capability without replaying it, (2) a calling agent can
validate inputs/outputs automatically, (3) replay can be fully deterministic, (4) it travels
across tenants with minimal change (§4).

Shape, briefly:

- **`inputs` / `outputs`** — typed, named, described. Inputs carry a `sensitive` flag that drives
  redaction everywhere downstream. These come straight from the discovery run's own tool calls:
  when the model types a value it recognizes as coming from the goal (not a fixed constant), it
  sets a `parameter_hint` (`src/agent/tools.py`). The Recorder trusts that hint instead of guessing
  afterward which literals "look like" variables.
- **`steps`** — each one is `{action, locator, value, checkpoint, risk, requires_approval}`.
  `locator` is a **ranked bundle**, not one selector: a primary plus ordered fallbacks, each with a
  `robustness_note` explaining why it's ranked where it is (see `src/surface/locator_strategy.py`
  — role+accessible-name beats id beats name-attribute CSS beats text beats structural CSS beats
  XPath). Every step checks a `checkpoint` — did we reach the state we expected, not just "did the
  click not throw an error."
- **`recognized_outcomes`** — this is the schema's answer to "business outcome vs. recoverable vs.
  hard failure." Each one names a step to check after, a locator that signals it, a `kind`
  (`business` / `recoverable` / `escalate`), and what to do about it. This is knowledge someone
  writes down at record time, not something automatically learned from one successful run — a
  single discovery run, by definition, never sees its own failure states. `target_app/known_outcomes.py`
  is the seed data for this demo; in production this would be a registry per `app_id` that a
  reviewer maintains, not something written per capability.
- **`target.base_url`** is the one field a tenant override is meant to replace (§4); everything
  else stays the same across tenants by design.

## 3. Determinism & error handling

Every locator resolves by trying the primary strategy, then falling through the ranked fallbacks,
before failing (`PlaywrightAdapter._resolve_to_playwright`). Every step's `checkpoint` is a
declared wait condition (`element_visible` / `url_matches` / `network_idle` / `element_hidden`) —
never an assumption that the previous action just worked.

One correctness bug I caught and fixed while building this: a checkpoint built naively right after
"type member_id, then click Search" would hardcode the *literal* path seen during discovery (e.g.
`/member/12345`). That would only ever match a replay called with that exact member id, defeating
the whole point of parameterizing it. `_templatized_path_pattern` (used by `Recorder`) replaces
any occurrence of a known parameter's discovery-time raw value with a wildcard before saving the
pattern, so `/member/12345` becomes `/member/[^/]+` in the artifact. (Covered by
`src/recorder/test_recorder.py`.) This is the same idea as the canonicalization stretch goal in the
assignment, applied somewhere it actually matters rather than as an optional extra.

When a checkpoint fails, `error_taxonomy.classify_outcome` checks the live page against the
artifact's declared `recognized_outcomes` for that step — and only those, matched by step id, not
a blind scan of the whole page:

- **`business`** → returned to the caller as `status: "business_outcome"` with a named code
  (`MEMBER_NOT_FOUND`, `PERMISSION_DENIED`, `INVALID_INITIAL_DEPOSIT`). Not treated as an error.
- **`recoverable`** → the declared `recovery_action` runs once, the original checkpoint is checked
  again, and only then does the run continue or fall through to a hard failure.
- **`escalate`** → control passes to a human (§5).
- **no match** → a genuine hard failure: `{step_id, expected, observed, message}`, plus a
  screenshot and a fresh DOM snapshot, so it's debuggable without re-running anything.

If the UI drifts in some other way — a selector quietly breaking — it surfaces the same way as any
other state the system doesn't recognize: a hard failure naming the step and what was expected. It
never gets silently retried into a false success.

## 4. Heterogeneity & multi-tenant

**Surface abstraction.** `SurfaceAdapter` (`src/surface/surface_adapter.py`, an `abc.ABC`) is the
boundary: perceive, navigate, click, type_text, select_option, extract_text, wait_for, screenshot,
get_handoff_url. Everything above it — agent, recorder, replay, policy — is written against this
interface only. A legacy frameset app doesn't need a new abstraction, just an adapter that
flattens frames into one snapshot (same interface, messier `css_path`/`xpath` fallbacks
internally). A desktop app would need an adapter backed by an OS accessibility API (UIA/AT-SPI)
instead of a DOM — `LocatorCandidate` already has room for that (a `role`+`role_name` strategy also
makes sense on desktop accessibility trees). None of that would touch `ArtifactStep`,
`RecognizedOutcome`, the Recorder, or the Replay Executor.

**Multi-tenant reuse.** An artifact is written to not depend on a specific base URL:
`target.base_url` is a single field, and `ReplayExecutor.run` already accepts a
`base_url_override` per call — that's the mechanism for "one recording, many tenants." What
doesn't transfer automatically is markup drift between tenants running a differently-skinned
version of the same vendor product. The locator fallback chain absorbs small drift for free —
role+name survives re-theming; only structural CSS/XPath breaks. For bigger drift, the design
answer (not built here) is a **tenant override layer**: a small patch keyed by `(app_id,
tenant_id)` that can swap one step's locator bundle or one recognized_outcome's matched text
without touching the base artifact — plus a confidence/flakiness signal (stretch goal §8 in the
assignment) that flags when an artifact's success rate drops for a specific tenant, so it gets
re-reviewed instead of silently failing. I didn't build the override store or a drift detector. I
kept the places they'd plug in (`base_url_override`, and locators stored as data rather than code)
so adding them later is additive, not a rewrite.

## 5. Escalation & handoff

Control sits in exactly one place at a time — the automation, or a human — tracked by whichever
one is currently driving the adapter. `EscalationManager.raise_intervention` (`src/escalation/`)
is the only way to hand control from the first to the second. It takes a screenshot, writes an
`InterventionRequest` (`runs/<runId>.intervention.json`), and starts a small Flask operator page
(in a background thread, via `werkzeug.serving.make_server`, so it can be shut down cleanly) that
shows the reason, the screenshot, and a link to the browser's own CDP endpoint
(`PlaywrightAdapter.get_handoff_url`). That's genuinely the same live session — not a clone of it,
not a replay. The calling loop then blocks in `wait_for_resolution`, polling for
`runs/<runId>.resolution.json`.

Four triggers, all exercised by this system:
1. An irreversible step (`requires_approval: true`, e.g. the final "Confirm" click in
   `open-sub-account`). Replay and discovery never auto-execute it.
2. A `recognized_outcome` explicitly marked `kind: "escalate"` (session expiry — automatic
   re-auth isn't wired to a credential store, so a human decides instead of a silent retry).
3. Repeated action *failures*, or hitting a max-step/timeout limit, during discovery.
4. The model repeating the identical *successful* action several turns in a row without making
   progress — for example, re-typing a field that's already filled. This is a real failure mode I
   saw in actual discovery runs, and it's different from (3): nothing throws an exception, each
   action genuinely succeeds, it just never moves the task forward. Detected by comparing a
   signature of consecutive decisions (tool name + target + value), not by watching for errors.

**On resume, the code never re-runs the step that triggered escalation.** For an irreversible
action, re-running it could double-submit something like a fund transfer if the human already did
it live. Instead, resume just reads the current state and moves on. If the human declined, or
never actually clicked through, the next checkpoint (or the artifact's `final_checkpoint`)
correctly reports a hard failure instead of a false success. What gets logged for the audit trail:
the operator's free-text note, a resolution timestamp, and a fresh screenshot at resume time — all
written to `evidence/<runId>/log.jsonl` alongside everything else.

**What's intentionally mocked, per the assignment's own scope note:** the operator UI is a bare
HTML form, not a full co-browsing tool, and the human's individual keystrokes/clicks aren't
captured step by step — a real deployment would want the operator acting *through* the same
Playwright `page` object, so every action is still recorded, which I noted but didn't build. What
is real: the pause, the live session handoff, the resume signal, and the fact that a blocked
process genuinely continues afterward. There's no separate "fake" resume path — `python -m src.cli
resume` writes the exact same resolution file the operator page's form does.

## 6. Safety

`PolicyEngine` (`src/policy/policy_engine.py`) checks every action, in both loops, against
`src/config/allowlist.json`: allowed domains, allowed route patterns, allowed action types.
Anything outside that list raises `PolicyViolationError` before it ever reaches the adapter.
During discovery this goes straight to escalation instead of silently failing or retrying.

Risk classification is name-pattern-based (`confirm`, `delete`, `close account`, ...) plus an
explicit hint from the model (`irreversible: true` on a `click` tool call). Either signal marks a
step `irreversible`, and irreversible steps require human approval unconditionally, in both
discovery and replay. This is deliberately conservative: a false positive costs one unnecessary
escalation; a false negative risks an unauthorized money-moving action.

Redaction happens at two points, not one. First, live: `DiscoveryAgent` checks every typed field's
locator against sensitive-name patterns (password, SSN, account number, ...) and forces it into a
parameter if it matches, **regardless of whether the model flagged it**. This closes a real gap I
found while building this: an unflagged login password would otherwise get baked into the artifact
as a literal string. Making sensitivity detection independent of the model's own cooperation fixed
that. Second, at every write boundary: `EvidenceStore`/`redaction.py` scrub named-sensitive fields
and value-shaped secrets (SSN/card-number/API-key patterns) before anything touches `log.jsonl` or
`transcript.json`. **Limits:** business data that isn't credential-shaped — an extracted balance,
say — is not redacted from evidence. It's the capability's actual output, and blanket-redacting it
would make the evidence useless for debugging. That's a deliberate scope line, written down here
instead of left implicit.

## 7. Cuts

- **Legacy-frameset and desktop adapters** — designed for (§4), not built. Only one
  `SurfaceAdapter` (Playwright) exists.
- **Tenant override store and artifact confidence/flakiness scoring** — the places they'd plug in
  exist (`base_url_override`, `approval_state: draft/approved`, the `require_approved` gate in
  `ReplayExecutor`), but the store and the scorer don't.
- **Recoverable-outcome demo in the live app** — `attempt_recovery`'s `click` and `wait_and_retry`
  paths are unit-tested (`src/replay/test_error_taxonomy.py`) but not exercised end-to-end against
  the target app, since I didn't want to fake an interstitial just to hit the code path.
  `reauthenticate` is an explicit no-op stub pending a real credential store; the target app
  doesn't currently have a real recoverable UI state.
- **Operator action capture during manual control** — the human's live actions aren't captured
  step by step (§5); only a note and before/after evidence are.
- **Per-step semantic checkpoints beyond URL/visibility** (e.g. "the balance cell contains a
  positive number") — checkpoints confirm *reaching* a state, not deep-validating its content
  beyond what extraction/transform already type-checks.
- **No dispatcher in front of `discover`/`replay`.** Right now a human decides which command to
  run; nothing in the system answers "does a capability for this request already exist" on its
  own. I deliberately didn't fold that check into `ReplayExecutor` — its entire value is a narrow,
  fast, fully predictable contract: same latency, same cost, same behavior every time. A silent
  fallback into Discovery on a miss would break that guarantee for every caller, not just the miss
  case. It would also risk quietly bypassing the `draft`/`approved` gate: "no artifact found →
  auto-run Discovery → immediately replay the fresh result" would let an unreviewed capability act
  on a real request with nobody having looked at it. The right shape is a separate, deliberately
  simple coordinator in front of both: resolve by exact capability id first (the realistic case —
  a calling agent already knows the name it wants, like a function name, not a vague sentence to
  be matched), and if nothing matches, say so and stop rather than auto-triggering Discovery. A
  human decides whether a new capability is worth teaching at all, and only then does Discovery
  run.


