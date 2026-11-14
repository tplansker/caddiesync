"""CaddieSync web app: FastAPI backend + the single-page UI in static/.

Run locally:   python app.py          (then open http://127.0.0.1:8000)
On Render:     uvicorn app:app --host 0.0.0.0 --port $PORT
"""

from __future__ import annotations

import logging
import os

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from caddiesync import guards
from caddiesync.config import DAILY_BUDGET_USD, MODEL_NAME, OUTPUT, ROOT, ConfigError
from caddiesync.pipeline import Blocked, search
from caddiesync.tools import join_waitlist, load_courses, read_json

log = logging.getLogger("caddiesync")
app = FastAPI(title="CaddieSync", docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


class SearchIn(BaseModel):
    message: str = Field(max_length=4_000)


class WaitlistIn(BaseModel):
    slot_id: str = Field(max_length=80)
    players: int = Field(ge=1, le=4)


def _visitor(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() or (request.client.host if request.client else "unknown")


@app.get("/")
def index() -> FileResponse:
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "model": MODEL_NAME, "courses": len(load_courses())}


@app.post("/api/search")
async def api_search(body: SearchIn, request: Request) -> JSONResponse:
    try:
        return JSONResponse(await search(body.message, visitor=_visitor(request)))
    except Blocked as e:
        return JSONResponse({"error": str(e), "blocked": True}, status_code=429)
    except ConfigError as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    except Exception:  # never send stack traces (or anything that might contain a key) to the browser
        log.exception("search failed")
        return JSONResponse({"error": "Something went wrong while searching. Please try again."}, status_code=500)


@app.post("/api/waitlist")
def api_waitlist(body: WaitlistIn) -> JSONResponse:
    try:
        return JSONResponse(join_waitlist(body.slot_id, body.players))
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)


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
