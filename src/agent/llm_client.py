from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import torch
from transformers import AutoModelForCausalLM, AutoTokenizer

from src.agent.tools import DISCOVERY_TOOLS

# Small (3.8B), CPU-friendly, MIT-licensed, publicly accessible (no HuggingFace login or
# license gate) as of this writing -- see REPORT.md §1 for why this replaced two earlier,
# gated choices.
MODEL_ID = "microsoft/Phi-3.5-mini-instruct"


def _extract_first_json_object(text: str) -> Optional[Dict[str, Any]]:
    """The model frequently keeps generating past a complete JSON tool call -- there's no
    native stop signal for a format we invented via prompting, unlike a real tool-calling
    API, so it often trails off into more text (another sentence, a repeated block) after
    an otherwise-correct JSON object. A naive `json.loads` on the whole string fails on that
    trailing content ("Extra data: ..."); `raw_decode` parses exactly one JSON value starting
    at a position and simply ignores whatever comes after it, which is what we actually want."""
    start = text.find("{")
    if start == -1:
        return None
    try:
        parsed, _end_index = json.JSONDecoder().raw_decode(text, start)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, dict) or "tool_name" not in parsed:
        return None
    return parsed


@dataclass
class LlmDecision:
    tool_name: str
    tool_use_id: str
    input: Dict[str, Any]
    raw_assistant_content: List[Any]


def _tool_menu_text() -> str:
    """There's no native `tools=` parameter for raw local generation, so the menu and the
    required JSON envelope have to be spelled out inside the prompt itself -- this is the
    local-model equivalent of DISCOVERY_TOOLS being passed to a hosted API's tool-calling
    parameter. Deliberately compact (field names only, not the full nested JSON Schema) --
    on CPU, prompt length directly costs wall-clock time on every single step, and the full
    schema dump was adding a lot of tokens for no benefit a small model actually uses."""
    lines = [
        "Respond with ONLY a single JSON object -- no markdown code fences, nothing before or"
        " after it. Your entire response must start with { and end with }, in this EXACT key"
        " order -- write your reasoning FIRST, then let it determine your tool choice, not the"
        " other way around (deciding the tool before reasoning about it leads to contradicting"
        " your own stated reason):",
        '{"rationale": "<brief reason for the action you are about to take>", "tool_name": "<name>", "arguments": {...other fields for that tool...}}',
        "",
        "Tools -- name(other required fields, [optional fields]): description",
    ]
    for tool in DISCOVERY_TOOLS:
        schema = tool["input_schema"]
        required = set(schema.get("required", []))
        fields = [
            (name if name in required else f"[{name}]")
            for name in schema.get("properties", {})
            if name != "rationale"
        ]
        lines.append(f"- {tool['name']}({', '.join(fields)}): {tool['description']}")
    return "\n".join(lines)


class LlmClient:
    """Local, key-free replacement for a hosted-API client: loads the model's weights once
    and runs inference on this machine. No account, no network call at decide-time, no
    per-call cost. Swappable independently of everything else in the system --
    discovery_agent.py only ever depends on the LlmDecision shape `decide()` returns, never
    on how it was produced."""

    def __init__(self, model: str = MODEL_ID):
        # HF_TOKEN is unused by MODEL_ID's default (it isn't gated) -- kept available for
        # anyone who points LOCAL_MODEL at a gated checkpoint instead.
        # Deliberately NOT passing trust_remote_code=True: Phi-3 architecture has native
        # support in modern `transformers`, and forcing the model repo's own bundled code
        # instead risks it being stale against this library version's internals (this is
        # what caused a `DynamicCache has no attribute 'seen_tokens'` failure in testing).
        token = os.environ.get("HF_TOKEN") or None
        try:
            self._tokenizer = AutoTokenizer.from_pretrained(model, token=token)
            self._model = AutoModelForCausalLM.from_pretrained(
                model,
                token=token,
                dtype=torch.bfloat16 if torch.cuda.is_available() else torch.float32,
                device_map="auto" if torch.cuda.is_available() else None,
            ).eval()
        except OSError as exc:
            message = str(exc).lower()
            if any(kw in message for kw in ("gated", "access", "401", "403", "token")):
                raise RuntimeError(
                    f"'{model}' requires HuggingFace authentication. Accept the license at "
                    f"https://huggingface.co/{model} and set HF_TOKEN in .env (see .env.example)."
                ) from exc
            raise

    def decide(self, system: str, messages: List[Dict[str, Any]]) -> LlmDecision:
        # This checkpoint's chat template natively supports "system"/"user"/"assistant"
        # roles -- no role folding or remapping needed here.
        chat = [
            {"role": "system", "content": f"{system}\n\n{_tool_menu_text()}"},
            *_flatten(messages),
        ]

        inputs = self._tokenizer.apply_chat_template(
            chat, add_generation_prompt=True, tokenize=True, return_dict=True, return_tensors="pt"
        )
        inputs = {k: v.to(self._model.device) for k, v in inputs.items()}

        with torch.inference_mode():
            output_ids = self._model.generate(
                **inputs,
                max_new_tokens=300,  # generous now that GPU makes per-token cost cheap -- a tight
                                     # cap on CPU previously truncated valid JSON mid-object when
                                     # the model reasoned in prose before it (see _tool_menu_text)
                # Mild sampling, not greedy: when the screen genuinely hasn't changed between
                # turns (e.g. a repeated action left the page identical), greedy decoding
                # (do_sample=False) is mathematically guaranteed to reproduce the exact same
                # output forever, since identical input always argmaxes to the same token under
                # greedy search -- there's no way out of that loop from the prompt side alone.
                # A little randomness gives the model an actual chance to pick something else
                # once repeat-detection or a stronger instruction nudges it, rather than a
                # deterministic dead end.
                do_sample=True,
                temperature=0.4,
                top_p=0.9,
                pad_token_id=self._tokenizer.eos_token_id,
            )

        prompt_len = inputs["input_ids"].shape[-1]
        raw_text = self._tokenizer.decode(
            output_ids[0][prompt_len:], skip_special_tokens=True
        ).strip()

        parsed = _extract_first_json_object(raw_text)
        if parsed is None:
            raise RuntimeError(f"Model did not return a parseable JSON tool call. Raw output: {raw_text!r}")

        # `rationale` is now a top-level key (see _tool_menu_text -- reasoning has to come
        # before the tool choice in generation order to actually inform it), but everything
        # downstream (DiscoveryAgent, tools.py's schemas) still expects it inside `input`
        # alongside the tool's other arguments, so it's merged back in here.
        arguments = dict(parsed.get("arguments", {}))
        arguments["rationale"] = parsed.get("rationale", "")

        return LlmDecision(
            tool_name=parsed["tool_name"],
            tool_use_id=str(uuid.uuid4()),  # no real API call id exists locally; synthesized here
            input=arguments,
            raw_assistant_content=[{"type": "text", "text": raw_text}],
        )


def _flatten(messages: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    """Collapses the structured content-block lists built for a hosted tool-calling API's
    tool_result/text blocks down to plain {"role", "content": str} turns."""
    flat: List[Dict[str, str]] = []
    for m in messages:
        content = m["content"]
        if isinstance(content, str):
            flat.append({"role": m["role"], "content": content})
        else:
            text = "\n".join(b.get("text") or b.get("content") or "" for b in content)
            flat.append({"role": m["role"], "content": text})
    return flat
