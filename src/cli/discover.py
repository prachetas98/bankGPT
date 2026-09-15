from __future__ import annotations

import json
import os
import time
from typing import List

from src.agent.discovery_agent import DiscoveryAgent, DiscoveryOptions
from src.agent.llm_client import LlmClient
from src.cli.argv import parse_args, required
from src.evidence.evidence_store import EvidenceStore
from src.recorder.recorder import KnownOutcomeSeed, RecordParams, Recorder
from src.storage.artifact_store import ArtifactStore
from src.storage.run_store import RunStore
from src.surface.playwright_adapter import PlaywrightAdapter
from src.types.run import SuccessResult
from target_app.known_outcomes import MEMBER_LOOKUP_KNOWN_OUTCOMES


def _known_outcomes_for(capability_id: str) -> List[KnownOutcomeSeed]:
    """Per-capability seed knowledge of known non-happy-path states. A production system would
    look this up from a registry keyed by target.app_id; hardcoded here for the one target app
    this project ships. See target_app/known_outcomes.py and REPORT.md."""
    return MEMBER_LOOKUP_KNOWN_OUTCOMES if capability_id == "member-savings-lookup" else []


def discover_command(argv: List[str]) -> None:
    args = parse_args(argv)
    goal = required(args, "goal")
    capability_id = required(args, "capability")
    name = args.get("name", capability_id)
    description = args.get("description", goal)
    app_id = args.get("app-id", "bankgpt-member-services")
    entry_path = args.get("entry-path", "/login")
    max_steps = int(args["max-steps"]) if "max-steps" in args else 20
    # A CPU-run small local model can take real time per step (see llm_client.py) -- the
    # default here is generous specifically to avoid an escalation triggered by cumulative
    # inference slowness rather than an actual problem. Override with DISCOVERY_TIMEOUT_S.
    timeout_s = float(os.environ.get("DISCOVERY_TIMEOUT_S", "1800"))

    base_url = os.environ.get("TARGET_BASE_URL", "http://localhost:4000")
    model = os.environ.get("LOCAL_MODEL", "microsoft/Phi-3.5-mini-instruct")
    headed = os.environ.get("HEADED", "1") != "0"
    operator_port = int(os.environ.get("OPERATOR_SERVER_PORT", "4100"))

    run_id = f"discover-{capability_id}-{int(time.time() * 1000)}"
    evidence = EvidenceStore(run_id)
    run_store = RunStore()
    artifact_store = ArtifactStore()

    adapter = PlaywrightAdapter(headed=headed, cdp_port=9222)
    print(f"Loading {model} (first run downloads the weights -- can take a while, especially on CPU)...")
    llm = LlmClient(model)
    print("Model loaded.")
    agent = DiscoveryAgent(adapter, llm, evidence, run_store, operator_port)

    print(f"Starting discovery run {run_id}")
    print(f"  goal:   {goal}")
    print(f"  target: {base_url}{entry_path}")
    print(f"  evidence: {evidence.dir}")

    try:
        outcome = agent.run(
            DiscoveryOptions(
                run_id=run_id, capability_id=capability_id, goal=goal, base_url=base_url,
                entry_path=entry_path, max_steps=max_steps, timeout_s=timeout_s,
            )
        )
        evidence.write_result(outcome.result)

        print(f"\nDiscovery result: {outcome.result.status}")
        print(json.dumps(outcome.result.model_dump(), indent=2, default=str))

        if isinstance(outcome.result, SuccessResult):
            artifact = Recorder().build(
                RecordParams(
                    capability_id=capability_id,
                    name=name,
                    description=description,
                    discovery_run_id=run_id,
                    model=model,
                    goal=goal,
                    app_id=app_id,
                    base_url=base_url,
                    entry_path=entry_path,
                    trace=outcome.trace,
                    finish_summary=outcome.finish_summary,
                    final_checkpoint_locator=outcome.final_checkpoint_locator,
                    known_outcomes=_known_outcomes_for(capability_id),
                )
            )
            file = artifact_store.save(artifact)
            evidence.write_artifact_snapshot(artifact)
            print(f"\nSaved capability artifact -> {file} (approval_state: draft)")
            print(f"Review it, then replay with e.g.:\n  python -m src.cli replay --capability {capability_id} --params '{{\"member_id\":\"12345\"}}'")
        else:
            print(f"\nNo artifact recorded (run did not reach 'success'). See evidence at {evidence.dir}.")
    finally:
        adapter.close()
