"""Plain-code tools: the course database, the accessibility cache, and JSON logs. None of these call an AI model.

data/courses.json is built offline by scripts/build_courses.py from OpenStreetMap (course maps, hole by hole) and
USGS elevation data, plus hand-researched facts for featured courses (data/featured.json). It covers New England.
"""

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from .config import CACHE_DIR, DATA

COURSES_FILE = DATA / "courses.json"
CACHE_FILE = CACHE_DIR / "accessibility_cache.json"
_LOCK = threading.Lock()

# Course-data fields an assessment may cite. Anything else in a finding is treated as invented.
EVIDENCE_FIELDS = (
    "access", "holes", "par", "map_detail", "mapped_length_yd",
    # measured from the map + USGS elevation
    "terrain", "holes_steeper_than_ramp", "total_climb_ft", "avg_climb_per_hole_ft", "biggest_hole_climb_ft",
    "biggest_climb_hole", "steepest_grade_pct", "steepest_hole", "elevation_range_ft", "walkability",
    "avg_walk_between_holes_yd", "longest_walk_between_holes_yd", "forced_carries", "water_carry_holes",
    "longest_water_carry_yd", "water_in_play", "bunkers", "bunkers_near_green_avg", "cartpath_to_green_yd",
    # measured from mapped parking lots and clubhouses (straight-line walks)
    "walk_lot_to_first_tee_yd", "walk_9th_green_to_clubhouse_yd", "walk_back_to",
    # ADA access: mapped tags (rare) or hand-researched with sources
    "accessible_parking_mapped", "clubhouse_wheelchair_mapped", "restrooms_mapped",
    "accessible_parking", "clubhouse_step_free", "accessible_restrooms",
    # adaptive equipment and programs (hand-researched, data/adaptive.json)
    "adaptive_equipment", "adaptive_note", "adaptive_programs",
    # policies: only known when researched or confirmed by phone
    "cart_fairway_access", "cart_near_greens", "adaptive_cart",
    # hand-researched text
    "terrain_note", "notes",
)


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

@lru_cache(maxsize=1)
def _load(mtime: float) -> dict[str, dict[str, Any]]:
    return {c["course_id"]: c for c in read_json(COURSES_FILE, [])}


def load_courses() -> dict[str, dict[str, Any]]:
    """All courses, keyed by course_id. Re-read only when the file changes."""
    try:
        return _load(COURSES_FILE.stat().st_mtime)
    except FileNotFoundError:
        return {}


def course_facts(course: dict[str, Any]) -> dict[str, Any]:
    """The part of a course record the Course Analyst sees. null = unknown (never guess)."""
    return {k: course.get(k) for k in ("course_id", "name", "town", "state", *EVIDENCE_FIELDS)}


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
