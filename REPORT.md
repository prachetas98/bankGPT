# REPORT

## 1. Architecture

The system has two independent modes that share three components: a **Surface Adapter**
(`src/surface/`), a **Policy Engine** (`src/policy/`), and an **Evidence Store**
(`src/evidence/`). Nothing above the adapter interface knows it's talking to Playwright/Chromium;
nothing bypasses the policy engine to touch the live UI directly.

- **Discovery** (`src/agent/discovery_agent.py`): an LLM-driven observe→decide→act loop. Each
  turn, the adapter perceives the page (a pruned list of interactive *and* read-only elements
  with roles/names, not raw HTML — see §4), the model picks exactly one tool call (the action
  vocabulary in
  `src/agent/tools.py`), the action runs through the policy engine, then the adapter executes it.
  A **Recorder** (`src/recorder/recorder.py`) turns the resulting trace into a **Capability
  Artifact** on success.
- **Replay** (`src/replay/replay_executor.py`): walks an artifact's steps with the same adapter
  and policy engine, asserting a checkpoint after each action and classifying any miss via an
  **error taxonomy** (`src/replay/error_taxonomy.py`) instead of guessing. No LLM call anywhere in
  this path.
- **Escalation** (`src/escalation/`) is a third consumer of the same adapter: it can pause either
  loop, expose the *same* live session to a human (a mock operator HTTP page plus the browser's
  own CDP debugging endpoint), and resume once a resolution is written.

**Key decisions:**

- **Python + Pydantic everywhere the schema matters.** The artifact is the contract a calling
  agent depends on; I wanted runtime-validated types from one source of truth
  (`src/types/artifact.py`), not a hand-maintained JSON Schema kept in sync by hand. Pydantic v2's
  discriminated unions express the same "one of several typed shapes" contracts Zod's
  `discriminatedUnion` would in a TypeScript version.
- **A local, key-free model (Microsoft Phi-3.5-mini-instruct, 3.8B, MIT-licensed, via Hugging
  Face `transformers`) instead of a hosted API.** Chosen specifically because it's publicly
  accessible with no HuggingFace license gate — two Gemma checkpoints considered first both
  turned out to require accepting a license and authenticating, which defeats the point of a
  zero-account setup. `src/agent/llm_client.py` is intentionally the only file that knows which
  model is in use — `DiscoveryAgent` depends solely on the `LlmDecision` shape `decide()` returns,
  never on how it was produced (see `test_error_taxonomy.py`/the rest of the test suite, none of
  which touch an LLM at all). The honest trade-off: local generation has no native tool-calling
  API, so `llm_client.py` builds its own — the tool menu and required JSON envelope are spelled
  out in the prompt (`_tool_menu_text`), and the response is parsed and validated by hand instead
  of arriving pre-validated. A small instruct model is also measurably less reliable at
  multi-step structured output than a frontier model built around tool use as a first-class
  capability, so a discovery run is more likely to need a retry or hit `give_up`/escalation than
  it would with a stronger hosted model. I accepted that reliability cost specifically to keep
  the one required real run free of any API key, account, or per-call cost — runnable end to end
  on a plain CPU box with nothing more than the model weights (though in practice a GPU is worth
  it: the same model took minutes per step on CPU versus single-digit seconds on GPU).

  That reliability trade-off was not hypothetical — the actual discovery runs produced during
  this project surfaced specific, fixable failure modes, each traced from a real transcript
  rather than guessed at: (1) the model would occasionally reason correctly in `rationale`
  ("this field already has a value, don't type again") but still emit the *contradicting* tool
  choice, because the JSON envelope originally asked for `tool_name` before `rationale` — since
  generation is left-to-right, the tool was committed before the reasoning that should have
  informed it existed; reordering the envelope to `{rationale, tool_name, arguments}` fixed this.
  (2) greedy decoding (`do_sample=False`) is mathematically guaranteed to repeat the identical
  action forever once the screen stops changing between turns (e.g. after a redundant retype) --
  switching to light sampling (`temperature=0.4`) gives the model an actual chance to pick
  something else once a correction is warranted. (3) malformed tool arguments (a `parameter_hint`
  sent as a bare string instead of the expected object; a `type` call missing `value`) were
  crashing with raw Python exceptions (`"Error: 'value'"`) fed straight back to the model as its
  only feedback -- useless as a self-correction signal. `_require()` in `discovery_agent.py`
  replaces that with an explicit, actionable message instead of a bare `KeyError`.
- **Playwright's synchronous API, DOM-first perception (not the accessibility-tree API, not
  screenshot+coordinates).** I extract role/accessible-name/attributes directly via
  `page.evaluate` (`src/surface/playwright_adapter.py`) so the same code that perceives for the
  LLM also produces the ranked locator candidates the Recorder needs — one code path, not two
  independently-maintained ones. Screenshot+coordinates is the documented fallback for a future
  surface with no DOM at all (see §4). **A real gap this surfaced:** perception originally only
  matched genuinely interactive elements (links, buttons, form controls) — which meant read-only
  *display* data (a balance sitting in a plain `<td>`) was invisible to the model entirely. A
  goal that's purely "read this value" had nothing to target, and the model, unable to see the
  actual answer, wandered into an unrelated form instead. Fixed by also matching elements with
  an `id` attribute (a legacy app's closest thing to a test hook) and giving them a synthetic
  `"text"` role whose perceived "value" is its text content — read-only display data is now
  first-class in perception, not just clickable/typeable controls. I chose Playwright's **sync** API over its async one
  deliberately: this system is a single-purpose CLI, not a concurrent server, so plain blocking
  calls keep the control flow (especially the escalation pause/resume seam) easy to read; the one
  place that genuinely needs concurrency — the mock operator HTTP page staying reachable while the
  main flow blocks waiting for a human — runs in a background thread (`src/escalation/operator_server.py`),
  which is the idiomatic way to mix "a tiny server" into an otherwise synchronous script.
- **Flat-file storage, single process, synchronous CLI.** Hundreds of tenants is a real constraint
  for the *design* (§4), but building queues/services for a one-tenant demo would be exactly the
  premature infrastructure the assignment says not to reward. `artifacts/*.json`, `evidence/<runId>/`,
  `runs/<runId>.*.json`.
- **One target app, built by me, deliberately legacy-flavored**: server-rendered tables, no test
  ids, no ARIA (`target_app/`, a small Flask app). This was the only way to *reliably* produce the
  specific runtime states the rubric asks for (not-found, permission-denied, validation error,
  session timeout) on demand, which a public demo site would not let me control.

**Trade-off I made explicitly:** the discovery loop and the replay engine are separate classes
rather than one "executor with an optional LLM." I considered unifying them, but the moment an LLM
is in the loop, the unit of work is "decide, then act, then see what happened" — replay's unit of
work is "assert, don't decide." Forcing one abstraction over both would have hidden that
difference instead of expressing it.

## 2. Artifact schema

`src/types/artifact.py` is the centerpiece. Design goals, in order: (1) a human reviewer can
understand the capability without replaying it, (2) a calling agent can validate inputs/outputs
mechanically, (3) replay can be fully deterministic, (4) it travels across tenants with minimal
change (§4).

Shape, briefly:

- **`inputs` / `outputs`** — typed, named, described; inputs carry a `sensitive` flag that drives
  redaction everywhere downstream. These are populated from the discovery run's own tool calls:
  when the model types a value it recognizes as coming from the goal (not a fixed constant), it
  sets a `parameter_hint` (`src/agent/tools.py`); the Recorder trusts that hint rather than
  guessing post-hoc which literals "look like" variables.
- **`steps`** — each one is `{action, locator, value, checkpoint, risk, requires_approval}`.
  `locator` is a **ranked bundle**, not a single selector: primary + ordered fallbacks, each with a
  `robustness_note` explaining the ranking (see `src/surface/locator_strategy.py` — role+accessible
  name beats id beats name-attribute CSS beats text beats structural CSS beats XPath). Every step
  asserts a `checkpoint` — "did we reach the state we expected," not "did the click not throw."
- **`recognized_outcomes`** — the schema-level answer to "business outcome vs. recoverable vs.
  hard failure." Each one names a step to check after, a locator whose presence signals it, a
  `kind` (`business` / `recoverable` / `escalate`), and what to do about it. This is **authored
  knowledge attached at record time**, not something automatically mined from one successful run —
  a single discovery run, by construction, never observes its own failure states.
  `target_app/known_outcomes.py` is the seed data for this demo; in production this would be a
  per-`app_id` registry a reviewer maintains, not a one-off per capability.
- **`target.base_url`** is the one field a tenant override is expected to replace (§4); everything
  else is tenant-independent by design.

## 3. Determinism & error handling

Every locator is resolved by trying the primary strategy, then falling through ranked fallbacks,
before failing (`PlaywrightAdapter._resolve_to_playwright`). Every step's `checkpoint` is a
declared wait condition (`element_visible` / `url_matches` / `network_idle` / `element_hidden`),
not an implicit assumption that the previous action worked.

One correctness issue I caught and fixed while building this: naively, a checkpoint synthesized
right after "type member_id, then click Search" would hardcode the *literal* path observed during
discovery (e.g. `/member/12345`), which would only ever match a replay called with that exact
member id — defeating the entire point of parameterizing it. `_templatized_path_pattern` (used by
`Recorder`) replaces any occurrence of a known parameter's discovery-time raw value with a wildcard before the
pattern is saved, so `/member/12345` becomes `/member/[^/]+` in the artifact. (Covered by
`src/recorder/test_recorder.py`.) This is the same idea as the canonicalization stretch goal,
applied where it's load-bearing rather than optional.

When a checkpoint fails, `error_taxonomy.classify_outcome` checks the live page against the
artifact's declared `recognized_outcomes` for that step — and *only* those, matched by step id, not
a global scan:

- **`business`** → returned to the caller as `status: "business_outcome"` with a named code
  (`MEMBER_NOT_FOUND`, `PERMISSION_DENIED`, `INVALID_INITIAL_DEPOSIT`). Not an error.
- **`recoverable`** → the declared `recovery_action` runs once, the original checkpoint is
  re-checked, and only then does the run continue or fall through to a hard failure.
- **`escalate`** → control transfers to a human (§5).
- **no match** → a genuine hard failure: `{step_id, expected, observed, message}`, plus a
  screenshot and a fresh DOM snapshot, so it's debuggable without re-running anything.

Secondary UI drift (a selector quietly breaking) surfaces the same way as any other unrecognized
state — a hard failure naming the step and what was expected — rather than being silently retried
into a false success.

## 4. Heterogeneity & multi-tenant

**Surface abstraction.** `SurfaceAdapter` (`src/surface/surface_adapter.py`, an `abc.ABC`) is the
seam: perceive, navigate, click, type_text, select_option, extract_text, wait_for, screenshot,
get_handoff_url. Everything above it — agent, recorder, replay, policy — is written against this
interface only. A legacy frameset app needs no new abstraction, only an adapter that flattens
frames into one perceived snapshot (same interface, uglier `css_path`/`xpath` fallbacks
internally). A desktop app needs an adapter backed by an OS accessibility API (UIA/AT-SPI) instead
of a DOM — `LocatorCandidate` already has room for that (a `role`+`role_name` strategy is
meaningful on desktop AX trees too); it would not touch `ArtifactStep`, `RecognizedOutcome`, the
Recorder, or the Replay Executor at all.

**Multi-tenant reuse.** An artifact is written to be base-url-agnostic: `target.base_url` is a
single field, and `ReplayExecutor.run` already accepts a `base_url_override` per invocation — the
same artifact, called with a different tenant's URL, is the mechanism for "one recording, many
tenants." What doesn't transfer automatically is per-tenant *markup* drift (a differently-skinned
instance of the same vendor product). The locator fallback chain absorbs small drift for free
(role+name survives re-theming; only structural CSS/XPath breaks). For larger drift, the design
answer — not built here — is a **tenant override layer**: a small patch keyed by `(app_id,
tenant_id)` that can replace one step's locator bundle or one recognized_outcome's copy-matched
text without touching the base artifact, plus a confidence/flakiness signal (stretch goal §8 in
the assignment) that flags when an artifact's success rate drops for a specific tenant, prompting
re-review rather than silent failure. I did not build the override store or a drift detector; I
kept the seam (`base_url_override`, and locators as data rather than code) so adding one is
additive.

## 5. Escalation & handoff

Control lives in exactly one place at a time, tracked implicitly by whichever thread currently
drives the adapter: the automation, or a human. `EscalationManager.raise_intervention`
(`src/escalation/`) is the only way to move from the former to the latter — it captures a
screenshot, writes an `InterventionRequest` (`runs/<runId>.intervention.json`), and starts a small
Flask operator page (in a background thread, via `werkzeug.serving.make_server` so it can be
cleanly stopped) that shows the reason, the screenshot, and a link to the browser's own CDP
endpoint (`PlaywrightAdapter.get_handoff_url`) — genuinely the same live session, not a clone or a
replay of one. The calling loop then blocks in `wait_for_resolution`, polling for
`runs/<runId>.resolution.json`.

Four concrete triggers, all exercised by this system: (1) an irreversible step
(`requires_approval: true`, e.g. the final "Confirm" click in `open-sub-account`) — replay/discovery
never auto-executes it; (2) a `recognized_outcome` explicitly marked `kind: "escalate"` (session
expiry — automatic re-auth isn't wired to a credential store, so it's a human decision, not a
silent retry); (3) repeated action *failures*, or a max-step/timeout dead-end, during discovery;
(4) the model repeating the identical *successful* action several turns in a row without
progressing (e.g. re-typing an already-filled field) — a real failure mode observed in actual
discovery runs, distinct from (3) because nothing raises an exception; each action genuinely
succeeds, it just never moves the task forward. Detected by comparing a signature of consecutive
decisions (tool name + target + value) rather than watching for errors.

**On resume, the code never re-executes the step that triggered escalation.** For an irreversible
action, that would risk double-submitting something like a fund transfer if the human already did
it live; instead, resume just re-perceives current state and moves on — if the human declined or
never actually clicked through, the next checkpoint (or the artifact's `final_checkpoint`)
correctly reports a hard failure rather than a false success. What's captured for the audit trail:
the operator's free-text note, a resolution timestamp, and a fresh screenshot at resume time —
logged alongside everything else in `evidence/<runId>/log.jsonl`.

**What's intentionally mocked, per the assignment's own scope note:** the operator UI is a bare
HTML form, not a co-browsing console, and manual keystroke/click-level capture during the human's
turn is not implemented — a real deployment would want the operator acting *through* the same
Playwright `page` object (so every action is still recorded), which I noted but didn't build. What
is real: the pause, the live session exposure, the resume signal, and the fact that a blocked
process genuinely continues afterward — there's no separate "fake" resume path
(`python -m src.cli resume` writes the exact same resolution file the operator page's form does).

## 6. Safety

`PolicyEngine` (`src/policy/policy_engine.py`) gates every action, in both loops, against
`src/config/allowlist.json`: allowed domains, allowed route patterns, allowed action types.
Anything outside it raises `PolicyViolationError` before it reaches the adapter — during discovery
this routes straight to escalation rather than silently failing or retrying.

Risk classification is name-pattern-based (`confirm`, `delete`, `close account`, ...) plus an
explicit model-provided hint (`irreversible: true` on a `click` tool call) — either signal marks a
step `irreversible`, and irreversible steps require human approval unconditionally, in both
discovery and replay. This is conservative by design: a false positive costs one unnecessary
escalation; a false negative risks an unauthorized money-moving action.

Redaction happens at two points, not one. First, live: `DiscoveryAgent` checks every typed field's
locator against sensitive-name patterns (password, SSN, account number, ...) and, if it matches,
**forces it into a parameter regardless of whether the model flagged it** — a real gap I found
while building this (an unflagged login password would otherwise be baked into the artifact as a
literal) and closed by making sensitivity detection independent of the model's cooperation. Second,
at every write boundary: `EvidenceStore`/`redaction.py` scrub named-sensitive fields and
value-shaped secrets (SSN/card-number/API-key patterns) before anything touches `log.jsonl` or
`transcript.json`. **Limits:** business data that isn't credential-shaped (e.g. an extracted
balance) is not redacted from evidence — it's the capability's actual output, and blanket-redacting
it would make the evidence useless for debugging; this is a deliberate scope line, documented here
rather than left implicit.

## 7. Cuts

- **Legacy-frameset and desktop adapters** — designed for (§4), not implemented. Only one
  `SurfaceAdapter` (Playwright) exists.
- **Tenant override store and artifact confidence/flakiness scoring** — the seam exists
  (`base_url_override`, `approval_state: draft/approved`, `require_approved` gate in
  `ReplayExecutor`), the store and the scorer don't.
- **Recoverable-outcome demo in the live app** — `attempt_recovery`'s `click` and `wait_and_retry`
  paths are unit-tested (`src/replay/test_error_taxonomy.py`) but not exercised end-to-end against
  the target app, since I didn't want to fabricate a fake interstitial just to hit the code path;
  `reauthenticate` is an explicit no-op stub pending a real credential store; the target app
  doesn't currently have a real recoverable UI state.
- **Operator action capture during manual control** — the human's live actions aren't captured
  step-by-step (§5); only a note and before/after evidence are.
- **Per-step semantic checkpoints beyond URL/visibility** (e.g. "the balance cell contains a
  positive number") — checkpoints confirm *reaching* a state, not deep-validating its content
  beyond what extraction/transform already type-checks.
- **No dispatcher in front of `discover`/`replay`.** Right now a human decides which command to
  run; nothing in the system itself answers "does a capability for this request already exist."
  I deliberately did not fold that check into `ReplayExecutor` — its entire value is a narrow,
  fast, fully-predictable contract (same latency, same cost, same behavior, every time), and a
  silent fallback into Discovery on a miss would break that guarantee for every caller, not just
  the miss case. It would also risk quietly bypassing the `draft`/`approved` gate: "no artifact
  found → auto-run Discovery → immediately replay the fresh result" would let an unreviewed
  capability act on a real request with nobody having looked at it. The right shape is a separate,
  deliberately dumb coordinator in front of both: resolve by exact capability id first (the
  realistic case — a calling agent already knows the name it wants, the same way it knows a
  function name, not a vague sentence to be matched), and if nothing matches, surface that and stop
  rather than auto-triggering Discovery — a human decides whether a new capability is worth
  teaching at all, and only then does Discovery run.

**What I'd build next:** that dispatcher, backed by a small `app_id`-keyed recognized_outcomes
registry (turns "hand-authored per capability" into "authored once per vendor app, reused") and
a tenant override layer, then the agent-facing capability catalog (stretch goal) the dispatcher
would resolve against, so a calling agent discovers and invokes artifacts by name instead of a
human passing `--capability` on a CLI.
