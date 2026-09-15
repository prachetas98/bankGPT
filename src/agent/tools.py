"""
The tool set IS the action vocabulary (see types/action.py). Every tool
requires `rationale` -- forcing the model to state why before acting gives us
an audit trail without depending on it volunteering free text, which
tool_choice={"type": "any"} does not guarantee.

`parameter_hint` on type/select_option is how the model tells the Recorder
"this value came from the goal, not a fixed constant" -- the Recorder trusts
it to build the artifact's declared inputs (see recorder/recorder.py).
"""
from __future__ import annotations

from typing import Any, Dict, List

DISCOVERY_TOOLS: List[Dict[str, Any]] = [
    {
        "name": "navigate",
        "description": "Go to an absolute URL within the target application.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rationale": {"type": "string"},
                "url": {"type": "string"},
            },
            "required": ["rationale", "url"],
        },
    },
    {
        "name": "click",
        "description": "Click an interactive element by its ref from the most recent observation.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rationale": {"type": "string"},
                "ref": {"type": "string", "description": "Element ref, e.g. 'e3'."},
                "irreversible": {
                    "type": "boolean",
                    "description": "Set true if this click submits/confirms an action that cannot be trivially undone (e.g. transferring funds, closing an account).",
                },
            },
            "required": ["rationale", "ref"],
        },
    },
    {
        "name": "type",
        "description": "Type a value into a text field by its ref, replacing any existing content.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rationale": {"type": "string"},
                "ref": {"type": "string"},
                "value": {"type": "string"},
                "parameter_hint": {
                    "type": "object",
                    "description": "Set this when the value came from the GOAL text (e.g. a member id), not a fixed constant baked into the flow.",
                    "properties": {
                        "name": {"type": "string", "description": "snake_case parameter name, e.g. member_id."},
                        "description": {"type": "string"},
                    },
                },
            },
            "required": ["rationale", "ref", "value"],
        },
    },
    {
        "name": "select_option",
        "description": "Choose an option in a dropdown/select by its ref.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rationale": {"type": "string"},
                "ref": {"type": "string"},
                "value": {"type": "string"},
                "parameter_hint": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
                },
            },
            "required": ["rationale", "ref", "value"],
        },
    },
    {
        "name": "extract",
        "description": "Read text from an element by its ref and declare it as a named output of this capability.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rationale": {"type": "string"},
                "ref": {"type": "string"},
                "output_name": {"type": "string", "description": "snake_case output name, e.g. savings_balance."},
                "output_description": {"type": "string"},
                "transform": {"type": "string", "enum": ["none", "parse_currency", "parse_integer", "trim"]},
            },
            "required": ["rationale", "ref", "output_name", "output_description"],
        },
    },
    {
        "name": "wait_for",
        "description": "Wait for the page to settle (network idle) before observing again. Use sparingly, after navigate/click if the page seems to still be loading.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rationale": {"type": "string"},
                "timeout_ms": {"type": "number"},
            },
            "required": ["rationale"],
        },
    },
    {
        "name": "finish",
        "description": "Declare the goal complete. Only call this once the observation clearly shows the goal state was reached.",
        "input_schema": {
            "type": "object",
            "properties": {
                "rationale": {"type": "string"},
                "summary": {"type": "string"},
                "checkpoint_ref": {
                    "type": "string",
                    "description": "Ref of the element whose presence proves the goal was reached (used as the artifact's final checkpoint).",
                },
            },
            "required": ["rationale", "summary", "checkpoint_ref"],
        },
    },
    {
        "name": "give_up",
        "description": "Declare that you cannot safely or successfully proceed (e.g. stuck in a loop, missing information, need a decision only a human should make).",
        "input_schema": {
            "type": "object",
            "properties": {
                "rationale": {"type": "string"},
                "reason": {"type": "string"},
            },
            "required": ["rationale", "reason"],
        },
    },
]
