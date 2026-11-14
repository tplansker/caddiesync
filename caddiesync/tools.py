"""Plain-code tools: the course database, the Tee-Time Sniper, the accessibility cache, and JSON logs.

None of these call an AI model. The Sniper is code on purpose: a tee time's price and status are facts,
so they are read from the tee sheet, never generated or edited by a model.

The tee sheet is SIMULATED (no public tee-time API exists for these courses). It is generated the same way
every time for a given course and date, so a demo run is repeatable and dates are always in the future.
"""

from __future__ import annotations

import hashlib
import json
import random
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .config import CACHE_DIR, DATA, OUTPUT

COURSES_FILE = DATA / "courses.json"
CACHE_FILE = CACHE_DIR / "accessibility_cache.json"
_LOCK = threading.Lock()

# Course-data fields an assessment may cite. Anything else in a finding is treated as invented.
EVIDENCE_FIELDS = (
    "access", "holes", "par", "yardage_forward", "yardage_back", "slope_forward", "terrain", "terrain_note",
    "walkability", "cart_fairway_access", "cart_near_greens", "adaptive_cart", "forced_carries",
    "water_in_play", "bunkers", "notes",
)

WINDOWS = {  # minutes after midnight, [start, end)
    "early": (6 * 60, 8 * 60), "morning": (8 * 60, 11 * 60), "midday": (11 * 60, 13 * 60),
    "afternoon": (13 * 60, 16 * 60), "twilight": (16 * 60, 19 * 60), "any": (0, 24 * 60),
}


# ---------- JSON helpers ----------

def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def append_json(path: Path, entry: dict[str, Any]) -> None:
    with _LOCK:
        items = read_json(path, [])
        items.append(entry)
        write_json(path, items)


# ---------- Course database ----------

def load_courses() -> dict[str, dict[str, Any]]:
    return {c["course_id"]: c for c in read_json(COURSES_FILE, [])}


def course_facts(course: dict[str, Any]) -> dict[str, Any]:
    """The part of a course record the Course Analyst sees. null = unknown (never guess)."""
    return {k: course.get(k) for k in ("course_id", "name", "town", *EVIDENCE_FIELDS)}


# ---------- Tee-Time Sniper (simulated tee sheet) ----------

def _rng(*parts: str) -> random.Random:
    seed = int(hashlib.sha256("|".join(parts).encode()).hexdigest()[:12], 16)
    return random.Random(seed)


def tee_sheet(course: dict[str, Any], day: date) -> list[dict[str, Any]]:
    """Every tee time for one course on one day, with price per player, open spots, deals, waitlist."""
    rng = _rng(course["course_id"], day.isoformat())
    weekend = day.weekday() >= 5
    low, high = course.get("weekend_rate_usd" if weekend else "weekday_rate_usd") or [45, 70]
    base = rng.uniform(low, high)
    interval = 9 if course.get("holes", 18) == 18 else 10
    slots = []
    t = 6 * 60 + 30 + rng.choice([0, 3, 6])
    while t <= 17 * 60:
        prime = 7 * 60 <= t < 11 * 60
        busy = (0.75 if weekend else 0.45) + (0.15 if prime else -0.1)
        booked = rng.random() < busy
        spots = 0 if booked else rng.choice([1, 2, 2, 3, 4, 4, 4])
        price = base * (0.6 if t >= 15 * 60 + 30 else 1.0)          # twilight rate
        deal = (not booked) and rng.random() < 0.12
        if deal:
            price *= 0.85
        hhmm = f"{t // 60:02d}:{t % 60:02d}"
        slots.append({
            "slot_id": f"{course['course_id']}|{day.isoformat()}|{hhmm}",
            "course_id": course["course_id"],
            "date": day.isoformat(),
            "time": hhmm,
            "minutes": t,
            "price_per_player": round(price),
            "open_spots": spots,
            "deal": deal,
            "waitlist_open": booked and prime and rng.random() < 0.5,
            "simulated": True,
        })
        t += interval
    return slots


def find_tee_times(courses: list[dict[str, Any]], day: date, window: str, players: int,
                   max_price: float | None, per_course: int = 3) -> dict[str, dict[str, list[dict[str, Any]]]]:
    """The Sniper. For each course: up to `per_course` bookable slots that fit, plus waitlist options."""
    start, end = WINDOWS.get(window, WINDOWS["any"])
    now = datetime.now()
    if day == now.date():  # no tee times that have already gone off
        start = max(start, now.hour * 60 + now.minute + 30)
    found: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for course in courses:
        fits = [s for s in tee_sheet(course, day)
                if start <= s["minutes"] < end and (max_price is None or s["price_per_player"] <= max_price)]
        open_ = [s for s in fits if s["open_spots"] >= players]
        waitlist = [s for s in fits if s["waitlist_open"]]
        if open_ or waitlist:
            found[course["course_id"]] = {"open": open_[:per_course], "waitlist": waitlist[:2]}
    return found


def slot_by_id(slot_id: str) -> dict[str, Any] | None:
    """Look up a slot from its id by regenerating that day's sheet (used to verify the Concierge's picks)."""
    try:
        course_id, day, _ = slot_id.split("|")
        course = load_courses()[course_id]
        return next((s for s in tee_sheet(course, date.fromisoformat(day)) if s["slot_id"] == slot_id), None)
    except (ValueError, KeyError):
        return None


def next_weekday(today: date, weekday: int = 5) -> date:
    """Next given weekday after today (default Saturday)."""
    return today + timedelta(days=(weekday - today.weekday() - 1) % 7 + 1)


# ---------- Accessibility cache ----------

def cache_get(course_id: str, needs_key: str) -> dict[str, Any] | None:
    return read_json(CACHE_FILE, {}).get(f"{course_id}::{needs_key}")


def cache_put(course_id: str, needs_key: str, entry: dict[str, Any]) -> None:
    with _LOCK:
        cache = read_json(CACHE_FILE, {})
        cache[f"{course_id}::{needs_key}"] = entry
        write_json(CACHE_FILE, cache)


def cache_clear() -> None:
    with _LOCK:
        write_json(CACHE_FILE, {})


# ---------- Waitlist (human-in-the-loop: only a click in the UI can add someone) ----------

def join_waitlist(slot_id: str, players: int) -> dict[str, Any]:
    slot = slot_by_id(slot_id)
    if slot is None or not slot["waitlist_open"]:
        raise ValueError("That tee time does not have an open waitlist.")
    entry = {"slot_id": slot_id, "players": players, "time": now_iso(), "simulated": True}
    append_json(OUTPUT / "waitlist.json", entry)
    position = sum(1 for e in read_json(OUTPUT / "waitlist.json", []) if e["slot_id"] == slot_id)
    return {**entry, "position": position}
