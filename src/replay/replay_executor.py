from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional
from urllib.parse import urljoin

from src.escalation.escalation_manager import EscalationContext, EscalationManager
from src.evidence.evidence_store import EvidenceStore
from src.policy.policy_engine import PolicyEngine, ProposedAction
from src.replay.error_taxonomy import MatchedOutcome, attempt_recovery, classify_outcome
from src.storage.run_store import RunStore
from src.surface.surface_adapter import SurfaceAdapter
from src.types.action import WaitNetworkIdle
from src.types.artifact import ArtifactStep, CapabilityArtifact
from src.types.run import EscalatedResult, FailureResult, RunResult, SuccessResult, BusinessOutcomeResult, ErrorDetail
from src.util.transforms import apply_transform


@dataclass
class ReplayOptions:
    run_id: str
    params: Dict[str, Any] = field(default_factory=dict)
    base_url_override: Optional[str] = None  # per-tenant reuse hook -- see REPORT.md, Heterogeneity & multi-tenant
    require_approved: bool = False
    block_on_escalation: bool = True  # False: return status "escalated" immediately instead of blocking.


class ReplayExecutor:
    """
    THE PRODUCTION EXECUTION PATH (3.3). No LLM anywhere in this file.
    Every step: resolve -> policy-check -> act -> assert checkpoint. A
    missed checkpoint is never treated as "retry blindly" -- it goes
    straight to error_taxonomy.classify_outcome, which is the only branch
    point between a business outcome, a recoverable condition, and a hard
    failure.
    """

    def __init__(self, adapter: SurfaceAdapter, evidence: EvidenceStore, run_store: RunStore, operator_port: int):
        self._adapter = adapter
        self._evidence = evidence
        self._policy = PolicyEngine()
        self._escalation = EscalationManager(run_store, operator_port)

    def run(self, artifact: CapabilityArtifact, opts: ReplayOptions) -> RunResult:
        base_url = opts.base_url_override or artifact.target.base_url

        if opts.require_approved and artifact.approval_state != "approved":
            return self._fail(
                opts.run_id, artifact.id, "input-validation", "artifact.approval_state == 'approved'",
                artifact.approval_state, "Unattended replay requires an approved artifact.",
            )

        params_ok, params_message = self._validate_params(artifact, opts.params)
        if not params_ok:
            return self._fail(
                opts.run_id, artifact.id, "input-validation", "all required inputs present and typed",
                repr(opts.params), params_message,
            )

        outputs: Dict[str, Any] = {}

        entry_url = urljoin(base_url, artifact.target.entry_path)
        self._policy.check_action(ProposedAction(action_type="navigate", url=entry_url))
        self._adapter.navigate(entry_url)

        for step in artifact.steps:
            self._evidence.log("act", f"{step.action}: {step.description}", step_id=step.id)

            url = _substitute(step.url, base_url) if step.url else None
            policy_decision = self._policy.check_action(
                ProposedAction(action_type=step.action, url=url, locator=step.locator), step.risk
            )

            if step.requires_approval or policy_decision.requires_approval:
                escalated = self._handle_escalation(
                    artifact, opts, step, f"Step '{step.id}' is classified irreversible and requires human approval."
                )
                if escalated is not None:
                    return escalated
                # resumed: a human performed this step live; do not also execute it, just move on.
                continue

            try:
                self._act(step, url, opts.params)
            except Exception as err:  # noqa: BLE001
                return self._fail(
                    opts.run_id, artifact.id, step.id, f"{step.action} to execute", str(err),
                    f"Action threw during execution: {err}", outputs,
                )

            checkpoint_ok = self._adapter.wait_for(step.checkpoint)
            self._capture_step_evidence(step.id)

            if not checkpoint_ok:
                classification = classify_outcome(self._adapter, step.id, artifact.recognized_outcomes)

                if isinstance(classification, MatchedOutcome) and classification.kind == "business":
                    return BusinessOutcomeResult(
                        run_id=opts.run_id,
                        capability_id=artifact.id,
                        outcome=classification.outcome.business_outcome_code or classification.outcome.id,
                        detail=classification.outcome.description,
                        outputs=outputs,
                        evidence_dir=str(self._evidence.dir),
                    )

                if isinstance(classification, MatchedOutcome) and classification.kind == "recoverable":
                    recovered = attempt_recovery(self._adapter, classification.outcome)
                    rechecked = recovered and self._adapter.wait_for(step.checkpoint)
                    if not rechecked:
                        return self._fail(
                            opts.run_id, artifact.id, step.id, _describe(step.checkpoint),
                            "recovery attempted, checkpoint still not met",
                            f"Recognized recoverable condition '{classification.outcome.id}' but recovery did not restore the expected state.",
                            outputs,
                        )
                elif isinstance(classification, MatchedOutcome) and classification.kind == "escalate":
                    escalated = self._handle_escalation(
                        artifact, opts, step, classification.outcome.escalation_reason or classification.outcome.description
                    )
                    if escalated is not None:
                        return escalated
                    # resumed: re-check once more before moving on.
                    if not self._adapter.wait_for(step.checkpoint):
                        return self._fail(
                            opts.run_id, artifact.id, step.id, _describe(step.checkpoint),
                            "still not met after human intervention",
                            "Checkpoint still failed after human handoff completed.", outputs,
                        )
                else:
                    return self._fail(
                        opts.run_id, artifact.id, step.id, _describe(step.checkpoint), self._observed_summary(),
                        "Unrecognized state: no declared recognized_outcome matched, and the step checkpoint was not met.",
                        outputs,
                    )

            if step.action == "extract" and step.output_name and step.locator:
                raw = self._adapter.extract_text(step.locator)
                out_spec = next((o for o in artifact.outputs if o.name == step.output_name), None)
                outputs[step.output_name] = apply_transform(raw, out_spec.transform if out_spec else "none")
                self._evidence.log("checkpoint", f"Extracted {step.output_name}", step_id=step.id, data={"value": outputs[step.output_name]})

        if not self._adapter.wait_for(artifact.final_checkpoint):
            return self._fail(
                opts.run_id, artifact.id, "final-checkpoint", _describe(artifact.final_checkpoint), self._observed_summary(),
                "All steps completed but the overall goal checkpoint was not met.", outputs,
            )

        return SuccessResult(run_id=opts.run_id, capability_id=artifact.id, outputs=outputs, evidence_dir=str(self._evidence.dir))

    def _act(self, step: ArtifactStep, url: Optional[str], params: Dict[str, Any]) -> None:
        if step.action == "navigate":
            self._adapter.navigate(url)  # type: ignore[arg-type]
        elif step.action == "click":
            self._adapter.click(step.locator)  # type: ignore[arg-type]
        elif step.action == "type":
            self._adapter.type_text(step.locator, self._resolve_value(step, params))  # type: ignore[arg-type]
        elif step.action == "select_option":
            self._adapter.select_option(step.locator, self._resolve_value(step, params))  # type: ignore[arg-type]
        elif step.action == "extract":
            return  # handled after checkpoint passes, see run()
        elif step.action == "wait_for":
            self._adapter.wait_for(step.wait if step.wait else _default_wait(step.timeout_ms))

    def _resolve_value(self, step: ArtifactStep, params: Dict[str, Any]) -> str:
        if step.value is None:
            raise ValueError(f"Step '{step.id}' ({step.action}) has no declared value source.")
        if step.value.source == "literal":
            return step.value.value
        v = params.get(step.value.param)
        if v is None:
            raise ValueError(f"Missing required param '{step.value.param}' for step '{step.id}'.")
        return str(v)

    def _validate_params(self, artifact: CapabilityArtifact, params: Dict[str, Any]):
        missing = [i.name for i in artifact.inputs if i.required and (params.get(i.name) is None or params.get(i.name) == "")]
        if missing:
            return False, f"Missing required input(s): {', '.join(missing)}."
        return True, ""

    def _capture_step_evidence(self, step_id: str) -> None:
        path = self._evidence.next_screenshot_path(step_id)
        self._adapter.screenshot(path)
        self._evidence.log("checkpoint", "Checkpoint evaluated.", step_id=step_id, data={"screenshot_path": path})

    def _observed_summary(self) -> str:
        snapshot = self._adapter.perceive()
        return f'url={snapshot.url} title="{snapshot.title}" banner="{snapshot.banner_text or ""}"'

    def _handle_escalation(
        self, artifact: CapabilityArtifact, opts: ReplayOptions, step: ArtifactStep, reason: str
    ) -> Optional[RunResult]:
        """Returns a terminal RunResult if the escalation ends the run (non-blocking mode, or
        operator aborted); None if resumed and the caller should continue."""
        req, operator_url = self._escalation.raise_intervention(
            EscalationContext(run_id=opts.run_id, capability_id=artifact.id, mode="replay", step_id=step.id, reason=reason),
            self._adapter,
            self._evidence,
        )

        if not opts.block_on_escalation:
            return EscalatedResult(
                run_id=opts.run_id,
                capability_id=artifact.id,
                intervention_id=req.intervention_id,
                reason=reason,
                step_id=step.id,
                evidence_dir=str(self._evidence.dir),
                operator_url=operator_url,
            )

        resolution = self._escalation.wait_for_resolution(opts.run_id)
        self._evidence.log("control", f"Resolved: {resolution.action} -- {resolution.operator_note}", step_id=step.id)
        if resolution.action == "abort":
            return self._fail(opts.run_id, artifact.id, step.id, "human approval/action", "operator aborted", f"Operator note: {resolution.operator_note}")
        return None

    def _fail(
        self, run_id: str, capability_id: str, step_id: str, expected: str, observed: str, message: str,
        outputs: Optional[Dict[str, Any]] = None,
    ) -> RunResult:
        self._evidence.log("error", message, step_id=step_id, data={"expected": expected, "observed": observed})
        return FailureResult(
            run_id=run_id,
            capability_id=capability_id,
            error=ErrorDetail(step_id=step_id, expected=expected, observed=observed, message=message),
            evidence_dir=str(self._evidence.dir),
        )


def _substitute(templated_url: str, base_url: str) -> str:
    return templated_url.replace("{{base_url}}", base_url)


def _describe(condition) -> str:
    return f"condition '{condition.kind}' to become true"


def _default_wait(timeout_ms: int) -> WaitNetworkIdle:
    return WaitNetworkIdle(kind="network_idle", timeout_ms=timeout_ms)
