"""The model boundary. Discovery and probing are the only callers; replay never imports this."""

from __future__ import annotations

import os
from typing import Any, Protocol

DEFAULT_MODEL = "claude-opus-5-5"


class LLMError(Exception):
    pass


class LLM(Protocol):
    model: str

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Any:
        """Returns a message with .content blocks (.type, and for tool_use: .id .name .input)."""


class ClaudeLLM:
    """Model is chosen per role: CUA_MODEL_DISCOVERY / CUA_MODEL_PROBE, then CUA_MODEL.
    Requires a model with adaptive thinking (Claude Opus 5.x / Sonnet 5.x)."""

    def __init__(self, role: str) -> None:
        import anthropic

        self.model = os.environ.get(f"CUA_MODEL_{role.upper()}") or os.environ.get("CUA_MODEL") or DEFAULT_MODEL
        self.client = anthropic.Anthropic()

    def complete(self, system: str, messages: list[dict], tools: list[dict]) -> Any:
        response = self.client.messages.create(
            model=self.model,
            max_tokens=16000,
            system=system,
            tools=tools,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            cache_control={"type": "ephemeral"},
        )
        if response.stop_reason == "refusal":
            details = getattr(response, "stop_details", None)
            raise LLMError(f"the model declined this request (category: {getattr(details, 'category', None)})")
        if response.stop_reason == "max_tokens":
            raise LLMError("the model's reply was cut off at max_tokens")
        return response


def tool(name: str, description: str, /, **props: dict) -> dict:
    return {
        "name": name,
        "description": description,
        "strict": True,
        "input_schema": {"type": "object", "properties": props, "required": list(props),
                         "additionalProperties": False},
    }


S = {"type": "string"}
