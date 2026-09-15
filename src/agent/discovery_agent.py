from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from src.agent.llm_client import LlmClient
from src.agent.prompts import render_observation, system_prompt
from src.agent.trace import DiscoveredStep, ParameterHint
from src.escalation.escalation_manager import EscalationContext, EscalationManager
from src.evidence.evidence_store import EvidenceStore
from src.policy.policy_engine import PolicyEngine, PolicyViolationError, ProposedAction
from src.storage.run_store import RunStore
from src.surface.surface_adapter import SurfaceAdapter
from src.types.action import LocatorBundle, ValueLiteral, ValueParam, WaitNetworkIdle
from src.types.run import ErrorDetail, FailureResult, RunResult, SuccessResult
from src.util.transforms import apply_transform
from src.util.url import resolve_url

MAX_CONSECUTIVE_ERRORS = 3
# Small models sometimes repeat the identical action turn after turn without recognizing it
# already happened (e.g. re-typing a value that's already in the field instead of moving on
# to click Search). This never raises an exception -- each repeat "succeeds" -- so it needs
# its own detection, separate from MAX_CONSECUTIVE_ERRORS above.
MAX_REPEATED_ACTIONS = 3

# Caps how much conversation history gets sent to the model on each turn (roughly the last 5
# turns). Without this, prompt length -- and GPU memory -- grows without bound over a long
# run, since every step appends both the model's decision and a fresh full screen description
# to the history, and the whole thing gets re-sent every time. The goal is restated in the
# system prompt every turn regardless of windowing, and each observation is a self-sufficient
# snapshot of the current screen, so recent history is enough to decide the next action --
# this only trims what's sent to the model, never the full record kept for evidence/transcript.
MAX_CONTEXT_MESSAGES = 10

_NAME_ATTR_RE = re.compile(r'name="([^"]+)"')


@dataclass
class DiscoveryOptions:
    run_id: str
    capability_id: str
    goal: str
    base_url: str
    entry_path: str
    max_steps: int = 20
    timeout_s: float = 5 * 60


@dataclass
class DiscoveryOutcome:
    result: RunResult
    trace: List[DiscoveredStep] = field(default_factory=list)
    finish_summary: Optional[str] = None
    final_checkpoint_locator: Optional[LocatorBundle] = None


def _require(input_: Dict[str, Any], key: str, tool_name: str) -> Any:
    """Raises a clear, model-readable error instead of a bare KeyError. A raw 'Error: value'
    tells the model nothing about what went wrong or how to fix it -- real transcripts showed
    it getting stuck on exactly this kind of unhelpful feedback (see REPORT.md, Determinism &
    error handling)."""
    value = input_.get(key)
    if value is None:
        raise ValueError(
            f"The '{tool_name}' tool requires a '{key}' argument, but none was provided. "
            "Either include it, or choose a different tool this turn."
        )
    return value


def _parse_parameter_hint(hint: Any) -> Optional["ParameterHint"]:
    """The model is asked for {"name": ..., "description": ...} but real transcripts showed
    it sometimes sending a bare string (e.g. "member_id") instead -- accept both rather than
    crashing on `hint["name"]` when hint is actually a str."""
    if hint is None:
        return None
    if isinstance(hint, str):
        return ParameterHint(name=hint, description="")
    if isinstance(hint, dict) and hint.get("name"):
        return ParameterHint(name=hint["name"], description=hint.get("description", ""))
    return None


def _infer_field_name(locator: LocatorBundle) -> str:
    """Best-effort snake_case-ish name for an auto-parameterized sensitive field, e.g.
    input[name="password"] -> "password"."""
    match = _NAME_ATTR_RE.search(locator.primary.value)
    if match:
        return match.group(1)
    if locator.primary.role_name:
        return re.sub(r"[^a-z0-9]+", "_", locator.primary.role_name.lower())
    return "sensitive_value"


class DiscoveryAgent:
    """
    The observe -> decide -> act loop (3.1). Every side effect on the live
    surface goes through `policy.check_action` first and is logged to
    `evidence` -- this loop has no privileged bypass of either.

    Three ways this loop ends without a hard crash, all deliberate:
     - finish: goal reached, caller gets outputs + a trace to hand to Recorder.
     - give_up / policy block / repeated errors / max steps / timeout: treated
       uniformly as "stuck" and routed to EscalationManager (3.6). A human can
       resume (agent tries again with fresh eyes) or abort (run ends as a
       failure, but with full evidence of exactly where and why).
    """

    def __init__(
        self,
        adapter: SurfaceAdapter,
        llm: LlmClient,
        evidence: EvidenceStore,
        run_store: RunStore,
        operator_port: int,
    ):
        self._adapter = adapter
        self._llm = llm
        self._evidence = evidence
        self._policy = PolicyEngine()
        self._escalation = EscalationManager(run_store, operator_port)

    def run(self, opts: DiscoveryOptions) -> DiscoveryOutcome:
        trace: List[DiscoveredStep] = []
        outputs: Dict[str, Any] = {}
        sensitive_typed_values: List[str] = []
        consecutive_errors = 0
        repeat_count = 0
        last_signature: Optional[tuple] = None
        started_at = time.monotonic()

        entry_url = resolve_url(opts.base_url, opts.entry_path)
        self._policy.check_action(ProposedAction(action_type="navigate", url=entry_url))
        self._adapter.navigate(entry_url)
        snapshot = self._adapter.perceive()

        system = system_prompt(opts.goal, opts.base_url)
        messages: List[Dict[str, Any]] = [{"role": "user", "content": render_observation(snapshot, 0, opts.max_steps)}]

        try:
            for step in range(opts.max_steps):
                if time.monotonic() - started_at > opts.timeout_s:
                    return self._escalate_or_fail(opts, trace, outputs, "Wall-clock timeout reached without completing the goal.")

                print(f"  step {step + 1}/{opts.max_steps}: thinking...", flush=True)
                decision = self._llm.decide(system, messages[-MAX_CONTEXT_MESSAGES:])
                # LlmClient already returns plain dicts (not SDK objects), so `messages` stays
                # directly JSON-serializable for write_transcript below, and a later replayed
                # conversation turn only ever depends on plain-dict content, same as the
                # tool_result/text blocks constructed further down in this loop.
                messages.append({"role": "assistant", "content": decision.raw_assistant_content})
                print(f"  step {step + 1}/{opts.max_steps}: {decision.tool_name} -- {decision.input.get('rationale', '')}", flush=True)
                self._evidence.log("decide", f"{decision.tool_name}: {decision.input.get('rationale', '')}", step_id=f"step-{step}")

                signature = (decision.tool_name, decision.input.get("ref"), decision.input.get("value"), decision.input.get("url"))
                repeat_count = repeat_count + 1 if signature == last_signature else 1
                last_signature = signature

                if repeat_count >= MAX_REPEATED_ACTIONS:
                    reason = f"Model repeated the same action ({decision.tool_name}) {repeat_count} times in a row without progressing -- likely stuck in a loop."
                    self._evidence.log("error", reason, step_id=f"step-{step}")
                    resumed = self._try_resume_or_abort(opts, f"step-{step}", reason)
                    if not resumed:
                        return DiscoveryOutcome(
                            trace=trace,
                            result=FailureResult(
                                run_id=opts.run_id,
                                capability_id=opts.capability_id,
                                error=ErrorDetail(
                                    step_id=f"step-{step}", expected="progress toward the goal",
                                    observed=reason, message=reason,
                                ),
                                evidence_dir=str(self._evidence.dir),
                            ),
                        )
                    repeat_count = 0
                    last_signature = None
                    snapshot = self._adapter.perceive()
                    messages.append(
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "tool_result",
                                    "tool_use_id": decision.tool_use_id,
                                    "content": "A human intervened after a repeated-action loop was detected. Continuing from the current state.",
                                },
                                {"type": "text", "text": render_observation(snapshot, step + 1, opts.max_steps)},
                            ],
                        }
                    )
                    continue

                result_summary: str
                stop: Optional[DiscoveryOutcome] = None

                try:
                    result_summary, stop = self._execute_action(
                        opts, step, decision.tool_name, decision.input, trace, outputs, sensitive_typed_values
                    )
                    consecutive_errors = 0
                except Exception as err:  # noqa: BLE001 - any action can legitimately fail at runtime
                    consecutive_errors += 1
                    message = str(err)
                    self._evidence.log("error", message, step_id=f"step-{step}")
                    result_summary = f"Error: {message}"

                    if isinstance(err, PolicyViolationError) or consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                        reason = (
                            f"Policy blocked an action: {message}"
                            if isinstance(err, PolicyViolationError)
                            else f"{consecutive_errors} consecutive action failures -- likely stuck."
                        )
                        resumed = self._try_resume_or_abort(opts, f"step-{step}", reason)
                        if not resumed:
                            return DiscoveryOutcome(
                                trace=trace,
                                result=FailureResult(
                                    run_id=opts.run_id,
                                    capability_id=opts.capability_id,
                                    error=ErrorDetail(
                                        step_id=f"step-{step}",
                                        expected="action to succeed or be approved",
                                        observed=reason,
                                        message=message,
                                    ),
                                    evidence_dir=str(self._evidence.dir),
                                ),
                            )
                        consecutive_errors = 0

                if stop is not None:
                    return stop

                snapshot = self._adapter.perceive()
                messages.append(
                    {
                        "role": "user",
                        "content": [
                            {"type": "tool_result", "tool_use_id": decision.tool_use_id, "content": result_summary},
                            {"type": "text", "text": render_observation(snapshot, step + 1, opts.max_steps)},
                        ],
                    }
                )

            return self._escalate_or_fail(
                opts, trace, outputs, f"Reached max step count ({opts.max_steps}) without completing the goal."
            )
        finally:
            self._evidence.write_transcript(messages, sensitive_typed_values)

    def _execute_action(
        self,
        opts: DiscoveryOptions,
        step: int,
        tool_name: str,
        input_: Dict[str, Any],
        trace: List[DiscoveredStep],
        outputs: Dict[str, Any],
        sensitive_typed_values: List[str],
    ) -> Tuple[str, Optional[DiscoveryOutcome]]:
        step_id = f"step-{step}"

        if tool_name == "navigate":
            url = resolve_url(opts.base_url, str(_require(input_, "url", "navigate")))
            self._policy.check_action(ProposedAction(action_type="navigate", url=url))
            url_before = self._adapter.current_url()
            self._adapter.navigate(url)
            url_after = self._adapter.current_url()
            trace.append(DiscoveredStep(index=step, action="navigate", rationale=str(input_.get("rationale", "")), risk="safe", url=url, url_after=url_after))
            self._log_act(step_id, "navigate", {"url": url})
            if url_before == url_after:
                return (
                    f"Navigated to {url}, which is the page you were already on -- this had no "
                    "effect. Do not navigate here again; choose a different next action based on "
                    "what's actually shown below.",
                    None,
                )
            return f"Navigated to {url}.", None

        if tool_name == "click":
            locator = self._adapter.resolve_element_ref(str(_require(input_, "ref", "click")))
            explicit_risk = "irreversible" if input_.get("irreversible") is True else None
            decision = self._policy.check_action(ProposedAction(action_type="click", locator=locator), explicit_risk)

            if decision.requires_approval:
                reason = f'Model wants to click "{locator.primary.role_name or locator.primary.value}", classified irreversible. Awaiting human approval/action.'
                resumed = self._try_resume_or_abort(opts, step_id, reason)
                if not resumed:
                    return "Irreversible click blocked pending human approval; run aborted.", DiscoveryOutcome(
                        trace=trace,
                        result=FailureResult(
                            run_id=opts.run_id,
                            capability_id=opts.capability_id,
                            error=ErrorDetail(step_id=step_id, expected="human approval", observed="operator aborted", message="Irreversible action not approved."),
                            evidence_dir=str(self._evidence.dir),
                        ),
                    )
                return "A human handled this step manually during the pause. Continuing from the current state.", None

            self._adapter.click(locator)
            trace.append(DiscoveredStep(index=step, action="click", rationale=str(input_.get("rationale", "")), risk=decision.risk, locator=locator, url_after=self._adapter.current_url()))
            self._log_act(step_id, "click", {"ref": input_.get("ref"), "name": locator.primary.role_name or locator.primary.value})
            return f"Clicked {input_.get('ref')}.", None

        if tool_name == "type":
            locator = self._adapter.resolve_element_ref(str(_require(input_, "ref", "type")))
            self._policy.check_action(ProposedAction(action_type="type", locator=locator))
            value = str(_require(input_, "value", "type"))
            self._adapter.type_text(locator, value)

            parameter_hint = _parse_parameter_hint(input_.get("parameter_hint"))
            is_sensitive = self._is_sensitive_target(locator, parameter_hint.name if parameter_hint else None)
            # Guardrail: a sensitive-looking field (password, SSN, account number, ...) is
            # ALWAYS turned into a declared, redacted parameter, regardless of whether the
            # model thought to flag it -- the artifact must never bake in a literal secret
            # just because the model treated it as "part of the fixed flow." See 3.4 and
            # REPORT.md, "Safety."
            if is_sensitive and parameter_hint is None:
                parameter_hint = ParameterHint(
                    name=_infer_field_name(locator),
                    description="Sensitive value -- must be supplied at replay time, never stored in the artifact.",
                )
            if parameter_hint is None or is_sensitive:
                sensitive_typed_values.append(value)

            trace.append(
                DiscoveredStep(
                    index=step,
                    action="type",
                    rationale=str(input_.get("rationale", "")),
                    risk="safe",
                    locator=locator,
                    value=ValueParam(source="param", param=parameter_hint.name) if parameter_hint else ValueLiteral(source="literal", value=value),
                    parameter_hint=parameter_hint,
                    raw_value=value,
                    url_after=self._adapter.current_url(),
                )
            )
            self._log_act(step_id, "type", {"ref": input_.get("ref"), "parameter_hint": parameter_hint})
            suffix = f" (parameter: {parameter_hint.name})" if parameter_hint else ""
            return (
                f"Typed a value into {input_.get('ref')}{suffix}. This field now already contains "
                "that value -- do not type into it again. Choose a different next action now.",
                None,
            )

        if tool_name == "select_option":
            locator = self._adapter.resolve_element_ref(str(_require(input_, "ref", "select_option")))
            self._policy.check_action(ProposedAction(action_type="select_option", locator=locator))
            value = str(_require(input_, "value", "select_option"))
            self._adapter.select_option(locator, value)

            parameter_hint = _parse_parameter_hint(input_.get("parameter_hint"))

            trace.append(
                DiscoveredStep(
                    index=step,
                    action="select_option",
                    rationale=str(input_.get("rationale", "")),
                    risk="safe",
                    locator=locator,
                    value=ValueParam(source="param", param=parameter_hint.name) if parameter_hint else ValueLiteral(source="literal", value=value),
                    parameter_hint=parameter_hint,
                    raw_value=value,
                    url_after=self._adapter.current_url(),
                )
            )
            self._log_act(step_id, "select_option", {"ref": input_.get("ref"), "value": value, "parameter_hint": parameter_hint})
            return f"Selected an option in {input_.get('ref')}.", None

        if tool_name == "extract":
            locator = self._adapter.resolve_element_ref(str(_require(input_, "ref", "extract")))
            self._policy.check_action(ProposedAction(action_type="extract", locator=locator))
            raw = self._adapter.extract_text(locator)
            transform = input_.get("transform") or "none"
            value = apply_transform(raw, transform)
            output_name = str(_require(input_, "output_name", "extract"))

            # Guardrail: if the field being read looks sensitive (e.g. the model tries to
            # "confirm" a password it just typed by reading it back), never let the raw value
            # reach outputs, the evidence log, or -- critically -- the tool_result text that
            # gets embedded straight into transcript.json (log redaction alone doesn't cover
            # that path; see REPORT.md, Safety).
            is_sensitive = self._is_sensitive_target(locator, output_name)
            stored_value = "[REDACTED]" if is_sensitive else value
            outputs[output_name] = stored_value

            trace.append(
                DiscoveredStep(
                    index=step,
                    action="extract",
                    rationale=str(input_.get("rationale", "")),
                    risk="safe",
                    locator=locator,
                    output_name=output_name,
                    output_description=str(input_.get("output_description", "")),
                    transform=transform,
                    url_after=self._adapter.current_url(),
                )
            )
            self._log_act(step_id, "extract", {"output_name": output_name, "value": stored_value})
            return f"Extracted {output_name} = {stored_value!r}.", None

        if tool_name == "wait_for":
            self._adapter.wait_for(WaitNetworkIdle(kind="network_idle", timeout_ms=int(input_.get("timeout_ms", 8000))))
            trace.append(DiscoveredStep(index=step, action="wait_for", rationale=str(input_.get("rationale", "")), risk="safe", url_after=self._adapter.current_url()))
            self._log_act(step_id, "wait_for", {})
            return "Waited for network idle.", None

        if tool_name == "finish":
            checkpoint_ref = str(_require(input_, "checkpoint_ref", "finish"))
            final_locator = self._adapter.resolve_element_ref(checkpoint_ref)
            screenshot_path = self._evidence.next_screenshot_path("finish")
            self._adapter.screenshot(screenshot_path)
            self._evidence.log("outcome", f"finish: {input_.get('summary')}", step_id=step_id, data={"screenshot_path": screenshot_path})
            return "Goal marked complete.", DiscoveryOutcome(
                trace=trace,
                finish_summary=str(input_.get("summary")),
                final_checkpoint_locator=final_locator,
                result=SuccessResult(run_id=opts.run_id, capability_id=opts.capability_id, outputs=outputs, evidence_dir=str(self._evidence.dir)),
            )

        if tool_name == "give_up":
            reason = str(_require(input_, "reason", "give_up"))
            resumed = self._try_resume_or_abort(opts, step_id, f"Model gave up: {reason}")
            if resumed:
                return "A human intervened after give_up. Continuing from the current state.", None
            return "Run aborted after give_up.", DiscoveryOutcome(
                trace=trace,
                result=FailureResult(
                    run_id=opts.run_id,
                    capability_id=opts.capability_id,
                    error=ErrorDetail(step_id=step_id, expected="goal completion", observed="model gave up", message=reason),
                    evidence_dir=str(self._evidence.dir),
                ),
            )

        raise ValueError(f"Unknown tool '{tool_name}'.")

    def _try_resume_or_abort(self, opts: DiscoveryOptions, step_id: str, reason: str) -> bool:
        """Returns True if the human said "resume", False if "abort" (or timed out)."""
        req, _url = self._escalation.raise_intervention(
            EscalationContext(run_id=opts.run_id, capability_id=opts.capability_id, mode="discovery", step_id=step_id, reason=reason),
            self._adapter,
            self._evidence,
        )
        resolution = self._escalation.wait_for_resolution(opts.run_id)
        self._evidence.log("control", f"Resolved: {resolution.action} -- {resolution.operator_note}", step_id=step_id, data={"intervention_id": req.intervention_id})
        return resolution.action == "resume"

    def _escalate_or_fail(
        self, opts: DiscoveryOptions, trace: List[DiscoveredStep], outputs: Dict[str, Any], reason: str
    ) -> DiscoveryOutcome:
        resumed = self._try_resume_or_abort(opts, "loop-boundary", reason)
        if resumed:
            # A human fixed things up manually; report success with whatever was extracted so far.
            return DiscoveryOutcome(
                trace=trace,
                result=SuccessResult(run_id=opts.run_id, capability_id=opts.capability_id, outputs=outputs, evidence_dir=str(self._evidence.dir)),
            )
        return DiscoveryOutcome(
            trace=trace,
            result=FailureResult(
                run_id=opts.run_id,
                capability_id=opts.capability_id,
                error=ErrorDetail(step_id="loop-boundary", expected="goal completion", observed=reason, message=reason),
                evidence_dir=str(self._evidence.dir),
            ),
        )

    def _log_act(self, step_id: str, action: str, data: Dict[str, Any]) -> None:
        self._evidence.log("act", action, step_id=step_id, data=data, sensitive_keys={"value"})

    def _is_sensitive_target(self, locator: LocatorBundle, hinted_name: Optional[str]) -> bool:
        candidates = [hinted_name, locator.primary.role_name, locator.primary.value, *[f.value for f in locator.fallbacks]]
        return any(c and self._policy.is_sensitive_field_name(c) for c in candidates)
