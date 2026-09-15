from __future__ import annotations

import json
import os
import time
from typing import List

from src.cli.argv import parse_args, required
from src.evidence.evidence_store import EvidenceStore
from src.replay.replay_executor import ReplayExecutor, ReplayOptions
from src.storage.artifact_store import ArtifactStore
from src.storage.run_store import RunStore
from src.surface.playwright_adapter import PlaywrightAdapter


def replay_command(argv: List[str]) -> None:
    args = parse_args(argv)
    capability_id = required(args, "capability")
    params = json.loads(args["params"]) if "params" in args else {}
    base_url_override = args.get("base-url")
    require_approved = args.get("require-approved") == "true"

    headed = os.environ.get("HEADED", "1") != "0"
    operator_port = int(os.environ.get("OPERATOR_SERVER_PORT", "4100"))

    run_id = f"replay-{capability_id}-{int(time.time() * 1000)}"
    evidence = EvidenceStore(run_id)
    run_store = RunStore()
    artifact_store = ArtifactStore()
    artifact = artifact_store.load(capability_id)

    adapter = PlaywrightAdapter(headed=headed, cdp_port=9223)
    executor = ReplayExecutor(adapter, evidence, run_store, operator_port)

    print(f"Starting replay run {run_id}")
    print(f"  capability: {capability_id} v{artifact.version} (approval_state: {artifact.approval_state})")
    print(f"  params: {json.dumps(params)}")
    print(f"  evidence: {evidence.dir}")

    try:
        result = executor.run(
            artifact,
            ReplayOptions(run_id=run_id, params=params, base_url_override=base_url_override, require_approved=require_approved),
        )
        evidence.write_result(result)
        print(f"\nReplay result: {result.status}")
        print(json.dumps(result.model_dump(), indent=2, default=str))
    finally:
        adapter.close()
