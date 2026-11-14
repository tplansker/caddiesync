"""One CaddieSync search, start to finish.

  golfer's message
    -> [code]  clean_input + scan_for_injection
    -> [AI 1]  Profiler: message -> GolferRequest (date, time, budget, physical needs)
    -> [code]  Tee-Time Sniper: open tee times that fit date/time/budget (simulated tee sheet)
    -> [AI 2]  Course Analyst: accessibility score for each course that has open times
               (cached per course + needs, so repeat questions cost 0 tokens)
    -> [code]  check_assessment, rank
    -> [AI 3]  Concierge: picks the best 1-3 and explains them
    -> [code]  check_picks, then prices and times are copied from the tee sheet

Every step is recorded in `trace` (shown in the app) and every search is logged to output/usage_log.json.
"""

from __future__ import annotations

import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import asyncio
import json
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

from pydantic_ai.messages import ModelResponse

from . import guards
from .agents import agents
from .config import MODEL_NAME, OUTPUT, dollar_cost
from .models import AccessibilityAssessment, ConciergeReply, GolferRequest
from .tools import (WINDOWS, append_json, cache_get, cache_put, course_facts, find_tee_times, load_courses,
                    next_weekday, now_iso)


class Blocked(Exception):
    """A search stopped by a safety net before any AI was called. The message is safe to show."""


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
    result = await agents()["analyst"].run(prompt, usage_limits=guards.ANALYST_LIMITS)
    row = meter.add("course_analyst", result, course["name"])
    return result.output, row


async def search(message: str, visitor: str = "local", use_cache: bool = True) -> dict[str, Any]:
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
        raise Blocked("Tell CaddieSync a little about your round: when, who is playing, and any physical needs.")
    flags = guards.scan_for_injection(text)
    for f in flags:
        safety.append({"layer": "code: input scan", "event": f"Flagged: {f}"})
    _step(trace, "Input guard", "code", f"Cleaned input ({len(text)} chars). "
          + (f"Flagged {len(flags)} attack pattern(s)." if flags else "No attack patterns found."), t0, flags=flags)

    # ---- Agent 1: Profiler ----
    t = time.perf_counter()
    today = date.today()
    scan_line = f"Automatic safety scan: {'; '.join(flags)}\n" if flags else ""
    prompt = (f"Today is {guards.nice_date(today)}, {today.year} ({today.isoformat()}).\n{scan_line}"
              f"<golfer_request>\n{text}\n</golfer_request>")
    result = await agents()["profiler"].run(prompt, usage_limits=guards.PROFILER_LIMITS)
    req: GolferRequest = result.output
    row = meter.add("profiler", result)
    # Belt and braces: if code flagged something the model did not list, add it.
    if flags and not req.refused_requests:
        req.refused_requests = flags
    for r in req.refused_requests:
        safety.append({"layer": "AI: profiler", "event": f"Refused: {r}"})
    _step(trace, "Profiler", "ai", f"Understood: {req.group_summary}", t, output=req.model_dump(), usage=row)

    # ---- Tee-Time Sniper (code) ----
    t = time.perf_counter()
    day, date_note = guards.resolve_date(req.play_date, today, next_weekday(today))
    now = datetime.now()
    if day == today and WINDOWS[req.time_window][1] <= now.hour * 60 + now.minute + 30:
        day = day + timedelta(days=7)
        date_note = f"That time has already passed today, so CaddieSync searched {guards.nice_date(day)}."
    courses = load_courses()
    pool = [c for c in courses.values() if req.holes == "either" or str(c.get("holes")) == req.holes]
    found = find_tee_times(pool, day, req.time_window, req.players, req.max_price_per_player)
    _step(trace, "Tee-Time Sniper", "code",
          f"Checked {len(pool)} tee sheets for {guards.nice_date(day)}, {req.time_window}, {req.players} player(s)"
          + (f", up to ${req.max_price_per_player:.0f}" if req.max_price_per_player else "")
          + f": {len(found)} course(s) have times that fit.", t,
          courses_with_times=[courses[cid]["name"] for cid in found])

    # ---- Agent 2: Course Analyst (cached) ----
    t = time.perf_counter()
    key = req.needs.cache_key()
    assessments: dict[str, dict[str, Any]] = {}
    hits, misses, saved = [], [], {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
    for cid in found:
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
    results = await asyncio.gather(*(_assess(courses[cid], req, meter) for cid in misses), return_exceptions=True)
    for cid, res in zip(misses, results):
        if isinstance(res, BaseException):
            safety.append({"layer": "code: agent error", "event": f"Course Analyst failed on {courses[cid]['name']}; skipped it."})
            continue
        a, usage_row = res
        a, notes = guards.check_assessment(a, course_facts(courses[cid]), req.needs)
        for n in notes:
            safety.append({"layer": "code: fact check", "event": f"{courses[cid]['name']}: {n}"})
        entry = {"assessment": a.model_dump(), "created": now_iso(),
                 "usage": {k: usage_row[k] for k in ("input_tokens", "output_tokens", "cost_usd")}}
        if use_cache:
            cache_put(cid, key, entry)
        assessments[cid] = entry
    saved["cost_usd"] = round(saved["cost_usd"], 6)
    _step(trace, "Course Analyst", "ai",
          f"Scored {len(assessments)} course(s): {len(hits)} from cache (0 tokens), {len(misses)} fresh.", t,
          cache_hits=[courses[c]["name"] for c in hits], fresh=[courses[c]["name"] for c in misses],
          scores={courses[c]["name"]: v["assessment"]["score"] for c, v in assessments.items()},
          usage=[c for c in meter.calls if c["agent"] == "course_analyst"])

    # ---- Rank and build the candidate list (code) ----
    ranked = sorted(assessments, key=lambda c: -assessments[c]["assessment"]["score"])
    candidates: dict[str, dict[str, Any]] = {}
    for cid in ranked[:6]:
        a = assessments[cid]["assessment"]
        for s in found[cid]["open"][:2]:
            candidates[s["slot_id"]] = {"slot": s, "course": courses[cid], "a": a}
    view = [{"slot_id": sid, "course": c["course"]["name"], "town": c["course"].get("town"),
             "time_of_day": c["slot"]["time"], "deal": c["slot"]["deal"], "score": c["a"]["score"],
             "verdict": c["a"]["verdict"], "one_line": c["a"]["one_line"], "findings": c["a"]["findings"],
             "verify_with_course": c["a"]["verify_with_course"]} for sid, c in candidates.items()]

    # ---- Agent 3: Concierge ----
    t = time.perf_counter()
    if candidates:
        prompt = (f"Group: {req.group_summary}\nNeeds: {req.needs.model_dump_json()}\n"
                  f"refused_requests: {json.dumps(req.refused_requests)}\n\nCandidates:\n{json.dumps(view, indent=1)}")
        result = await agents()["concierge"].run(prompt, usage_limits=guards.CONCIERGE_LIMITS)
        reply: ConciergeReply = result.output
        row = meter.add("concierge", result)
        reply, notes = guards.check_picks(reply, set(candidates))
        for n in notes:
            safety.append({"layer": "code: tee-time check", "event": n})
        if not reply.picks:  # model returned nothing usable: fall back to the top score, in code
            best = max(candidates, key=lambda s: candidates[s]["a"]["score"])
            from .models import Pick
            reply.picks = [Pick(slot_id=best, why_it_fits=candidates[best]["a"]["one_line"])]
        _step(trace, "Concierge", "ai", f"Picked {len(reply.picks)} option(s): {reply.headline}", t,
              output=reply.model_dump(), usage=row)
    else:
        reply = ConciergeReply(headline="No open tee times fit this search", picks=[],
                               note_to_golfer="Try another day, a wider time window, or a higher budget.")
        _step(trace, "Concierge", "code", "Skipped the AI: no candidates, so no tokens spent.", t)

    # ---- Final answer: prices and times come from the tee sheet, never from a model ----
    recs = []
    for p in reply.picks:
        c = candidates[p.slot_id]
        s, course, a = c["slot"], c["course"], c["a"]
        recs.append({
            "slot_id": s["slot_id"], "course_name": course["name"], "town": course.get("town"),
            "date": s["date"], "tee_time": guards.nice_time(s["time"]),
            "price_per_player": s["price_per_player"], "open_spots": s["open_spots"], "deal": s["deal"],
            "accessibility_match_score": a["score"], "verdict": a["verdict"], "one_line": a["one_line"],
            "why_it_fits": p.why_it_fits, "heads_up": p.heads_up, "findings": a["findings"],
            "verify_with_course": a["verify_with_course"], "website": course.get("website"),
            "phone": course.get("phone"), "simulated_tee_sheet": True,
        })
    waitlist = [{"slot_id": w["slot_id"], "course_name": courses[cid]["name"],
                 "tee_time": guards.nice_time(w["time"]),
                 "price_per_player": w["price_per_player"], "score": assessments[cid]["assessment"]["score"]}
                for cid in ranked[:4] if assessments[cid]["assessment"]["score"] >= 50
                for w in found[cid]["waitlist"][:1]]
    others = [{"course_name": courses[c]["name"], "score": assessments[c]["assessment"]["score"],
               "verdict": assessments[c]["assessment"]["verdict"], "one_line": assessments[c]["assessment"]["one_line"]}
              for c in ranked if c not in {candidates[p.slot_id]["course"]["course_id"] for p in reply.picks}]

    totals = meter.totals()
    log = {"run_id": run_id, "time": now_iso(), "model": MODEL_NAME, "use_cache": use_cache,
           "message_chars": len(text), "flags": flags, "refused": req.refused_requests,
           "calls": meter.calls, **totals, "analyst_cache_hits": len(hits), "analyst_fresh": len(misses),
           "saved_by_cache": saved, "elapsed_ms": round((time.perf_counter() - t0) * 1000)}
    append_json(OUTPUT / "usage_log.json", log)
    append_json(OUTPUT / "audit_trail.json", {"run_id": run_id, "time": now_iso(), "message": text,
                                              "trace": trace, "safety": safety, "recommendations": recs})
    return {
        "run_id": run_id, "headline": reply.headline, "note": reply.note_to_golfer,
        "searched": {"date": day.isoformat(), "date_label": f"{guards.nice_date(day)}", "note": date_note,
                     "time_window": req.time_window, "players": req.players,
                     "max_price_per_player": req.max_price_per_player, "group": req.group_summary,
                     "needs": req.needs.model_dump()},
        "recommendations": recs, "waitlist_options": waitlist, "other_courses": others,
        "safety": safety, "trace": trace,
        "usage": {**totals, "calls": meter.calls, "analyst_cache_hits": len(hits), "analyst_fresh": len(misses),
                  "saved_by_cache": saved, "elapsed_ms": log["elapsed_ms"]},
    }
