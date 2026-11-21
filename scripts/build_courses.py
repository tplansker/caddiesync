"""Build CaddieSync's course database for all of New England from public map and elevation data.

    python scripts/build_courses.py fetch       # 1. OpenStreetMap: courses, holes, greens, bunkers, water, cart paths, towns
    python scripts/build_courses.py elevation   # 2. USGS 3DEP elevation (via OpenTopoData) for points along every hole
    python scripts/build_courses.py amenities   # 2b. OpenStreetMap: driving ranges, golf shops, simulators, food & drink
    python scripts/build_courses.py facilities  # 2c. OpenStreetMap: parking lots, clubhouses, restrooms (+ accessibility tags)
    python scripts/build_courses.py build       # 3. measure each course and write data/courses.json + data/places.json

Raw downloads are cached in data/raw/ (not committed), so each stage can be re-run without downloading again.
Only the build outputs are committed; the app never calls these services at runtime.

Build-only dependencies (not needed to run the app): see scripts/requirements-build.txt.
Data credits: (c) OpenStreetMap contributors (ODbL); elevation: USGS 3D Elevation Program (public domain).
"""

from __future__ import annotations

import json
import math
import re
import ssl
import statistics
import sys
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from pathlib import Path

import certifi

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
STATES = {"CT": "Connecticut", "RI": "Rhode Island", "MA": "Massachusetts", "VT": "Vermont", "NH": "New Hampshire", "ME": "Maine"}
UA = {"User-Agent": "CaddieSync (MGT 409 class project; course accessibility research)"}
CTX = ssl.create_default_context(cafile=certifi.where())
OVERPASS = ["https://overpass-api.de/api/interpreter", "https://overpass.kumi.systems/api/interpreter"]


def http(url: str, data: dict | None = None, timeout: int = 300) -> bytes:
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return r.read()


def overpass(query: str, attempts: int = 6) -> dict:
    for attempt in range(attempts):
        url = OVERPASS[attempt % len(OVERPASS)]
        try:
            return json.loads(http(url, {"data": query}, timeout=400))
        except Exception as e:  # busy servers answer 429/504; wait and try the other mirror
            print(f"    overpass {url.split('/')[2]} failed ({type(e).__name__}: {str(e)[:60]}); retrying", flush=True)
            time.sleep(20 + 20 * attempt)
    raise RuntimeError("Overpass kept failing")


# ---------------------------------------------------------------------------------------------- 1. fetch

def fetch() -> None:
    RAW.mkdir(parents=True, exist_ok=True)
    area = '[out:json][timeout:{t}];\narea["name"="{name}"]["admin_level"="4"]["boundary"="administrative"]->.s;\n'
    parts = {
        "courses": 'nwr["leisure"="golf_course"](area.s);\nout geom;',
        "golf": '(way["golf"~"^(hole|green|bunker|water_hazard|lateral_water_hazard|cartpath|tee)$"](area.s);\n'
                'relation["golf"~"^(green|bunker|water_hazard|lateral_water_hazard)$"](area.s););\nout geom;',
        "water": 'way["leisure"="golf_course"](area.s)->.c;\n.c map_to_area->.ca;\n'
                 'way["natural"="water"](area.ca);\nout geom;',
    }
    for code, name in STATES.items():
        out = RAW / f"osm_{code}.json"
        if out.exists():
            print(f"{code}: cached"); continue
        elements = []
        for part, body in parts.items():
            pf = RAW / f"osm_{code}_{part}.json"
            if not pf.exists():
                print(f"{code}: downloading {part} for {name}...", flush=True)
                try:
                    timeout = 170 if part == "water" else 300
                    pf.write_text(json.dumps(overpass(area.format(t=timeout, name=name) + body, attempts=2 if part == "water" else 6)))
                except RuntimeError:
                    if part != "water":
                        raise
                    print(f"{code}: water query kept failing; continuing with golf water hazards only", flush=True)
                    pf.write_text(json.dumps({"elements": []}))
                time.sleep(12)
            elements += json.loads(pf.read_text())["elements"]
        seen, uniq = set(), []
        for e in elements:
            k = (e["type"], e["id"])
            if k not in seen:
                seen.add(k); uniq.append(e)
        out.write_text(json.dumps({"elements": uniq}))
        print(f"{code}: {len(uniq)} map elements", flush=True)
    out = RAW / "places.json"
    if not out.exists():
        print("Downloading town and city names...", flush=True)
        places = []
        for code, name in STATES.items():
            q = f"""[out:json][timeout:200];
area["name"="{name}"]["admin_level"="4"]["boundary"="administrative"]->.s;
(node["place"~"^(city|town|village|suburb|island|hamlet)$"]["name"](area.s););
out;"""
            for e in overpass(q)["elements"]:
                places.append({"name": e["tags"]["name"], "state": code, "kind": e["tags"]["place"],
                               "lat": round(e["lat"], 5), "lon": round(e["lon"], 5)})
            time.sleep(10)
        out.write_text(json.dumps(places))
        print(f"{len(places)} places")


# ---------------------------------------------------------------------------------------------- geometry helpers

def project(lat0: float, lon0: float):
    kx = math.cos(math.radians(lat0)) * 111_320.0
    ky = 110_540.0
    return lambda lon, lat: ((lon - lon0) * kx, (lat - lat0) * ky)


def coords(geom: list[dict]) -> list[tuple[float, float]]:
    return [(p["lon"], p["lat"]) for p in geom if p]


def element_shapes(e: dict):
    """Shapely geometries (lon/lat) for one Overpass element: polygon(s) for areas, a line for open ways."""
    from shapely.geometry import LineString, Point, Polygon
    from shapely.ops import polygonize, unary_union
    if e["type"] == "node":
        return Point(e["lon"], e["lat"])
    if e["type"] == "way":
        c = coords(e.get("geometry", []))
        if len(c) < 2:
            return None
        if len(c) >= 4 and c[0] == c[-1]:
            p = Polygon(c)
            return p if p.is_valid else p.buffer(0)
        return LineString(c)
    if e["type"] == "relation":
        lines = [LineString(coords(m["geometry"])) for m in e.get("members", [])
                 if m.get("role") in ("outer", "") and m.get("geometry") and len(m["geometry"]) >= 2]
        polys = list(polygonize(unary_union(lines))) if lines else []
        return unary_union(polys) if polys else None
    return None


# ---------------------------------------------------------------------------------------------- course + feature parsing

FEATURE_KINDS = ("hole", "green", "bunker", "water_hazard", "lateral_water_hazard", "cartpath", "tee")
SKIP_NAME = re.compile(r"\b(mini(ature)?|disc|frisbee|footgolf|driving range|putt[- ]?putt|adventure golf)\b", re.I)


def load_state(code: str):
    """Courses (with shapes) and features (with shapes) for one state."""
    from shapely.geometry import Point
    data = json.loads((RAW / f"osm_{code}.json").read_text())
    courses, feats = [], []
    for e in data["elements"]:
        tags = e.get("tags", {})
        if tags.get("leisure") == "golf_course":
            name = tags.get("name", "").strip()
            if not name or SKIP_NAME.search(name):
                continue
            shape = element_shapes(e) if e["type"] != "node" else Point(e["lon"], e["lat"])
            if shape is None:
                continue
            courses.append({"osm": f"{e['type']}/{e['id']}", "tags": tags, "shape": shape, "state": code})
        else:
            kind = tags.get("golf") or ("water" if tags.get("natural") == "water" else None)
            if kind in FEATURE_KINDS or kind == "water":
                shape = element_shapes(e)
                if shape is not None and not shape.is_empty:
                    feats.append({"kind": kind, "tags": tags, "shape": shape})
    return courses, feats


def assign_features(courses: list[dict], feats: list[dict]) -> None:
    """Attach each feature to the course whose outline contains it (30 m tolerance)."""
    from shapely.strtree import STRtree
    areas = [c for c in courses if c["shape"].geom_type in ("Polygon", "MultiPolygon")]
    for c in courses:
        c["features"] = defaultdict(list)
    if not areas:
        return
    tree = STRtree([c["shape"].buffer(0.0004) for c in areas])  # ~35 m in degrees
    for f in feats:
        pt = f["shape"].representative_point() if f["shape"].geom_type != "LineString" else f["shape"].interpolate(0.5, normalized=True)
        hits = tree.query(pt, predicate="intersects")
        if len(hits):
            # smallest containing course wins (a 9-hole course inside a larger club outline)
            best = min(hits, key=lambda i: areas[i]["shape"].area)
            areas[best]["features"][f["kind"]].append(f)


# ---------------------------------------------------------------------------------------------- 2b. amenities

def amenities() -> None:
    """Driving ranges, golf shops, indoor golf / simulators, and food & drink on or next to each course."""
    for code, name in STATES.items():
        out = RAW / f"osm_{code}_amenities.json"
        if out.exists():
            print(f"{code}: amenities cached"); continue
        print(f"{code}: downloading amenities for {name}...", flush=True)
        q = f"""[out:json][timeout:240];
area["name"="{name}"]["admin_level"="4"]["boundary"="administrative"]->.s;
way["leisure"="golf_course"](area.s)->.c;
.c map_to_area->.ca;
(
  nwr["golf"="driving_range"](area.s);
  nwr["leisure"="golf_course"]["golf"="driving_range"](area.s);
  nwr["shop"="golf"](area.s);
  nwr["sport"="golf"]["indoor"="yes"](area.s);
  nwr["leisure"~"^(indoor_golf|sports_centre|sports_hall)$"]["sport"="golf"](area.s);
  nwr["name"~"[Ss]imulator|[Ii]ndoor [Gg]olf|[Gg]olf [Ll]ounge|X-?[Gg]olf|[Gg]olf [Ss]tudio"](area.s);
  nwr["amenity"~"^(restaurant|bar|cafe|pub|fast_food)$"](area.ca);
  nwr["building"="clubhouse"](area.ca);
);
out center tags;"""
        try:
            data = overpass(q, attempts=4)
        except RuntimeError:
            print(f"{code}: amenities query kept failing; amenities will show as not mapped", flush=True)
            data = {"elements": []}
        out.write_text(json.dumps(data))
        print(f"{code}: {len(data['elements'])} amenity elements", flush=True)
        time.sleep(12)


# ---------------------------------------------------------------------------------------------- 2c. facilities

def facilities() -> None:
    """Parking lots, clubhouse buildings and restrooms inside each course outline, with any accessibility tags
    (wheelchair=*, capacity:disabled, parking_space=disabled). Used to measure the walk from the lot to the first tee
    and from the 9th green back to the clubhouse."""
    for code, name in STATES.items():
        out = RAW / f"osm_{code}_facilities.json"
        if out.exists():
            print(f"{code}: facilities cached"); continue
        print(f"{code}: downloading parking lots and clubhouses for {name}...", flush=True)
        q = f"""[out:json][timeout:300];
area["name"="{name}"]["admin_level"="4"]["boundary"="administrative"]->.s;
way["leisure"="golf_course"](area.s)->.c;
.c map_to_area->.ca;
(
  nwr["amenity"="parking"](area.ca);
  nwr["amenity"="parking_space"]["parking_space"="disabled"](area.ca);
  nwr["building"="clubhouse"](area.ca);
  nwr["club"="golf"](area.ca);
  nwr["amenity"="toilets"](area.ca);
);
out center tags;"""
        try:
            data = overpass(q, attempts=4)
        except RuntimeError:
            print(f"{code}: whole-state query kept failing; trying smaller map tiles", flush=True)
            data = facilities_tiled(code)
        out.write_text(json.dumps(data))
        print(f"{code}: {len(data['elements'])} facility elements", flush=True)
        time.sleep(15)


def facilities_tiled(code: str, n: int = 3) -> dict:
    """Same query in an n x n grid of boxes over the state's courses (for big states that time out)."""
    pts = [(e["bounds"]["minlat"], e["bounds"]["minlon"], e["bounds"]["maxlat"], e["bounds"]["maxlon"])
           for e in json.loads((RAW / f"osm_{code}_courses.json").read_text())["elements"] if "bounds" in e]
    s, w = min(p[0] for p in pts) - 0.01, min(p[1] for p in pts) - 0.01
    nn, ee = max(p[2] for p in pts) + 0.01, max(p[3] for p in pts) + 0.01
    elements, seen = [], set()
    for i in range(n):
        for j in range(n):
            box = (f"{s + (nn - s) * i / n:.4f},{w + (ee - w) * j / n:.4f},"
                   f"{s + (nn - s) * (i + 1) / n:.4f},{w + (ee - w) * (j + 1) / n:.4f}")
            q = f"""[out:json][timeout:180];
way["leisure"="golf_course"]({box})->.c;
.c map_to_area->.ca;
(
  nwr["amenity"="parking"](area.ca);
  nwr["amenity"="parking_space"]["parking_space"="disabled"](area.ca);
  nwr["building"="clubhouse"](area.ca);
  nwr["club"="golf"](area.ca);
  nwr["amenity"="toilets"](area.ca);
);
out center tags;"""
            try:
                part = overpass(q, attempts=4)["elements"]
            except RuntimeError:
                print(f"{code}: tile {i},{j} kept failing; skipped", flush=True)
                part = []
            for e in part:
                if (e["type"], e["id"]) not in seen:
                    seen.add((e["type"], e["id"])); elements.append(e)
            print(f"{code}: tile {i * n + j + 1}/{n * n}: {len(part)} elements", flush=True)
            time.sleep(8)
    return {"elements": elements}


# ---------------------------------------------------------------------------------------------- 2. elevation

ELEV_CACHE = RAW / "elevation.json"
SAMPLES = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0)


def hole_samples(line) -> list[tuple[float, float]]:
    return [tuple(round(v, 5) for v in line.interpolate(t, normalized=True).coords[0]) for t in SAMPLES]


def elevation() -> None:
    cache: dict[str, float] = json.loads(ELEV_CACHE.read_text()) if ELEV_CACHE.exists() else {}
    need: list[tuple[float, float]] = []
    for code in STATES:
        if not (RAW / f"osm_{code}.json").exists():
            print(f"{code}: map data not downloaded yet; skipping (run again later, cached points are kept)")
            continue
        courses, feats = load_state(code)
        for f in feats:
            if f["kind"] == "hole" and f["shape"].geom_type == "LineString":
                for lon, lat in hole_samples(f["shape"]):
                    k = f"{lat},{lon}"
                    if k not in cache:
                        need.append((lat, lon))
    need = list(dict.fromkeys(need))
    print(f"{len(cache)} elevations cached, {len(need)} to fetch ({math.ceil(len(need) / 100)} requests)", flush=True)
    for i in range(0, len(need), 100):
        batch = need[i:i + 100]
        locs = "|".join(f"{lat},{lon}" for lat, lon in batch)
        for attempt in range(5):
            try:
                res = json.loads(http(f"https://api.opentopodata.org/v1/ned10m?locations={locs}", timeout=60))
                if res.get("status") != "OK":
                    raise RuntimeError(res.get("error", "status not OK"))
                for (lat, lon), r in zip(batch, res["results"]):
                    if r.get("elevation") is not None:
                        cache[f"{lat},{lon}"] = round(r["elevation"], 2)
                break
            except Exception as e:
                print(f"    elevation request failed ({str(e)[:80]}); waiting", flush=True)
                time.sleep(10 * (attempt + 1))
        if (i // 100) % 25 == 0:
            ELEV_CACHE.write_text(json.dumps(cache))
            print(f"  {i + len(batch)}/{len(need)}", flush=True)
        time.sleep(1.05)  # public API limit: 1 request per second
    ELEV_CACHE.write_text(json.dumps(cache))
    print(f"done: {len(cache)} elevations cached")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    if stage == "fetch":
        fetch()
    elif stage == "elevation":
        elevation()
    elif stage == "amenities":
        amenities()
    elif stage == "facilities":
        facilities()
    elif stage == "build":
        from build_measure import build  # noqa: E402  (kept in its own file for readability)
        build()
    else:
        sys.exit(__doc__)
