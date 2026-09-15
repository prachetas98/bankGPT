# Evidence

This directory is populated at runtime by `discover` and `replay` (see the demo path in the root
README). It is git-ignored except for a small set of curated sample runs the assignment asks for:

- `sample-discovery-run/` — a real discovery run (goal -> live LLM-driven Chromium session ->
  saved artifact). Produced by running `python -m src.cli discover` (no API key needed — the
  local model runs on this machine).
- `sample-replay-run/` — a deterministic replay of the resulting artifact on the happy path.
- `sample-replay-error-run/` — a replay hitting a business outcome or escalation (e.g. member_id
  `99999` for "not found", or `50000` for the session-timeout escalation path).

Each run directory contains:

```
log.jsonl        structured, timestamped events (observe/decide/act/checkpoint/outcome/error/control)
screenshots/     one PNG per step, plus one at any escalation point
transcript.json  the raw LLM message transcript (discovery only) — sensitive typed values redacted
artifact.json    snapshot of the artifact as saved at the end of this run (discovery only)
result.json      the final structured RunResult (success / business_outcome / failure / escalated)
```

**This directory is intentionally empty of real run data in the source submission** — see the
root README's note on why the discovery run must be produced live, not shipped pre-baked.
