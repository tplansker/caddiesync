"""One CaddieSync search, start to finish.

  golfer's message (+ saved profile)
    -> [code]  Input guard: clean_input + scan_for_injection
    -> [AI 1]  Profiler: message -> GolferRequest (where, how far, trip or single round, physical needs)
    -> [code]  Saved-profile floor: saved needs can only be made stricter, never looser
    -> [code]  Course Locator: courses near that place that fit holes / price tier / public access,
               pre-scored from measured facts; only the top ~10 go to the AI
    -> [AI 2]  Course Analyst: accessibility score per course (cached per course + needs)
    -> [code]  Fact check: drop claims about unknown facts, cap scores when key facts are unknown
    -> [AI 3]  Concierge: picks the best 1-5 courses and explains them
    -> [code]  Pick check, then every fact, price tier, distance and link is copied from the course database

Every step is recorded in `trace` (shown in the app) and every search is logged to output/usage_log.json.
"""

from __future__ import annotations

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import asyncio
import json
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote_plus

from pydantic_ai.messages import ModelResponse

from . import guards, locator
from .agents import agents
from .config import MODEL_NAME, OUTPUT, dollar_cost
from .models import AccessibilityAssessment, ConciergeReply, GolferRequest, Pick, Profile
from .tools import append_json, cache_get, cache_put, course_facts, load_courses, now_iso

DEFAULT_PLACE = "New Haven, CT"
# Courses on islands without a bridge: a trip there means a ferry (or a flight).
ISLAND_TOWNS = {("MA", t) for t in ("Vineyard Haven", "Tisbury", "Edgartown", "Oak Bluffs", "West Tisbury", "Chilmark",
                                    "Aquinnah", "Nantucket", "Siasconset")} | {("RI", "New Shoreham"), ("RI", "Block Island"),
                                    ("ME", "Vinalhaven"), ("ME", "North Haven"), ("ME", "Islesboro"), ("ME", "Chebeague Island")}


def needs_ferry(c: dict[str, Any], center: dict[str, Any]) -> bool:
    island = (c["state"], c.get("town") or "") in ISLAND_TOWNS
    return island and (c["state"], center["label"].split(",")[0]) not in ISLAND_TOWNS and "vineyard" not in center["label"].lower() \
        and "nantucket" not in center["label"].lower() and "block island" not in center["label"].lower()
ANALYZE_TOP = 10


class Blocked(Exception):
    """A search stopped by a safety net. The message is safe to show."""


def _provider_blocked(e: BaseException) -> bool:
    """The model provider's own safety filter (Azure OpenAI content/jailbreak filter via Portkey) refused a prompt."""
    text = str(e).lower()
    return "content management policy" in text or "content_filter" in text or "jailbreak" in text


class Meter:
    """Adds up real token counts reported by the API, per agent."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def add(self, agent: str, result: Any, label: str = "") -> dict[str, Any]:
        u = result.usage
        cached = u.cache_read_tokens or 0
        row = {
            "agent": agent, "label": label, "requests": u.requests,
            "input_tokens": u.input_tokens or 0, "cached_input_tokens": cached,
            "output_tokens": u.output_tokens or 0, "reasoning_tokens": u.output_reasoning_tokens or 0,
            "cost_usd": round(dollar_cost(u.input_tokens or 0, cached, u.output_tokens or 0, u.cache_write_tokens or 0), 6),
            "model": next((m.model_name for m in result.all_messages() if isinstance(m, ModelResponse) and m.model_name), MODEL_NAME),
        }
        self.calls.append(row)
        return row

    def totals(self) -> dict[str, Any]:
        keys = ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_tokens", "requests")
        out = {k: sum(c[k] for c in self.calls) for k in keys}
        out["cost_usd"] = round(sum(c["cost_usd"] for c in self.calls), 6)
        return out


def _step(trace: list[dict[str, Any]], who: str, kind: str, summary: str, started: float, **detail: Any) -> None:
    trace.append({"step": len(trace) + 1, "who": who, "kind": kind, "summary": summary,
                  "ms": round((time.perf_counter() - started) * 1000), **detail})


async def _assess(course: dict[str, Any], req: GolferRequest, meter: Meter) -> tuple[AccessibilityAssessment, dict[str, Any]]:
    facts = course_facts(course)
    prompt = ("Group needs:\n" + req.needs.model_dump_json(indent=1)
              + f"\n\nCourse facts:\n{json.dumps(facts, indent=1)}")
    for attempt in range(2):  # one retry for transient errors (timeouts, brief gateway errors); never for the content filter
        try:
            result = await agents()["analyst"].run(prompt, usage_limits=guards.ANALYST_LIMITS)
            break
        except Exception as e:
            if attempt == 1 or _provider_blocked(e):
                raise
            await asyncio.sleep(1.5)
    row = meter.add("course_analyst", result, course["name"])
    return result.output, row


def highlights(c: dict[str, Any]) -> list[str]:
    """Measured facts in plain words, for the result card (code, not AI)."""
    out = []
    if c.get("total_climb_ft") is not None:
        hole = f" (hole {c['biggest_climb_hole']})" if c.get("biggest_climb_hole") else ""
        out.append(f"{c['total_climb_ft']:,} ft of climbing over the round; biggest climb {c['biggest_hole_climb_ft']} ft{hole}")
    if c.get("holes_steeper_than_ramp") is not None:
        n = c["holes_steeper_than_ramp"]
        out.append("No hole has a stretch steeper than a wheelchair ramp (8.3%)" if n == 0
                   else f"{n} hole{'s' if n != 1 else ''} with a stretch steeper than a wheelchair ramp; steepest about {c['steepest_grade_pct']}%")
    if c.get("water_carry_holes") is not None:
        n = c["water_carry_holes"]
        out.append("No shots over water" if n == 0 else f"{n} hole{'s' if n != 1 else ''} with a shot over water (longest about {c['longest_water_carry_yd']} yd)")
    if c.get("bunkers_near_green_avg") is not None:
        out.append(f"About {c['bunkers_near_green_avg']:g} bunker{'' if c['bunkers_near_green_avg'] == 1 else 's'} per green")
    if c.get("avg_walk_between_holes_yd") is not None:
        out.append(f"About {c['avg_walk_between_holes_yd']} yd from each green to the next tee")
    if c.get("cartpath_to_green_yd") is not None:
        out.append(f"Mapped cart paths come within about {c['cartpath_to_green_yd']} yd of a typical green")
    if c.get("walk_lot_to_first_tee_yd") is not None:
        out.append(f"About {c['walk_lot_to_first_tee_yd']} yd from the parking lot to the 1st tee")
    if c.get("walk_9th_green_to_clubhouse_yd") is not None:
        y = c["walk_9th_green_to_clubhouse_yd"]
        out.append(f"About {y} yd from the 9th green back to the {c.get('walk_back_to') or 'clubhouse'}"
                   + (": easy to stop after 9" if y <= 150 else ""))
    return out


def access_info(c: dict[str, Any]) -> dict[str, Any]:
    """ADA access, adaptive equipment and veterans programs for the card: researched facts with sources, mapped
    tags, or None (unknown: ask). Copied from the database by code, never written by the AI."""
    def known(researched: Any, mapped: Any) -> dict[str, Any] | None:
        if researched not in (None, ""):
            return {"value": researched, "how": "researched"}
        if mapped not in (None, ""):
            return {"value": mapped, "how": "map"}
        return None
    return {
        "parking": known(c.get("accessible_parking"), c.get("accessible_parking_mapped")),
        "clubhouse": known(c.get("clubhouse_step_free"), c.get("clubhouse_wheelchair_mapped")),
        "restrooms": known(c.get("accessible_restrooms"), c.get("restrooms_mapped")),
        "adaptive_equipment": c.get("adaptive_equipment") or [], "adaptive_note": c.get("adaptive_note"),
        "adaptive_programs": c.get("adaptive_programs") or [],
        "veterans_program": c.get("veterans_program"), "veterans_note": c.get("veterans_note"),
        "walk_lot_to_first_tee_yd": c.get("walk_lot_to_first_tee_yd"),
        "walk_9th_green_to_clubhouse_yd": c.get("walk_9th_green_to_clubhouse_yd"), "walk_back_to": c.get("walk_back_to"),
    }


EXTRA_Q = {"driving_range": "Do you have a driving range, and is it covered?", "practice_green": "Is there a practice putting green?",
           "simulator": "Do you have an indoor simulator for rainy days?", "pro_shop": "Do you have a pro shop, and can we rent clubs?",
           "food_drink": "Is there a restaurant, bar or snack bar open when we play?",
           "veterans_program": "Do you run PGA HOPE or any other golf program for veterans?"}


def call_script(c: dict[str, Any], verify: list[str], req: GolferRequest) -> list[str]:
    """What to ask when you call, built in code from what the data doesn't know."""
    qs = [f"Can you tell me {v[:1].lower() + v[1:].rstrip('.?')}?" for v in verify if v]
    qs += [EXTRA_Q[w] for w in req.wants if not locator.has_extra(c, w)]
    if req.needs.adaptive_cart and c.get("adaptive_equipment"):
        qs.insert(0, "Can you reserve your adaptive cart for our tee time, and what paperwork do you need?")
    elif req.needs.adaptive_cart and c.get("adaptive_cart") is None:
        qs.append("Do you have a single-rider or adaptive cart (like a SoloRider), or can I bring my own?")
    limited = req.needs.walking_ability != "full" or req.needs.adaptive_cart
    if limited and c.get("accessible_parking") is None:
        qs.append("Is there accessible (handicap) parking close to the clubhouse and the 1st tee?")
    if limited and c.get("clubhouse_step_free") is None:
        qs.append("Is the clubhouse step-free, with accessible restrooms, and are there restrooms out on the course?")
    if req.needs.walking_ability != "full" and c.get("cart_near_greens") is None:
        qs.append("Can a cart with a medical or handicap flag drive close to the greens and tees?")
    if limited:
        qs.append("Are there cart-path-only days, and quieter tee times when we won't feel rushed?")
    if c.get("access") in (None, "unknown"):
        qs.insert(0, "Is the course open to the public, and do we need to book ahead?")
    if c.get("price_tier") is None:
        qs.append("What are your current green fees and cart fees?")
    return list(dict.fromkeys(qs))[:8]


async def search(message: str, visitor: str = "local", use_cache: bool = True,
                 profile: Profile | None = None) -> dict[str, Any]:
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
    trace: list[dict[str, Any]] = []
    meter = Meter()
    safety: list[dict[str, str]] = []
    t0 = time.perf_counter()

    # ---- Safety nets before any AI call ----
    if guards.over_budget():
        raise Blocked("CaddieSync has reached today's spending limit. Please try again tomorrow.")
    if guards.rate_limited(visitor):
        raise Blocked("Too many searches in a short time. Please wait a few minutes.")
    text = guards.clean_input(message)
    if len(text) < 5:
        raise Blocked("Tell CaddieSync a little about your round: where, who is playing, and any physical needs.")
    flags = guards.scan_for_injection(text)
    for f in flags:
        safety.append({"layer": "code: input scan", "event": f"Flagged: {f}"})
    if profile is not None:  # saved text becomes part of the prompt, so it gets the same checks
        profile.notes = guards.clean_input(profile.notes)[:300]
        for f in guards.scan_for_injection(profile.notes):
            flags.append(f"saved profile note {f}")
            safety.append({"layer": "code: input scan", "event": f"Flagged in saved profile note: {f}"})
    _step(trace, "Input guard", "code", f"Cleaned input ({len(text)} chars). "
          + (f"Flagged {len(flags)} attack pattern(s)." if flags else "No attack patterns found."), t0, flags=flags)

    # ---- Agent 1: Profiler ----
    t = time.perf_counter()
    today = date.today()
    scan_line = f"Automatic safety scan: {'; '.join(flags)}\n" if flags else ""
    prompt = (f"Today is {guards.nice_date(today)}, {today.year}.\n{scan_line}"
              + (f"<saved_profile>\n{profile.as_prompt()}\n</saved_profile>\n" if profile else "")
              + f"<golfer_request>\n{text}\n</golfer_request>")
    try:
        result = await agents()["profiler"].run(prompt, usage_limits=guards.PROFILER_LIMITS)
    except Exception as e:
        if not _provider_blocked(e):
            raise
        append_json(OUTPUT / "audit_trail.json", {"run_id": run_id, "time": now_iso(), "message": text, "trace": trace,
                                                  "safety": safety + [{"layer": "provider: content filter",
                                                                       "event": "The AI provider refused the message as a likely attack."}]})
        raise Blocked("This message was blocked by the AI provider's safety filter as a likely attack"
                      + (", and CaddieSync's own checks flagged it too" if flags else "")
                      + ". Please describe your round in your own words.") from None
    req: GolferRequest = result.output
    row = meter.add("profiler", result)
    if flags and not req.refused_requests:  # if code flagged something the model did not list, add it
        req.refused_requests = flags
    for r in req.refused_requests:
        safety.append({"layer": "AI: profiler", "event": f"Refused: {r}"})
    _step(trace, "Profiler", "ai", f"Understood: {req.group_summary}", t, output=req.model_dump(), usage=row)

    # ---- Saved profile floor (code): a vague message can never loosen saved needs ----
    profile_notes: list[str] = []
    if profile is not None:
        t = time.perf_counter()
        profile_notes = profile.apply_floor(req.needs)
        if req.price_tier_max is None and profile.price_tier_max:
            req.price_tier_max = profile.price_tier_max
            profile_notes.append(f"price: up to {profile.price_tier_max}")
        if profile.veteran and "veterans_program" not in req.wants:
            req.wants.append("veterans_program")
            profile_notes.append("veterans programs: shown")
        if req.holes == "either" and profile.holes != "either":
            req.holes = profile.holes
            profile_notes.append(f"holes: {profile.holes}")
        _step(trace, "Saved profile", "code",
              ("Kept your saved needs and defaults: " + "; ".join(profile_notes)) if profile_notes
              else "Your saved needs were already covered by the Profiler.", t)

    # ---- Course Locator (code) ----
    t = time.perf_counter()
    courses = load_courses()
    place_note = None
    center = locator.resolve(req.near)
    if center is None:
        home = profile.home_town if profile and profile.home_town else None
        home_pt = locator.resolve(home)
        center = home_pt or locator.resolve(DEFAULT_PLACE)
        where = f"{center['label']}" + (" (your home town)" if home_pt else "")
        place_note = (f"CaddieSync couldn't find \"{req.near}\" on the map, so it searched around {where}." if req.near
                      else f"No place given, so CaddieSync searched around {where}."
                      + ("" if home_pt else " Add a town or region to search elsewhere."))
    elif center.get("note"):
        place_note = center["note"]
    radius = req.radius_miles or center.get("radius") or (profile.travel_miles if profile else None) or (40 if req.trip else 25)
    named = locator.find_named(courses, req.course_named) if req.course_named else []
    widened = False
    if named:
        pool = [{**c, "distance_mi": round(locator.miles(center["lat"], center["lon"], c["lat"], c["lon"]), 1)} for c in named][:3]
    else:
        pool = locator.find(courses, center, radius, req.holes, req.price_tier_max, req.include_private)
        if len(pool) < 3 and radius < 60:  # too few nearby: widen once rather than come back empty
            radius, widened = min(radius * 2, 60), True
            pool = locator.find(courses, center, radius, req.holes, req.price_tier_max, req.include_private)
    for c in pool:
        c["pre_score"] = locator.pre_score(c, req.needs, req.wants)
    pool.sort(key=lambda c: (-c["pre_score"], c["distance_mi"]))
    top = pool if named else pool[: max(ANALYZE_TOP, req.rounds + 3)]
    _step(trace, "Course Locator", "code",
          (f"Looked up \"{req.course_named}\": found {len(named)} match(es)." if named else
           f"{len(pool)} course(s) within {radius:g} mi of {center['label']}"
           + (" (widened the search: few courses nearby)" if widened else "")
           + f"; sending the top {len(top)} by measured accessibility to the AI."), t,
          center=center["label"], radius_mi=radius, considered=len(pool),
          shortlist={c["name"]: c["pre_score"] for c in top})

    # ---- Agent 2: Course Analyst (cached) ----
    t = time.perf_counter()
    key = req.needs.cache_key()
    by_id = {c["course_id"]: c for c in top}
    assessments: dict[str, dict[str, Any]] = {}
    hits, misses, saved = [], [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for cid in by_id:
        entry = cache_get(cid, key) if use_cache else None
        if entry:
            hits.append(cid)
            assessments[cid] = entry
            for k in saved:
                saved[k] += entry["usage"][k]
        else:
            misses.append(cid)
    if len(misses) > guards.MAX_FRESH_ASSESSMENTS:
        safety.append({"layer": "code: budget", "event": f"Capped fresh course assessments at {guards.MAX_FRESH_ASSESSMENTS} (had {len(misses)})."})
        misses = misses[: guards.MAX_FRESH_ASSESSMENTS]
    results = await asyncio.gather(*(_assess(by_id[cid], req, meter) for cid in misses), return_exceptions=True)
    for cid, res in zip(misses, results):
        if isinstance(res, BaseException):
            safety.append({"layer": "code: agent error",
                           "event": f"Course Analyst failed on {by_id[cid]['name']} ({type(res).__name__}) after a retry; skipped it."})
            continue
        a, usage_row = res
        a, notes = guards.check_assessment(a, course_facts(by_id[cid]), req.needs)
        for n in notes:
            safety.append({"layer": "code: fact check", "event": f"{by_id[cid]['name']}: {n}"})
        entry = {"assessment": a.model_dump(), "created": now_iso(),
                 "usage": {k: usage_row[k] for k in ("input_tokens", "output_tokens", "cost_usd")}}
        if use_cache:
            cache_put(cid, key, entry)
        assessments[cid] = entry
    saved["cost_usd"] = round(saved["cost_usd"], 6)
    _step(trace, "Course Analyst", "ai",
          f"Scored {len(assessments)} course(s): {len(hits)} from cache (0 tokens), {len(misses)} fresh.", t,
          cache_hits=[by_id[c]["name"] for c in hits], fresh=[by_id[c]["name"] for c in misses],
          scores={by_id[c]["name"]: v["assessment"]["score"] for c, v in assessments.items()},
          usage=[c for c in meter.calls if c["agent"] == "course_analyst"])

    # ---- Concierge ----
    ranked = sorted(assessments, key=lambda c: (-assessments[c]["assessment"]["score"], by_id[c]["distance_mi"]))
    view = [{"course_id": cid, "course": by_id[cid]["name"], "town": f"{by_id[cid].get('town')}, {by_id[cid]['state']}",
             "distance_mi": by_id[cid]["distance_mi"], "holes": by_id[cid].get("holes"), "price_tier": by_id[cid].get("price_tier"),
             "score": assessments[cid]["assessment"]["score"], "verdict": assessments[cid]["assessment"]["verdict"],
             "one_line": assessments[cid]["assessment"]["one_line"], "findings": assessments[cid]["assessment"]["findings"],
             "verify_with_course": assessments[cid]["assessment"]["verify_with_course"],
             "mapped_extras": sorted((by_id[cid].get("amenities") or {}).keys()),
             "adaptive_equipment": by_id[cid].get("adaptive_equipment"),
             "veterans_program": by_id[cid].get("veterans_program"),
             "island_needs_ferry": needs_ferry(by_id[cid], center)} for cid in ranked[:8]]
    t = time.perf_counter()
    if view:
        prompt = (f"Group: {req.group_summary}\nNeeds: {req.needs.model_dump_json()}\n"
                  f"Searching: {'a golf trip' if req.trip else 'a round'} near {center['label']}; suggest up to {req.rounds} course(s).\n"
                  f"Extras they want: {json.dumps(req.wants)}\n"
                  f"refused_requests: {json.dumps(req.refused_requests)}\n\nCandidates:\n{json.dumps(view, indent=1)}")
        try:
            result = await agents()["concierge"].run(prompt, usage_limits=guards.CONCIERGE_LIMITS)
            reply: ConciergeReply = result.output
            row = meter.add("concierge", result)
        except Exception as e:  # provider filter or model failure: choose in code so the golfer still gets results
            if not _provider_blocked(e):
                safety.append({"layer": "code: agent error", "event": "Concierge failed; picked the top-scored courses in code."})
            else:
                safety.append({"layer": "provider: content filter", "event": "The AI provider refused the Concierge prompt; picked the top-scored courses in code."})
            reply, row = ConciergeReply(headline=f"Top accessibility matches near {center['label']}", picks=[],
                                        note_to_golfer="Here are the highest-scoring courses for your group."), None
            reply.picks = [Pick(course_id=c, why_it_fits=assessments[c]["assessment"]["one_line"]) for c in ranked[: req.rounds]]
        reply, notes = guards.check_picks(reply, set(assessments))
        reply.picks = reply.picks[: req.rounds]
        for n in notes:
            safety.append({"layer": "code: pick check", "event": n})
        if not reply.picks:  # model returned nothing usable: fall back to the top score, in code
            best = ranked[0]
            reply.picks = [Pick(course_id=best, why_it_fits=assessments[best]["assessment"]["one_line"])]
        _step(trace, "Concierge", "ai", f"Picked {len(reply.picks)} course(s): {reply.headline}", t, output=reply.model_dump(), usage=row)
    else:
        reply = ConciergeReply(headline="No courses matched this search", picks=[],
                               note_to_golfer="Try a wider radius, a different town, or a higher price tier.")
        _step(trace, "Concierge", "code", "Skipped the AI: no candidates, so no tokens spent.", t)

    # ---- Final answer: every fact, tier, distance and link comes from the course database ----
    recs = []
    for p in reply.picks:
        c, a = by_id[p.course_id], assessments[p.course_id]["assessment"]
        recs.append({
            "course_id": c["course_id"], "course_name": c["name"], "town": c.get("town"), "state": c["state"],
            "distance_mi": c["distance_mi"], "holes": c.get("holes"), "par": c.get("par"), "access": c.get("access"),
            "price_tier": c.get("price_tier"), "price_note": c.get("price_note"),
            "accessibility_match_score": a["score"], "verdict": a["verdict"], "one_line": a["one_line"],
            "why_it_fits": p.why_it_fits, "heads_up": p.heads_up, "findings": a["findings"],
            "verify_with_course": a["verify_with_course"], "highlights": highlights(c),
            "call_script": call_script(c, a["verify_with_course"], req),
            "website": c.get("website"), "booking_url": c.get("booking_url"), "phone": c.get("phone"),
            "directions_url": f"https://www.google.com/maps/dir/?api=1&destination={c['lat']},{c['lon']}",
            "tee_time_search_url": "https://www.google.com/search?q=" + quote_plus(f"{c['name']} {c.get('town') or ''} {c['state']} tee times"),
            "map_detail": c.get("map_detail"), "verified_by_phone": c.get("verified_by_phone", False),
            "amenities": c.get("amenities") or {}, "wants": req.wants, "ferry": needs_ferry(c, center),
            "access_info": access_info(c),
            "lat": c["lat"], "lon": c["lon"], "sources": c.get("sources", []),
        })
    picked = {r["course_id"] for r in recs}
    others = [{"course_id": cid, "course_name": by_id[cid]["name"], "town": by_id[cid].get("town"), "state": by_id[cid]["state"],
               "distance_mi": by_id[cid]["distance_mi"], "score": assessments[cid]["assessment"]["score"],
               "verdict": assessments[cid]["assessment"]["verdict"], "one_line": assessments[cid]["assessment"]["one_line"],
               "lat": by_id[cid]["lat"], "lon": by_id[cid]["lon"]}
              for cid in ranked if cid not in picked]

    totals = meter.totals()
    log = {"run_id": run_id, "time": now_iso(), "model": MODEL_NAME, "use_cache": use_cache,
           "message_chars": len(text), "flags": flags, "refused": req.refused_requests,
           "center": center["label"], "radius_mi": radius, "courses_considered": len(pool),
           "calls": meter.calls, **totals, "analyst_cache_hits": len(hits), "analyst_fresh": len(misses),
           "saved_by_cache": saved, "elapsed_ms": round((time.perf_counter() - t0) * 1000)}
    append_json(OUTPUT / "usage_log.json", log)
    append_json(OUTPUT / "audit_trail.json", {"run_id": run_id, "time": now_iso(), "message": text,
                                              "trace": trace, "safety": safety, "recommendations": recs})
    return {
        "run_id": run_id, "headline": reply.headline, "note": reply.note_to_golfer,
        "searched": {"place": center["label"], "lat": center["lat"], "lon": center["lon"], "radius_mi": radius,
                     "place_note": place_note, "trip": req.trip, "rounds": req.rounds, "players": req.players,
                     "holes": req.holes, "price_tier_max": req.price_tier_max, "group": req.group_summary, "wants": req.wants,
                     "needs": req.needs.model_dump(), "courses_considered": len(pool),
                     "used_profile": profile is not None, "from_profile": profile_notes},
        "recommendations": recs, "other_courses": others, "safety": safety, "trace": trace,
        "usage": {**totals, "calls": meter.calls, "analyst_cache_hits": len(hits), "analyst_fresh": len(misses),
                  "saved_by_cache": saved, "elapsed_ms": log["elapsed_ms"]},
    }
