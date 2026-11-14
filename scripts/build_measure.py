"""Stage 3 of build_courses.py: measure every course and write data/courses.json and data/places.json.

What is measured, per hole (hole = the OpenStreetMap line drawn from tee to green):
  - climb and drop along the hole, from USGS elevation at 6 points (tee, 20%, 40%, 60%, 80%, green)
  - steepest grade between those points. "Steep" means steeper than 8.33%, the ADA limit for a wheelchair ramp
  - water the ball must fly over: where the tee-to-green line crosses a pond/water hazard (lateral hazards ignored)
  - bunkers within 25 m of the green
  - walk from this green to the next tee
  - distance from the green to the nearest mapped cart path (a proxy only; cart RULES need a phone call)

Course-level labels (terrain, walkability, forced_carries, water_in_play, bunkers) are derived from those numbers with
fixed thresholds documented below, so every label can be traced to a measurement.
"""

from __future__ import annotations

import json
import math
import re
import statistics
from collections import defaultdict
from pathlib import Path

from build_courses import RAW, ROOT, STATES, assign_features, hole_samples, load_state, project

ADA_RAMP = 0.0833
M_TO_YD = 1.09361
M_TO_FT = 3.28084


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:50]


def to_m(shape, proj):
    from shapely.ops import transform
    return transform(lambda x, y, z=None: proj(x, y), shape)


def hole_ref(f) -> int | None:
    m = re.match(r"\d+", f["tags"].get("ref", ""))
    return int(m.group()) if m else None


def classify_terrain(steep_share: float, climb_per_18_ft: float) -> str:
    """Terrain from total uphill feet per 18 holes (what a knee feels over a round), bumped up one level when
    half or more of the holes also have a stretch steeper than the ADA ramp limit (8.33%)."""
    levels = ["flat", "gently_rolling", "hilly", "severe"]
    i = 0 if climb_per_18_ft < 150 else 1 if climb_per_18_ft < 350 else 2 if climb_per_18_ft < 650 else 3
    if i == 0 and steep_share >= 0.15:
        i = 1
    if steep_share >= 0.5 and climb_per_18_ft >= 450:
        i = min(i + 1, 3)
    return levels[i]


def measure(course: dict, elev: dict[str, float]) -> dict:
    shape = course["shape"]
    c = shape.centroid
    proj = project(c.y, c.x)
    F = course["features"]
    holes = [f for f in F.get("hole", []) if f["shape"].geom_type == "LineString"]
    greens = [to_m(f["shape"], proj) for f in F.get("green", []) if f["shape"].geom_type in ("Polygon", "MultiPolygon")]
    bunkers = [to_m(f["shape"], proj) for f in F.get("bunker", [])]
    water = [to_m(f["shape"], proj) for f in F.get("water_hazard", []) + F.get("water", [])
             if f["shape"].geom_type in ("Polygon", "MultiPolygon")]
    all_water = water + [to_m(f["shape"], proj) for f in F.get("lateral_water_hazard", [])]
    paths = [to_m(f["shape"], proj) for f in F.get("cartpath", [])]
    out: dict = {"holes_mapped": len(holes), "greens_mapped": len(greens), "bunkers_mapped": len(bunkers),
                 "water_mapped": len(all_water), "cartpaths_mapped": len(paths)}
    if not holes:
        return out

    from shapely.ops import unary_union
    water_u = unary_union(water) if water else None
    all_water_u = unary_union(all_water) if all_water else None
    paths_u = unary_union(paths) if paths else None

    per_hole = []
    for f in holes:
        line_ll = f["shape"]
        line = to_m(line_ll, proj)
        pts = hole_samples(line_ll)
        z = [elev.get(f"{lat},{lon}") for lon, lat in pts]
        h = {"ref": hole_ref(f), "par": f["tags"].get("par"), "length_yd": round(line.length * M_TO_YD)}
        if all(v is not None for v in z):
            seg = line.length / (len(z) - 1)
            diffs = [b - a for a, b in zip(z, z[1:])]
            h["climb_m"] = sum(d for d in diffs if d > 0)
            h["drop_m"] = -sum(d for d in diffs if d < 0)
            # Grade over stretches of at least 40 m, so 1-2 m of elevation-data noise on a short par 3
            # doesn't read as a steep slope.
            k = max(1, math.ceil(40 / seg)) if seg > 1 else len(z) - 1
            k = min(k, len(z) - 1)
            h["max_grade"] = max(abs(z[i + k] - z[i]) / (seg * k) for i in range(len(z) - k)) if seg > 1 else 0
            h["z_tee"], h["z_green"] = z[0], z[-1]
        if water_u is not None:
            cross = line.intersection(water_u)
            parts = getattr(cross, "geoms", [cross]) if not cross.is_empty else []
            carry = max((p.length for p in parts), default=0)
            h["water_carry_yd"] = round(carry * M_TO_YD) if carry > 10 else 0
        if all_water_u is not None:
            h["water_near"] = line.distance(all_water_u) < 25
        end = line.interpolate(1, normalized=True)
        green = min(greens, key=lambda g: g.distance(end)) if greens else None
        if green is not None and green.distance(end) < 40:
            h["bunkers_near_green"] = sum(1 for b in bunkers if b.distance(green) < 25)
            if paths_u is not None:
                h["cartpath_to_green_m"] = green.distance(paths_u)
        h["_tee"], h["_green"] = line.interpolate(0, normalized=True), end
        per_hole.append(h)

    # Walking between holes: green of hole n to the nearest tee of hole n+1
    by_ref = defaultdict(list)
    for h in per_hole:
        if h["ref"]:
            by_ref[h["ref"]].append(h)
    transfers, transfer_climb = [], []
    for ref, hs in by_ref.items():
        for h in hs:
            nxt = by_ref.get(ref + 1)
            if nxt:
                n = min(nxt, key=lambda x: x["_tee"].distance(h["_green"]))
                d = n["_tee"].distance(h["_green"])
                if d < 1500:
                    transfers.append(d)
                    if "z_green" in h and "z_tee" in n:
                        transfer_climb.append(max(n["z_tee"] - h["z_green"], 0))

    with_z = [h for h in per_hole if "climb_m" in h]
    if with_z:
        steep = [h for h in with_z if h["max_grade"] > ADA_RAMP]
        changes = [(h["climb_m"] + h["drop_m"]) / 2 for h in with_z]
        worst = max(with_z, key=lambda h: h["climb_m"])
        steepest = max(with_z, key=lambda h: h["max_grade"])
        total_climb = sum(h["climb_m"] for h in with_z) + sum(transfer_climb)
        zs = [v for h in with_z for v in (h["z_tee"], h["z_green"])]
        out.update({
            "terrain": classify_terrain(len(steep) / len(with_z), total_climb * M_TO_FT * 18 / len(with_z)),
            "holes_steeper_than_ramp": len(steep),
            "total_climb_ft": round(total_climb * M_TO_FT),
            "avg_climb_per_hole_ft": round(statistics.mean(h["climb_m"] for h in with_z) * M_TO_FT),
            "biggest_hole_climb_ft": round(worst["climb_m"] * M_TO_FT),
            "biggest_climb_hole": worst["ref"],
            "steepest_grade_pct": round(steepest["max_grade"] * 100, 1),
            "steepest_hole": steepest["ref"],
            "elevation_range_ft": round((max(zs) - min(zs)) * M_TO_FT),
        })
    if transfers:
        out["avg_walk_between_holes_yd"] = round(statistics.mean(transfers) * M_TO_YD)
        out["longest_walk_between_holes_yd"] = round(max(transfers) * M_TO_YD)
    out["mapped_length_yd"] = sum(h["length_yd"] for h in per_hole)
    pars = [int(h["par"]) for h in per_hole if str(h.get("par", "")).isdigit()]
    if len(pars) == len(per_hole) and len(per_hole) in (9, 18):
        out["par"] = sum(pars)

    enough_map = len(per_hole) >= 9
    # "No water" is only trusted on courses mapped in detail (bunkers too); otherwise unmapped ponds could hide.
    detailed = len(greens) >= 9 and len(bunkers) >= 9
    if enough_map and (water_u is not None or detailed):
        carries = [h.get("water_carry_yd", 0) for h in per_hole]
        n = sum(1 for v in carries if v > 0)
        longest = max(carries, default=0)
        scale = 18 / len(per_hole)
        n18 = n * scale
        out["water_carry_holes"] = n
        out["longest_water_carry_yd"] = longest
        # Length matters more than count: several 30-yard carries are easier than one 150-yard carry.
        out["forced_carries"] = ("none" if n == 0
                                 else "significant" if longest > 170 or (n18 >= 6 and longest >= 100)
                                 else "several" if longest >= 100 or (n18 >= 3 and longest >= 60)
                                 else "few_short")
        near = sum(1 for h in per_hole if h.get("water_near")) * scale
        out["water_in_play"] = "little" if near <= 2 else "some" if near <= 6 else "lots"
    bn = [h["bunkers_near_green"] for h in per_hole if "bunkers_near_green" in h]
    if len(bn) >= 9 and out["bunkers_mapped"] > 0:
        avg = statistics.mean(bn)
        out["bunkers_near_green_avg"] = round(avg, 1)
        out["bunkers"] = "few" if avg < 1 else "moderate" if avg <= 2.5 else "many"
    cp = [h["cartpath_to_green_m"] for h in per_hole if "cartpath_to_green_m" in h]
    if len(cp) >= 9 and len(paths) >= 3:
        out["cartpath_to_green_yd"] = round(statistics.median(cp) * M_TO_YD)
    if "terrain" in out:
        hard = out["terrain"] in ("hilly", "severe") or out.get("avg_walk_between_holes_yd", 0) > 250
        easy = out["terrain"] in ("flat", "gently_rolling") and out.get("avg_walk_between_holes_yd", 999) < 130
        out["walkability"] = "hard" if hard else "easy" if easy else "moderate"
    return out


def access_of(tags: dict) -> str:
    a = (tags.get("access") or "").lower()
    text = " ".join(tags.get(k, "") for k in ("name", "operator", "operator:type", "description", "club")).lower()
    if a in ("private", "members", "no") or "private" in text:
        return "private"
    if "semi-private" in text or "semi private" in text:
        return "semi_private"
    if any(w in text for w in ("municipal", "town of", "city of", "state park", "public")) or tags.get("operator:type") in ("public", "government"):
        return "public"
    if a in ("yes", "public", "permissive") or tags.get("fee") == "yes":
        return "public"
    return "unknown"


def nearest_place(lat: float, lon: float, places: list[dict], state: str) -> str | None:
    best, bd = None, 1e9
    for p in places:
        if p["state"] != state or p["kind"] not in ("city", "town", "village"):
            continue
        d = (p["lat"] - lat) ** 2 + ((p["lon"] - lon) * math.cos(math.radians(lat))) ** 2
        if d < bd:
            best, bd = p["name"], d
    return best


def build() -> None:
    elev = json.loads((RAW / "elevation.json").read_text()) if (RAW / "elevation.json").exists() else {}
    places = json.loads((RAW / "places.json").read_text()) if (RAW / "places.json").exists() else []
    featured = {f["osm_name"].lower(): f for f in json.loads((ROOT / "data" / "featured.json").read_text())}
    records, seen = [], {}
    for code in STATES:
        if not (RAW / f"osm_{code}.json").exists():
            print(f"{code}: not downloaded yet; skipped")
            continue
        courses, feats = load_state(code)
        assign_features(courses, feats)
        for c in courses:
            tags = c["tags"]
            pt = c["shape"].representative_point()
            m = measure(c, elev) if c["shape"].geom_type != "Point" else {"holes_mapped": 0}
            name = tags["name"].strip()
            rec = {
                "course_id": f"{slug(name)}_{code.lower()}", "name": name,
                "town": tags.get("addr:city") or nearest_place(pt.y, pt.x, places, code), "state": code,
                "lat": round(pt.y, 5), "lon": round(pt.x, 5), "access": access_of(tags),
                "holes": int(tags["holes"]) if str(tags.get("holes", "")).isdigit() else (m["holes_mapped"] or None),
                "par": m.pop("par", None) or (int(tags["par"]) if str(tags.get("par", "")).isdigit() else None),
                "map_detail": "full" if m["holes_mapped"] >= 9 else "partial" if m["holes_mapped"] else "outline",
                "terrain": None, "terrain_note": None, "walkability": None,
                "cart_fairway_access": None, "cart_near_greens": None, "adaptive_cart": None,
                "forced_carries": None, "water_in_play": None, "bunkers": None,
                **m,
                "website": tags.get("website") or tags.get("contact:website") or tags.get("url"),
                "phone": tags.get("phone") or tags.get("contact:phone"),
                "booking_url": None, "price_tier": None, "price_note": None, "notes": None,
                "verified_by_phone": False,
                "sources": [f"https://www.openstreetmap.org/{c['osm']}"],
            }
            if rec["holes"] and rec["holes"] > 18 and m["holes_mapped"] > 18:
                rec["notes"] = f"Facility with {m['holes_mapped']} mapped holes (more than one course); numbers cover all of them."
            f = featured.get(name.lower())
            if f:  # hand-researched facts (sources, phone, price tier, review notes) override map gaps
                for k, v in f.items():
                    if k in ("osm_name",) or v in (None, "", []):
                        continue
                    if k == "sources":
                        rec["sources"] = rec["sources"] + [s for s in v if s not in rec["sources"]]
                    elif k == "notes" and rec["notes"]:
                        rec["notes"] = f"{v} {rec['notes']}"
                    else:
                        rec[k] = v
            key = (slug(name), code)
            if key in seen:  # duplicate outlines: keep the one with more mapped detail
                prev = seen[key]
                if rec["holes_mapped"] <= prev["holes_mapped"]:
                    continue
                records.remove(prev)
            seen[key] = rec
            records.append(rec)
    records.sort(key=lambda r: (r["state"], r["name"]))
    (ROOT / "data" / "courses.json").write_text(json.dumps(records, indent=0, ensure_ascii=False))
    # Places for the Course Locator: towns + a few well-known trip regions
    keep = [p for p in places if p["kind"] in ("city", "town", "village", "island")]
    if keep:
        (ROOT / "data" / "places.json").write_text(json.dumps(keep, separators=(",", ":"), ensure_ascii=False))
    by_state = defaultdict(lambda: defaultdict(int))
    for r in records:
        by_state[r["state"]]["courses"] += 1
        by_state[r["state"]]["full"] += r["map_detail"] == "full"
        by_state[r["state"]]["terrain"] += r["terrain"] is not None
        by_state[r["state"]][r["access"]] += 1
    print(json.dumps(by_state, indent=1))
    print(f"{len(records)} courses, {len(keep)} places")
