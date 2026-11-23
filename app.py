"""TrueCourse web app: FastAPI backend + the single-page UI in static/.

Run locally:   python app.py          (then open http://127.0.0.1:8000)
On Render:     uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import time
from collections import defaultdict, deque

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from pydantic_ai.exceptions import ModelAuthenticationError

from caddiesync import accounts, guards
from caddiesync.config import DAILY_BUDGET_USD, MODEL_NAME, OUTPUT, ROOT, ConfigError
from caddiesync.models import Profile
from caddiesync.pipeline import Blocked, search
from caddiesync.tools import load_courses, read_json

log = logging.getLogger("caddiesync")
app = FastAPI(title="TrueCourse", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.middleware("http")
async def security_headers(request: Request, call_next):
    """Basic hardening for a public site: no MIME sniffing, no framing by other sites, minimal referrer data."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), geolocation=(), microphone=(self)")
    return response
accounts.init_db()
COOKIE = "cs_session"


class SearchIn(BaseModel):
    message: str = Field(max_length=4_000)
    use_profile: bool = True
    # Browser copy of a profile, used only when the visitor is not signed in (guest, or account was reset).
    profile: Profile | None = None


class Credentials(BaseModel):
    username: str = Field(max_length=30)
    password: str = Field(max_length=200)
    profile: Profile | None = None  # on sign-up: restore the browser's saved copy


def _visitor(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health() -> dict:
    courses = load_courses().values()
    return {"ok": True, "model": MODEL_NAME, "courses": len(courses),
            "holes_measured": sum(c.get("holes_mapped") or 0 for c in courses if c.get("total_climb_ft") is not None)}


# ---------- Accounts (free: username + password, no email) ----------

_attempts: dict[str, deque[float]] = defaultdict(deque)


def _json_only(request: Request) -> None:
    """Changes to accounts must be JSON. Together with SameSite=Lax cookies this blocks cross-site form posts."""
    if not request.headers.get("content-type", "").startswith("application/json"):
        raise HTTPException(415, "Send JSON.")


def _too_many_attempts(request: Request) -> bool:
    q, now = _attempts[_visitor(request)], time.time()
    while q and now - q[0] > 600:
        q.popleft()
    q.append(now)
    return len(q) > 10  # 10 sign-in / sign-up tries per 10 minutes per visitor


def _set_session(response: Response, request: Request, user_id: int) -> None:
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(COOKIE, accounts.new_session(user_id), max_age=accounts.SESSION_DAYS * 86400,
                        httponly=True, samesite="lax", secure=secure, path="/")


def current_user(request: Request) -> tuple[int, str] | None:
    return accounts.user_for_token(request.cookies.get(COOKIE))


def _me(user: tuple[int, str]) -> dict:
    p = accounts.get_profile(user[0])
    return {"signed_in": True, "username": user[1], "profile": p.model_dump() if p else None}


@app.get("/api/me")
def api_me(request: Request) -> dict:
    user = current_user(request)
    return _me(user) if user else {"signed_in": False}


@app.post("/api/signup", dependencies=[Depends(_json_only)])
def api_signup(body: Credentials, request: Request, response: Response) -> dict:
    if _too_many_attempts(request):
        raise HTTPException(429, "Too many tries. Please wait a few minutes.")
    try:
        uid = accounts.create_user(body.username.strip(), body.password)
    except accounts.AccountError as e:
        raise HTTPException(400, str(e)) from None
    if body.profile:
        accounts.save_profile(uid, _checked(body.profile))
    _set_session(response, request, uid)
    return _me((uid, body.username.strip()))


@app.post("/api/login", dependencies=[Depends(_json_only)])
def api_login(body: Credentials, request: Request, response: Response) -> dict:
    if _too_many_attempts(request):
        raise HTTPException(429, "Too many tries. Please wait a few minutes.")
    try:
        uid = accounts.verify_user(body.username.strip(), body.password)
    except accounts.AccountError as e:
        raise HTTPException(401, str(e)) from None
    _set_session(response, request, uid)
    return _me((uid, body.username.strip()))


@app.post("/api/logout")
def api_logout(request: Request, response: Response) -> dict:
    accounts.end_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"signed_in": False}


def _checked(profile: Profile) -> Profile:
    """Profiles are saved text that later reaches the AI: clean it, and refuse obvious injection attempts."""
    profile.notes = guards.clean_input(profile.notes)[:300]
    profile.display_name = guards.clean_input(profile.display_name)[:40]
    profile.home_town = guards.clean_input(profile.home_town)[:40]
    flags = guards.scan_for_injection(" ".join((profile.notes, profile.display_name, profile.home_town)))
    if flags:
        raise HTTPException(400, "Your profile note can only describe your group and needs. It looks like it "
                                 f"contains instructions for the AI ({flags[0]}).")
    return profile


@app.put("/api/profile", dependencies=[Depends(_json_only)])
def api_save_profile(profile: Profile, request: Request) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "Sign in to save your profile to your account.")
    accounts.save_profile(user[0], _checked(profile))
    return _me(user)


@app.post("/api/profile/check", dependencies=[Depends(_json_only)])
def api_check_profile(profile: Profile) -> dict:
    """Guests save their profile on their device only; this runs the same checks without storing anything."""
    return {"profile": _checked(profile).model_dump()}


@app.delete("/api/account")
def api_delete_account(request: Request, response: Response) -> dict:
    user = current_user(request)
    if not user:
        raise HTTPException(401, "You're not signed in.")
    accounts.delete_user(user[0])
    response.delete_cookie(COOKIE, path="/")
    return {"signed_in": False, "deleted": True}


# ---------- Search ----------

@app.post("/api/search")
async def api_search(body: SearchIn, request: Request) -> JSONResponse:
    profile = None
    if body.use_profile:
        user = current_user(request)
        profile = (accounts.get_profile(user[0]) if user else None) or body.profile
    try:
        return JSONResponse(await search(body.message, visitor=_visitor(request), profile=profile))
    except Blocked as e:
        return JSONResponse({"error": str(e), "blocked": True}, status_code=429)
    except ConfigError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    except ModelAuthenticationError:  # the AI key was rejected (expired or rotated); say so plainly, never show the key
        log.error("search failed: the AI provider rejected PORTKEY_API_KEY (expired or rotated)")
        return JSONResponse({"error": "The AI key for this site has expired, so searches are paused until it's renewed. "
                                      "The course map and everything else still work. To run TrueCourse yourself, see the README."},
                            status_code=503)
    except Exception:  # never send stack traces (or anything that might contain a key) to the browser
        log.exception("search failed")
        return JSONResponse({"error": "Something went wrong while searching. Please try again."}, status_code=500)


ACCESS_FIELDS = ("terrain", "walkability", "forced_carries", "bunkers", "cartpath_to_green_yd",
                 "cart_near_greens", "adaptive_cart")


@app.get("/api/courses")
def api_courses() -> list[dict]:
    """Every course, lightly: for the map and the course explorer. Full facts stay server-side."""
    out = []
    for c in load_courses().values():
        out.append({k: c.get(k) for k in ("course_id", "name", "town", "state", "lat", "lon", "access", "holes",
                                          "terrain", "price_tier", "map_detail", "total_climb_ft",
                                          "water_carry_holes", "verified_by_phone", "website")}
                   | {"known": sum(c.get(f) is not None for f in ACCESS_FIELDS), "of": len(ACCESS_FIELDS),
                      "amen": sorted((c.get("amenities") or {}).keys())
                              + (["adaptive"] if c.get("adaptive_equipment") else [])
                              + (["veterans"] if c.get("veterans_program") else [])})
    return out


@app.get("/api/stats")
def api_stats() -> dict:
    """Tokenomics from real runs in output/usage_log.json (used by the app's 'Cost' panel and the report)."""
    runs = [r for r in read_json(OUTPUT / "usage_log.json", []) if r.get("requests")]
    n = len(runs) or 1
    return {
        "runs": len(runs),
        "avg_input_tokens": round(sum(r["input_tokens"] for r in runs) / n),
        "avg_cached_input_tokens": round(sum(r["cached_input_tokens"] for r in runs) / n),
        "avg_output_tokens": round(sum(r["output_tokens"] for r in runs) / n),
        "avg_cost_usd": round(sum(r["cost_usd"] for r in runs) / n, 6),
        "analyst_cache_hit_rate": round(sum(r["analyst_cache_hits"] for r in runs)
                                        / max(sum(r["analyst_cache_hits"] + r["analyst_fresh"] for r in runs), 1), 3),
        "spent_today_usd": round(guards.spent_today_usd(), 4),
        "daily_budget_usd": DAILY_BUDGET_USD,
    }


if __name__ == "__main__":
    import uvicorn

    # reload=False so one edit never spawns a second server
    uvicorn.run(app, host="127.0.0.1", port=int(os.getenv("PORT", "8000")), reload=False)
