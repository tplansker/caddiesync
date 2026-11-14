"""Settings, the gpt-6-luna model (through Portkey), and dollar costs.

.env lookup order: this repo's folder first, then the folder one level above it (where the teaching staff
keep their shared .env). load_dotenv never overrides a value that is already set, so the repo's own .env
wins, and real environment variables (Render) win over both.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUTPUT = ROOT / "output"
CACHE_DIR = ROOT / "cache"

load_dotenv(ROOT / ".env")
load_dotenv(ROOT.parent / ".env")

REQUIRED_MODEL = "gpt-6-luna"  # the only model allowed on the course PORTKEY_API_KEY
MODEL_NAME = os.getenv("OPENAI_MODEL", REQUIRED_MODEL).strip() or REQUIRED_MODEL
if os.getenv("PORTKEY_API_KEY", "").strip() and MODEL_NAME != REQUIRED_MODEL:
    # The course key only allows gpt-6-luna. A stale OPENAI_MODEL in someone's .env must not break the app.
    print(f"[caddiesync] OPENAI_MODEL='{MODEL_NAME}' is not allowed on the course key; using {REQUIRED_MODEL}.")
    MODEL_NAME = REQUIRED_MODEL
PORTKEY_BASE_URL = os.getenv("PORTKEY_BASE_URL", "https://api.portkey.ai/v1").strip()
PORTKEY_FORCE_REFRESH = os.getenv("PORTKEY_FORCE_REFRESH", "true").strip().lower() != "false"
DAILY_BUDGET_USD = float(os.getenv("DAILY_BUDGET_USD", "2.00"))

PRICES = json.loads((DATA / "prices.json").read_text(encoding="utf-8"))


class ConfigError(RuntimeError):
    """Raised with a message that is safe to show the user (never contains a key)."""


def _portkey_client(key: str) -> Any:
    from openai import AsyncOpenAI

    headers = {"x-portkey-api-key": key}
    if PORTKEY_FORCE_REFRESH:  # skip Portkey's response replay so token counts are real
        headers["x-portkey-cache-force-refresh"] = "true"
    return AsyncOpenAI(base_url=PORTKEY_BASE_URL, api_key=key, default_headers=headers, timeout=120)


def build_model() -> Any:
    """gpt-6-luna through Portkey (PORTKEY_API_KEY), or direct OpenAI (OPENAI_API_KEY) as a fallback."""
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIResponsesModel
    from pydantic_ai.providers.openai import OpenAIProvider

    portkey_key = os.getenv("PORTKEY_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    if portkey_key:
        client = _portkey_client(portkey_key)
    elif openai_key:
        client = AsyncOpenAI(api_key=openai_key, timeout=120)
    else:
        raise ConfigError("No API key found. Put PORTKEY_API_KEY in a .env file in this folder or one folder above (see .env.example).")
    return OpenAIResponsesModel(MODEL_NAME, provider=OpenAIProvider(openai_client=client))


def dollar_cost(input_tokens: int, cached_tokens: int, output_tokens: int, cache_write_tokens: int = 0) -> float:
    rates = PRICES.get(MODEL_NAME, PRICES[REQUIRED_MODEL])
    uncached = max(input_tokens - cached_tokens - cache_write_tokens, 0)
    return (
        uncached * rates["input_per_1m"]
        + cached_tokens * rates["cached_input_per_1m"]
        + cache_write_tokens * rates["cache_write_per_1m"]
        + output_tokens * rates["output_per_1m"]
    ) / 1e6
