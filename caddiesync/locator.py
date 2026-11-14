"""Course Locator (plain code, no AI): where to look, which courses qualify, and which ones deserve the AI's time.

1. resolve(): turn "Cape Cod", "Portland, Maine", "Stowe VT" or a saved home town into a map point.
2. find():    courses within the radius that match holes / price tier / public access (or one named course).
3. pre_score(): a quick, rule-based accessibility score from the measured facts, so only the most promising
   ~10 courses go to the Course Analyst. This is why the cost of a search stays flat even with ~900 courses.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from typing import Any

from .config import DATA
from .tools import read_json

STATE_CODES = {"CT": "connecticut", "RI": "rhode island", "MA": "massachusetts", "VT": "vermont", "NH": "new hampshire", "ME": "maine"}
STATE_WORDS = {v: k for k, v in STATE_CODES.items()} | {k.lower(): k for k in STATE_CODES} | {"mass": "MA", "conn": "CT"}
TIERS = ["$", "$$", "$$$", "$$$$"]

# Trip regions people say that are not a single town: center point and a sensible default radius (miles).
REGIONS = {
    "cape cod": (41.68, -70.20, 35, "MA"), "the cape": (41.68, -70.20, 35, "MA"),
    "martha's vineyard": (41.40, -70.62, 15, "MA"), "marthas vineyard": (41.40, -70.62, 15, "MA"),
    "nantucket": (41.28, -70.10, 10, "MA"), "the berkshires": (42.35, -73.25, 30, "MA"), "berkshires": (42.35, -73.25, 30, "MA"),
    "boston": (42.36, -71.06, 25, "MA"), "greater boston": (42.36, -71.06, 30, "MA"), "north shore": (42.60, -70.90, 20, "MA"),
    "south shore": (42.10, -70.85, 20, "MA"), "pioneer valley": (42.20, -72.60, 25, "MA"),
    "white mountains": (44.20, -71.45, 35, "NH"), "lakes region": (43.60, -71.40, 30, "NH"), "seacoast": (43.05, -70.85, 25, "NH"),
    "southern maine": (43.50, -70.50, 35, "ME"), "midcoast maine": (44.10, -69.10, 35, "ME"), "mid-coast maine": (44.10, -69.10, 35, "ME"),
    "bar harbor": (44.39, -68.20, 25, "ME"), "acadia": (44.35, -68.25, 25, "ME"),
    "green mountains": (43.80, -72.90, 40, "VT"), "lake champlain": (44.50, -73.25, 30, "VT"), "southern vermont": (43.00, -72.85, 35, "VT"),
    "newport": (41.49, -71.31, 15, "RI"), "block island": (41.17, -71.58, 6, "RI"),
    "connecticut shoreline": (41.30, -72.50, 30, "CT"), "the shoreline": (41.30, -72.50, 30, "CT"), "litchfield hills": (41.75, -73.20, 25, "CT"),
    "mystic": (41.35, -71.97, 20, "CT"), "new england": (43.30, -71.60, 400, None),
}
KIND_RANK = {"city": 0, "town": 1, "village": 2, "island": 3}


@lru_cache(maxsize=1)
def _places() -> list[dict[str, Any]]:
    return read_json(DATA / "places.json", [])


def miles(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p = math.pi / 180
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 7917.6 * math.asin(math.sqrt(a))


def resolve(text: str | None) -> dict[str, Any] | None:
    """Map point for a place name. Returns {lat, lon, label, state, radius (region default or None), note}."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.strip().lower().replace(".", ""))
    t = re.sub(r"^(near|around|in|the area around|close to)\s+", "", t)
    if t in REGIONS:
        lat, lon, r, st = REGIONS[t]
        return {"lat": lat, "lon": lon, "label": text.strip().title(), "state": st, "radius": r, "note": None}
    state = None
    m = re.match(r"^(.*?)[,\s]+(ct|ri|ma|vt|nh|me|connecticut|rhode island|massachusetts|vermont|new hampshire|maine|mass|conn)$", t)
    if m:
        t, state = m.group(1).strip(), STATE_WORDS[m.group(2)]
    if t in STATE_WORDS and not state:  # a whole state
        code = STATE_WORDS[t]
        pts = [p for p in _places() if p["state"] == code]
        if pts:
            return {"lat": sum(p["lat"] for p in pts) / len(pts), "lon": sum(p["lon"] for p in pts) / len(pts),
                    "label": STATE_CODES[code].title(), "state": code, "radius": 120, "note": None}
    hits = [p for p in _places() if p["name"].lower() == t and (state is None or p["state"] == state)]
    if not hits:
        hits = [p for p in _places() if p["name"].lower().startswith(t) and (state is None or p["state"] == state)]
    if not hits:
        return None
    hits.sort(key=lambda p: (KIND_RANK.get(p["kind"], 9), p["state"] != "CT"))
    best = hits[0]
    # Only mention other states' matches when they're comparable (a city vs. a small town is not ambiguous).
    others = sorted({p["state"] for p in hits if p["state"] != best["state"]
                     and KIND_RANK.get(p["kind"], 9) <= KIND_RANK.get(best["kind"], 9)})
    note = (f"There's also a {best['name']} in {', '.join(others)}. Add the state (e.g. \"{best['name']}, {others[0]}\") "
            f"to search there instead.") if others and state is None else None
    return {"lat": best["lat"], "lon": best["lon"], "label": f"{best['name']}, {best['state']}", "state": best["state"],
            "radius": None, "note": note}


def find_named(courses: dict[str, dict[str, Any]], name: str) -> list[dict[str, Any]]:
    words = [w for w in re.findall(r"[a-z0-9']+", name.lower()) if w not in {"golf", "club", "course", "country", "cc", "the", "gc"}]
    if not words:
        return []
    return [c for c in courses.values() if all(w in c["name"].lower() for w in words)]


def tier_ok(course: dict[str, Any], max_tier: str | None) -> bool:
    return max_tier is None or course.get("price_tier") is None or TIERS.index(course["price_tier"]) <= TIERS.index(max_tier)


ORDER = {"terrain": ["flat", "gently_rolling", "hilly", "severe"], "forced_carries": ["none", "few_short", "several", "significant"],
         "bunkers": ["few", "moderate", "many"]}


def pre_score(c: dict[str, Any], needs: Any) -> float:
    """Rule-based estimate (0-100) used only to choose which courses the AI analyzes. Not shown as the final score."""
    s = 100.0
    t = c.get("terrain")
    if needs.terrain != "any":
        s -= 12 if t is None else {"flat_only": [0, 15, 40, 60], "avoid_steep": [0, 5, 25, 45]}[needs.terrain][ORDER["terrain"].index(t)]
    if needs.walking_ability != "full":
        w = c.get("walkability")
        s -= 8 if w is None else {"easy": 0, "moderate": 5, "hard": 15}[w]
    fc = c.get("forced_carries")
    if needs.forced_carries != "any":
        s -= 8 if fc is None else {"avoid_all": [0, 15, 30, 45], "avoid_long": [0, 0, 15, 30]}[needs.forced_carries][ORDER["forced_carries"].index(fc)]
    if needs.bunkers == "avoid_deep" and c.get("bunkers"):
        s -= [0, 8, 18][ORDER["bunkers"].index(c["bunkers"])]
    if needs.shorter_course and (c.get("holes") == 9 or (c.get("mapped_length_yd") or 9999) < 5800):
        s += 5
    if needs.adaptive_cart and c.get("adaptive_cart") is False:
        s -= 50
    if c.get("map_detail") != "full":
        s -= 15  # little measured data: still possible, but less certain
    if c.get("access") == "unknown":
        s -= 3   # known-public courses win ties; unknown ones may be private clubs
    return max(0.0, min(100.0, s))


def find(courses: dict[str, dict[str, Any]], center: dict[str, Any], radius_mi: float, holes: str,
         max_tier: str | None, include_private: bool) -> list[dict[str, Any]]:
    out = []
    for c in courses.values():
        if not include_private and c.get("access") == "private":
            continue
        if holes != "either" and c.get("holes") and str(c["holes"]) != holes and not (holes == "18" and c["holes"] > 18):
            continue
        if not tier_ok(c, max_tier):
            continue
        d = miles(center["lat"], center["lon"], c["lat"], c["lon"])
        if d <= radius_mi:
            out.append({**c, "distance_mi": round(d, 1)})
    return out
