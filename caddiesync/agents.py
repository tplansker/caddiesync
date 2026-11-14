"""The three AI agents. Each is a PydanticAI Agent on gpt-6-luna with a fixed structured output.

The instructions for each agent are loaded once and are byte-identical on every call, so OpenAI can serve
them from its prompt cache (billed at the cached-input rate). Anything that changes per run (today's date,
the golfer's words, the course facts) goes in the user message, after the cached prefix.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_ai import Agent

from .config import build_model
from .models import AccessibilityAssessment, ConciergeReply, GolferRequest

# Low reasoning effort: these are short form-filling and scoring tasks. max_tokens also caps reasoning tokens.
SETTINGS = {"max_tokens": 4_000, "openai_reasoning_effort": "low"}
PROMPTS = Path(__file__).resolve().parent / "prompts"


def _prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text(encoding="utf-8").strip()


@lru_cache(maxsize=1)
def agents() -> dict[str, Agent]:
    model = build_model()
    return {
        "profiler": Agent(model, name="profiler", output_type=GolferRequest,
                          instructions=_prompt("profiler"), model_settings=SETTINGS),
        "analyst": Agent(model, name="course_analyst", output_type=AccessibilityAssessment,
                         instructions=_prompt("analyst"), model_settings=SETTINGS),
        "concierge": Agent(model, name="concierge", output_type=ConciergeReply,
                           instructions=_prompt("concierge"), model_settings=SETTINGS),
    }
