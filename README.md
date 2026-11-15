# CaddieSync

**An AI golf concierge that finds New England golf courses your whole group can actually play.**

Tell CaddieSync who is playing, what is hard for them, and where ("my 70-year-old dad has a bad knee and can't
walk steep hills, near New Haven", "a long weekend on Cape Cod with my wife, a beginner", "I play from a
wheelchair, anything near Burlington VT?"). It searches every mapped golf course in Connecticut, Rhode Island,
Massachusetts, Vermont, New Hampshire and Maine, using hills, water carries, bunkers and walking distances
**measured hole by hole** from public map and elevation data. Then three AI agents pick and explain the best fits,
with a booking link, directions, and a list of what to ask when you call.

- **Live app: https://caddiesync.onrender.com** (free hosting: the first visit after a quiet spell takes ~30 seconds to wake up)
- GitHub: https://github.com/tplansker/caddiesync
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

Open **http://127.0.0.1:8000**, click one of the example chips (e.g. "Dad's bad knee" or "Cape Cod trip"), then
**Find my courses**. A search takes 5-20 seconds. The course data is already built and committed; nothing needs
to be downloaded.

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
golfer's message (+ saved profile)
  -> [code]  Input guard: strip hidden characters, flag attack phrases
  -> [AI 1]  Profiler: message -> place, radius, trip or single round, price tier, physical needs (structured output)
  -> [code]  Saved-profile floor: saved needs can only get stricter
  -> [code]  Course Locator: courses near that town/region; pre-scored on measured facts; top ~10 go to the AI
  -> [AI 2]  Course Analyst: 0-100 accessibility score per course, cached per course + needs
  -> [code]  Fact check: drop claims about unknown course facts, cap scores when key facts are unknown
  -> [AI 3]  Concierge: picks the best 1-5 courses and explains them
  -> [code]  Pick check; every fact, price tier, distance and link is copied from the course database
```

The Course Locator is why cost stays flat: whether there are 15 courses or 900, only the most promising ~10 are
sent to the AI, and repeat questions come from the cache.

| File | What it does |
|---|---|
| `app.py` | FastAPI server and API (search, accounts, profile, courses, stats) |
| `caddiesync/agents.py` | The three PydanticAI agents |
| `caddiesync/prompts/` | Each agent's instructions |
| `caddiesync/pipeline.py` | Runs one search end to end; logs tokens and the trace |
| `caddiesync/locator.py` | Course Locator: place lookup, distance filter, rule-based pre-score |
| `caddiesync/tools.py` | Course database and accessibility cache |
| `caddiesync/guards.py` | Safety nets: injection scan, fact checks, rate limit, daily budget |
| `caddiesync/models.py` | Structured outputs for each agent, and the saved `Profile` |
| `caddiesync/accounts.py` | Free accounts, sessions and saved profiles (SQLite) |
| `data/courses.json` | Every mapped New England course with measured accessibility facts (built, committed) |
| `data/places.json` | Towns and villages for location search |
| `data/featured.json` | Hand-researched facts for featured courses: price tiers, booking links, phone, sources |
| `scripts/build_courses.py`, `build_measure.py` | Rebuild the course data (only needed to refresh it) |
| `static/` | The web page |
| `tests/attack_test.py` | Runs 6 prompt-injection attacks (one hidden in a saved profile) and checks every defense held |
| `tests/cache_test.py` | Same searches with the cache cold vs warm: tokens, cost, same answers |

## The course data, honestly

- **Measured, not guessed.** `scripts/build_courses.py` downloads every golf course in New England from
  OpenStreetMap, where volunteers have drawn most courses hole by hole (tee-to-green lines, greens, bunkers,
  water hazards, cart paths). For each hole it samples USGS 3DEP elevation at six points and measures climbing,
  the steepest stretch (compared with the 8.33% ADA limit for a wheelchair ramp), shots that cross water,
  bunkers within 25 m of the green, and the walk to the next tee. Course labels (flat / gently rolling / hilly /
  very hilly, etc.) come from those numbers with fixed thresholds in `scripts/build_measure.py`.
- **Coverage (current build).** 851 courses across six states; 808 measured hole by hole (12,561 holes).
- **On-course extras.** Driving ranges (marked covered/indoor when tagged), practice greens, simulators / indoor
  golf, pro shops, and restaurants, bars, cafés and snack bars on or next to each course, from OpenStreetMap
  (`python scripts/build_courses.py amenities`). Ranges are well mapped (372 courses); food and pro shops much less
  so. A missing extra means "not on the map", so the app says "ask" and adds it to the call list instead of
  claiming the course has none. Extras give a small ranking boost only when accessibility is close.
- **Islands.** Courses on Martha's Vineyard, Nantucket, Block Island and Maine islands are flagged "ferry needed".
- **Limits.** Map quality varies: courses drawn only as an outline get no measurements and are marked "not
  measured". Bunker depth and course rules (cart access near greens, adaptive carts) aren't on any map, so the
  app tells you what to ask. "No water" is only claimed for courses mapped in detail.
- **Prices and booking.** Price tiers ($ under ~$40, $$ ~$40-75, $$$ ~$75-120, $$$$ over $120) are researched for
  featured courses (`data/featured.json`, with sources) and shown as "not listed" elsewhere. CaddieSync links to
  each course's own booking page or a tee-time search instead of guessing availability.
- **Credits.** Course maps (c) OpenStreetMap contributors, ODbL. Elevation: USGS 3D Elevation Program (public
  domain), via OpenTopoData. The app never calls these services at runtime; the data is built once.

To refresh the data (not needed to run the app): `pip install -r scripts/requirements-build.txt`, then
`python scripts/build_courses.py fetch`, `... elevation`, `... amenities`, `... build`. Downloads are cached in `data/raw/`.

## Free profiles

Anyone can create a free account (username + password, no email) and save a golfer profile: home town, how far
they'll drive, usual group, price range and physical needs. Every search starts from it, so it's the same each
time you sign in. People who don't want an account can save a profile on their device only.

- Passwords are hashed with scrypt (Python standard library); sessions are HttpOnly, SameSite cookies.
- A saved profile's needs are a **floor**: a vague message ("somewhere near home, 2 of us") can never drop "no
  steep hills" or "adaptive cart". A switch next to the search box turns the profile off for a different group.
- Profile notes reach the AI, so they're checked for prompt injection when saved and again on every search.
- Users can delete their account and all saved data from the profile screen. Signing out clears the device copy.
- Accounts live in SQLite at `instance/caddiesync.db` (set `CADDIESYNC_DB` to move it). On Render's free plan this
  file is erased on redeploy; the browser keeps a copy of the profile, and creating the account again with the
  same name restores it.

## Logs

Run logs go to `output/usage_log.json` (token counts and cost per call) and `output/audit_trail.json` (every step of
every search). The accessibility cache is `cache/accessibility_cache.json`.

## Deploy on Render

`render.yaml` is a Render Blueprint. New -> Blueprint -> pick this repo, then paste `PORTKEY_API_KEY` when
asked. Free instances sleep when idle, so the first search after a while can take ~30 seconds longer.
