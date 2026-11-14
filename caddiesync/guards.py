"""Safety nets that run in code, before and after the AI agents.

Attacks      - clean_input + scan_for_injection before the Profiler; prices/times never come from a model.
Malfunction  - check_assessment drops findings that cite unknown or made-up course facts;
               check_picks drops any tee time the Concierge invented.
Overspending - per-agent UsageLimits (pipeline.py), a per-visitor rate limit, and a daily dollar budget.
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import defaultdict, deque
from datetime import date, datetime, timezone
from typing import Any

from pydantic_ai import UsageLimits

from .config import DAILY_BUDGET_USD, OUTPUT
from .models import AccessibilityAssessment, ConciergeReply
from .tools import EVIDENCE_FIELDS, read_json

MAX_INPUT_CHARS = 800

# One model call per agent run is the normal case; a second covers one validation retry. Anything more is a loop.
PROFILER_LIMITS = UsageLimits(request_limit=3, total_tokens_limit=12_000)
ANALYST_LIMITS = UsageLimits(request_limit=3, total_tokens_limit=12_000)
CONCIERGE_LIMITS = UsageLimits(request_limit=3, total_tokens_limit=16_000)
MAX_FRESH_ASSESSMENTS = 16  # cap on uncached Course Analyst calls in a single search

INJECTION_PATTERNS = [
    (r"\b(ignore|disregard|forget|override)\b.{0,40}\b(previous|prior|above|earlier|all|your)\b.{0,20}\b(instructions?|rules|prompts?|directions)", "tries to override the AI's instructions"),
    (r"\b(system|developer)\s*(prompt|message|mode|instructions?)\b", "mentions the AI's system prompt"),
    (r"\byou are now\b|\bact as\b|\bpretend (to be|you are)\b|\bjailbreak\b|\bDAN\b", "tries to change the AI's role"),
    (r"\bI\s*(am|'m)\s*(the\s*)?(course\s*)?(owner|admin(istrator)?|manager|staff|pro shop|developer)\b", "claims to be staff or the owner"),
    (r"\b(bypass|skip|jump|cut|move me to the (front|top))\b.{0,30}\b(wait\s*list|waitlist|line|queue)\b|\b(wait\s*list|waitlist)\b.{0,30}\b(bypass|skip|jump)\b", "asks to skip the waitlist"),
    (r"\b(set(ting)?|chang(e|ing)|mak(e|ing)|overrid(e|ing)|lower(ing)?|drop(ping)?)\b.{0,30}\b(price|rate|fee|cost)s?\b.{0,30}(\$\s*0\b|\bfree\b|\bzero\b|\bto\s*\$?\d)", "asks to change a price"),
    (r"\bfor free\b|\bcomp(ed|limentary)? (round|tee time)\b", "asks for a free round"),
    (r"</?\s*(system|assistant|instructions?|golfer_request)\s*>", "contains fake control tags"),
]


def clean_input(text: str) -> str:
    """Strip invisible/control characters (a common way to hide instructions) and cap the length."""
    text = unicodedata.normalize("NFKC", text or "")
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch)[0] != "C")
    return re.sub(r"[ \t]+", " ", text).strip()[:MAX_INPUT_CHARS]


def scan_for_injection(text: str) -> list[str]:
    """Return a plain-English reason for every attack pattern found. Empty list = looks clean."""
    return sorted({why for pattern, why in INJECTION_PATTERNS if re.search(pattern, text, re.I | re.S)})


def nice_date(d: date) -> str:
    """'Saturday, November 21' on every OS (strftime's %-d does not work on Windows)."""
    return f"{d:%A, %B} {d.day}"


def nice_time(hhmm: str) -> str:
    """'08:15' -> '8:15 AM' on every OS."""
    h, m = map(int, hhmm.split(":"))
    return f"{(h - 1) % 12 + 1}:{m:02d} {'AM' if h < 12 else 'PM'}"


def resolve_date(value: str | None, today: date, fallback: date) -> tuple[date, str | None]:
    """Use the Profiler's date only if it is real and within the next 30 days."""
    try:
        day = date.fromisoformat(value) if value else None
    except ValueError:
        day = None
    if day is None:
        return fallback, f"No day given, so CaddieSync searched {nice_date(fallback)}."
    if not 0 <= (day - today).days <= 30:
        return fallback, f"Tee sheets only go 30 days out, so CaddieSync searched {nice_date(fallback)} instead."
    return day, None


def _critical_unknowns(needs: Any, facts: dict[str, Any]) -> list[tuple[str, int, str]]:
    """(field, max score, plain words) for facts this group depends on that the course data does not have."""
    out = []
    if needs.terrain != "any" and facts.get("terrain") is None:
        out.append(("terrain", 70, "how hilly the course is"))
    if needs.walking_ability != "full" and facts.get("cart_fairway_access") is None and facts.get("walkability") is None:
        out.append(("cart_fairway_access", 75, "whether carts can drive to the ball"))
    if needs.cart_near_greens and facts.get("cart_near_greens") is None:
        out.append(("cart_near_greens", 70, "how close carts can get to greens"))
    if needs.adaptive_cart and facts.get("adaptive_cart") is None:
        out.append(("adaptive_cart", 60, "whether an adaptive cart is available or allowed"))
    return out


def check_assessment(a: AccessibilityAssessment, facts: dict[str, Any], needs: Any = None) -> tuple[AccessibilityAssessment, list[str]]:
    """Drop findings that cite a field that does not exist or is unknown (null) for this course.
    Unknown facts move to verify_with_course instead of being presented as true."""
    notes, kept = [], []
    for f in a.findings:
        if f.field not in EVIDENCE_FIELDS:
            notes.append(f"dropped a finding citing a field that does not exist ('{f.field}')")
        elif facts.get(f.field) is None:
            notes.append(f"dropped a finding about '{f.field}', which is unknown for this course")
            if f.field not in a.verify_with_course and len(a.verify_with_course) < 4:
                a.verify_with_course.append(f.field.replace("_", " "))
        else:
            kept.append(f)
    if not kept:
        notes.append("no finding was backed by course data, so the score was capped at 40")
        a.score = min(a.score, 40)
    a.findings = kept
    # Missing data must not look like good news: cap the score when a fact this group depends on is unknown.
    if needs is not None:
        for field, cap, words in _critical_unknowns(needs, facts):
            if a.score > cap:
                notes.append(f"score capped at {cap} (was {a.score}) because {words} is unknown")
                a.score = cap
            if words not in a.verify_with_course and len(a.verify_with_course) < 4:
                a.verify_with_course.insert(0, words)
    a.verify_with_course = [v for v in a.verify_with_course if v.replace(" ", "_") not in EVIDENCE_FIELDS][:4]
    # Keep verdict consistent with score so a model slip can't label a 30 as a great fit.
    expected = "great_fit" if a.score >= 75 else "workable" if a.score >= 50 else "poor_fit"
    if a.verdict != expected:
        notes.append(f"verdict changed from {a.verdict} to {expected} to match the score {a.score}")
        a.verdict = expected
    return a, notes


def check_picks(reply: ConciergeReply, candidate_ids: set[str]) -> tuple[ConciergeReply, list[str]]:
    """Keep only picks whose slot_id came from the Sniper. A model cannot add a tee time that does not exist."""
    notes, kept, seen = [], [], set()
    for p in reply.picks:
        if p.slot_id not in candidate_ids:
            notes.append(f"dropped a pick with an unknown tee time id '{p.slot_id[:60]}'")
        elif p.slot_id not in seen:
            seen.add(p.slot_id)
            kept.append(p)
    reply.picks = kept
    return reply, notes


# ---------- Overspending ----------

_visits: dict[str, deque[float]] = defaultdict(deque)
RATE_LIMIT = (8, 600)  # at most 8 searches per visitor per 10 minutes


def rate_limited(visitor: str) -> bool:
    now, (limit, window) = time.time(), RATE_LIMIT
    q = _visits[visitor]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        return True
    q.append(now)
    return False


def spent_today_usd() -> float:
    today = datetime.now(timezone.utc).date().isoformat()  # usage_log times are UTC
    return sum(r.get("cost_usd", 0) for r in read_json(OUTPUT / "usage_log.json", []) if r.get("time", "").startswith(today))


def over_budget() -> bool:
    return spent_today_usd() >= DAILY_BUDGET_USD
