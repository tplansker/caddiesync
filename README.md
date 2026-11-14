# CaddieSync

**An AI golf concierge that finds tee times your whole group can actually play.**

Tell CaddieSync who is playing and what is hard for them ("my 70-year-old dad has a bad knee and can't
walk steep hills", "I play from a wheelchair with a SoloRider"). Three AI agents turn that into physical
needs, score every Connecticut course near New Haven for accessibility, and recommend open tee times that fit.

- Online app: _(Render link goes here once deployed)_
- Course: MGT 409 AI Foundations for Managers, final project

## Run it locally (about 3 minutes)

You need Python 3.11+ and a `.env` file with `PORTKEY_API_KEY`. The app looks for `.env` in this folder
first, then **one folder above** this repo, so the teaching staff's shared `.env` works without changes.

```bash
git clone <this repo> caddiesync
cd caddiesync
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Open **http://127.0.0.1:8000** and click one of the example chips (e.g. "Dad's bad knee"), then
**Find my tee time**. A search takes 5-20 seconds.

If port 8000 is busy: `PORT=8050 python app.py` (Windows PowerShell: `$env:PORT=8050; python app.py`).

### Keys

| Variable | Needed | Notes |
|---|---|---|
| `PORTKEY_API_KEY` | yes | Course key; every AI call is `gpt-6-luna` through Portkey |
| `OPENAI_API_KEY` | no | Only used if `PORTKEY_API_KEY` is missing (direct OpenAI) |
| `OPENAI_MODEL` | no | Defaults to `gpt-6-luna` |
| `DAILY_BUDGET_USD` | no | Spending cap per day (default $2.00) |

See `.env.example`. Never commit a real `.env`.

## How it works

```
golfer's message
  -> [code]  Input guard: strip hidden characters, flag attack phrases
  -> [AI 1]  Profiler: message -> date, time, players, budget, physical needs (structured output)
  -> [code]  Tee-Time Sniper: open tee times that fit (prices come ONLY from here)
  -> [AI 2]  Course Analyst: 0-100 accessibility score per course, cached per course + needs
  -> [code]  Fact check: drop claims about unknown course facts, cap scores when key facts are unknown
  -> [AI 3]  Concierge: picks the best 1-3 and explains them
  -> [code]  Tee-time check: drop any pick that is not a real tee time
```

| File | What it does |
|---|---|
| `app.py` | FastAPI server and API (`/api/search`, `/api/waitlist`, `/api/stats`) |
| `caddiesync/agents.py` | The three PydanticAI agents |
| `caddiesync/prompts/` | Each agent's instructions |
| `caddiesync/pipeline.py` | Runs one search end to end; logs tokens and the trace |
| `caddiesync/tools.py` | Course database, Tee-Time Sniper, accessibility cache |
| `caddiesync/guards.py` | Safety nets: injection scan, fact checks, rate limit, daily budget |
| `caddiesync/models.py` | Structured outputs for each agent |
| `data/courses.json` | 15 real public courses near New Haven, with sources |
| `static/` | The web page |
| `tests/attack_test.py` | Runs 5 prompt-injection attacks and checks every defense held |
| `tests/cache_test.py` | Same searches with the cache cold vs warm: tokens, cost, same answers |

## Data, honestly

- **Course facts** (holes, par, yardage, terrain, hazards) come from public listings and reviews, cited in
  `sources` for each course. Anything the sources don't agree on or don't say is `null` (unknown), and the
  app tells the golfer to confirm it rather than guessing.
- **Tee times and prices are simulated.** These courses don't offer a public tee-time API, so
  `tools.tee_sheet()` generates a repeatable tee sheet for each course and day.
- Run logs go to `output/usage_log.json` (token counts and cost per call) and `output/audit_trail.json`
  (every step of every search). The accessibility cache is `cache/accessibility_cache.json`.

## Deploy on Render

`render.yaml` is a Render Blueprint. New -> Blueprint -> pick this repo, then paste `PORTKEY_API_KEY` when
asked. Free instances sleep when idle, so the first search after a while can take ~30 seconds longer.
