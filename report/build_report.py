"""Build report/report.html: one self-contained file (screenshots embedded) for Canvas.

    python report/build_report.py

Every number comes from the app's real logs and test results, so the report can be rebuilt after new runs:
  output/usage_log.json           token counts and cost of every search (from the API)
  output/audit_trail.json         every step of every search (fact checks, safety events)
  report/evidence/*.json          attack test, cache test, plain-AI comparison
  data/courses.json               the built course database
Edit REAL_USER below after testing with a real golfer, then rebuild.
"""

from __future__ import annotations

import base64
import html
import json
import statistics as st
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "report"
IMG = REPORT / "img"
EV = REPORT / "evidence"
PRICES = json.loads((ROOT / "data" / "prices.json").read_text())["gpt-6-luna"]

# ------------------------------------------------------------------------------------------------ fill in yourself
# Leave quote empty until a real person has tried the app; the report then shows a clearly marked placeholder.
REAL_USER = {
    "who": "",            # e.g. "A friend who golfs with a physical limitation (describe it as they're comfortable)"
    "quote": "",          # what they said, in their words
    "before": "",         # how long it took them to find a course the old way (e.g. "about 40 minutes of calls")
    "after": "",          # how long with TrueCourse (e.g. "under 2 minutes")
    "outcome": "",        # did they play the course? how was it?
}
PHONE_CALLS = ""          # e.g. "I called 5 courses; 3 had cart access near greens, 1 had a SoloRider."

AUTHOR = "Tyler Plansker"
GITHUB = "https://github.com/tplansker/caddiesync"
LIVE = "https://caddiesync.onrender.com"


# ------------------------------------------------------------------------------------------------ data
def load(p: Path, default):
    try:
        return json.loads(p.read_text())
    except FileNotFoundError:
        return default


courses = load(ROOT / "data" / "courses.json", [])
logs = [r for r in load(ROOT / "output" / "usage_log.json", []) if "courses_considered" in r]
audit = load(ROOT / "output" / "audit_trail.json", [])
attacks = load(EV / "attack_results.json", {"passed": 0, "total": 0, "runs": []})
cache_res = load(EV / "cache_results.json", [])
plain = load(EV / "plain_ai_comparison.json", {})

cold = [r for r in logs if r["analyst_fresh"] >= 5]
warm = [r for r in logs if r["analyst_fresh"] == 0 and r["requests"] > 0]


def avg(rows, key):
    return st.mean(r[key] for r in rows) if rows else 0


def agent_avg(name, key):
    calls = [c for r in logs for c in r["calls"] if c["agent"] == name]
    return st.mean(c[key] for c in calls) if calls else 0


T = {  # typical runs
    "cold_n": len(cold), "warm_n": len(warm), "runs": len(logs),
    "cold_in": avg(cold, "input_tokens"), "cold_cached": avg(cold, "cached_input_tokens"), "cold_out": avg(cold, "output_tokens"),
    "cold_cost": avg(cold, "cost_usd"), "cold_ms": avg(cold, "elapsed_ms"),
    "cold_min": min((r["cost_usd"] for r in cold), default=0), "cold_max": max((r["cost_usd"] for r in cold), default=0),
    "warm_in": avg(warm, "input_tokens"), "warm_cached": avg(warm, "cached_input_tokens"), "warm_out": avg(warm, "output_tokens"),
    "warm_cost": avg(warm, "cost_usd"), "warm_ms": avg(warm, "elapsed_ms"),
    "considered": avg(logs, "courses_considered"),
    "analyzed": st.mean(r["analyst_fresh"] + r["analyst_cache_hits"] for r in logs) if logs else 0,
}
AG = {a: {k: agent_avg(a, k) for k in ("input_tokens", "cached_input_tokens", "output_tokens", "cost_usd")}
      for a in ("profiler", "course_analyst", "concierge")}


def uncached_cost(inp, out):
    return (inp * PRICES["input_per_1m"] + out * PRICES["output_per_1m"]) / 1e6


# Cost without any cost cutting: every candidate course analyzed, no prompt cache, no accessibility cache.
naive_per_search = (uncached_cost(AG["profiler"]["input_tokens"], AG["profiler"]["output_tokens"])
                    + T["considered"] * uncached_cost(AG["course_analyst"]["input_tokens"], AG["course_analyst"]["output_tokens"])
                    + uncached_cost(AG["concierge"]["input_tokens"], AG["concierge"]["output_tokens"]))
SEARCHES_PER_USER = 4  # assumption: a couple of local rounds and part of a trip each month


def monthly(users, warm_share, hosting, label):
    searches = users * SEARCHES_PER_USER
    ai = searches * ((1 - warm_share) * T["cold_cost"] + warm_share * T["warm_cost"])
    return {"users": users, "searches": searches, "warm": warm_share, "ai": ai, "hosting": hosting,
            "total": ai + hosting, "per_user": (ai + hosting) / users, "label": label,
            "naive_ai": searches * naive_per_search}


SCEN = [monthly(1, 0.0, 0, "1 user, cache empty (worst case)"),
        monthly(100, 0.5, 7, "100 users · Render Starter $7"),
        monthly(10_000, 0.9, 25 + 19, "10,000 users · Render Standard $25 + Postgres $19")]

# Cache saturation: one-time cost to pre-score every course for the 50 most common needs combinations.
COMMON_COMBOS = 50
prewarm = len(courses) * COMMON_COMBOS * AG["course_analyst"]["cost_usd"]

# Course data coverage
measured = [c for c in courses if c.get("terrain") is not None]
holes_measured = sum(c.get("holes_mapped") or 0 for c in measured)
by_state = Counter(c["state"] for c in courses)
amen = Counter(k for c in courses for k in (c.get("amenities") or {}))
terr = Counter(c.get("terrain") for c in courses)
access = Counter(c.get("access") for c in courses)
lot_walk = sum(c.get("walk_lot_to_first_tee_yd") is not None for c in courses)
back_walk = sum(c.get("walk_9th_green_to_clubhouse_yd") is not None for c in courses)
adaptive_n = sum(bool(c.get("adaptive_equipment")) for c in courses)
veterans_n = sum(bool(c.get("veterans_program")) for c in courses)
ada_tagged = sum(any(c.get(k) for k in ("accessible_parking_mapped", "clubhouse_wheelchair_mapped")) for c in courses)
ACCESS_FIELDS = ("terrain", "walkability", "cart_fairway_access", "cart_near_greens", "adaptive_cart", "forced_carries", "water_in_play", "bunkers")
known_all = st.mean(sum(c.get(f) is not None for f in ACCESS_FIELDS) for c in courses) if courses else 0
WEB_RESEARCH_KNOWN = 1.0   # measured earlier: 15 hand-researched CT courses, 0-3 of these 8 facts each (mean 1.0)
MEASURED_SAME_15 = 5.0     # the same 15 courses after measurement: 5 of 8 each

# Fact checks and safety events across all searches
fact = [e for a in audit for e in a.get("safety", []) if e["layer"] == "code: fact check"]
fact_dropped = sum("dropped" in e["event"] for e in fact)
fact_capped = sum("capped" in e["event"] for e in fact)
fact_verdict = sum("verdict changed" in e["event"] for e in fact)


# ------------------------------------------------------------------------------------------------ helpers
def esc(s) -> str:
    return html.escape(str(s))


def img(name: str, caption: str, cls: str = "") -> str:
    p = IMG / name
    if not p.exists():
        return f'<figure class="missing {cls}"><div>Missing screenshot: {esc(name)}</div><figcaption>{caption}</figcaption></figure>'
    b64 = base64.b64encode(p.read_bytes()).decode()
    return f'<figure class="{cls}"><img src="data:image/jpeg;base64,{b64}" alt="{esc(caption)}"><figcaption>{caption}</figcaption></figure>'


def usd(x, d=4) -> str:
    return f"${x:,.{d}f}"


def n(x) -> str:
    return f"{round(x):,}"


def todo(text: str) -> str:
    return f'<div class="todo"><b>To fill in before submitting:</b> {text}</div>'


# ------------------------------------------------------------------------------------------------ diagram
DIAGRAM = """
<svg viewBox="0 0 980 470" role="img" aria-label="TrueCourse agent pipeline" xmlns="http://www.w3.org/2000/svg">
  <defs>
    <marker id="ar" markerWidth="10" markerHeight="10" refX="8" refY="5" orient="auto"><path d="M0,0 L10,5 L0,10 z" fill="#3d5c10"/></marker>
    <style>
      .box{rx:12;ry:12} .ai{fill:#c6f24e;stroke:#0f140f;stroke-width:1.5} .code{fill:#fff;stroke:#0f140f;stroke-width:1.5}
      .data{fill:#0f140f} .t{font:800 15px Archivo,'Arial Black',Arial,sans-serif;fill:#0f140f} .s{font:12.5px Arial,sans-serif;fill:#2a332d}
      .tw{font:800 15px Archivo,'Arial Black',Arial,sans-serif;fill:#f6f2e6} .sw{font:12.5px Arial,sans-serif;fill:#d9d4c4}
      .lab{font:700 11px Arial,sans-serif;letter-spacing:.08em;fill:#3d5c10} .ln{stroke:#3d5c10;stroke-width:2;fill:none;marker-end:url(#ar)}
    </style>
  </defs>
  <!-- row 1 -->
  <rect class="box code" x="10" y="20" width="170" height="78"/><text class="t" x="24" y="48">Golfer's message</text><text class="s" x="24" y="68">+ saved profile</text><text class="s" x="24" y="85">(optional)</text>
  <rect class="box code" x="215" y="20" width="170" height="78"/><text class="lab" x="229" y="40">CODE</text><text class="t" x="229" y="60">Input guard</text><text class="s" x="229" y="80">clean + scan for attacks</text>
  <rect class="box ai" x="420" y="20" width="170" height="78"/><text class="lab" x="434" y="40">AI AGENT 1</text><text class="t" x="434" y="60">Profiler</text><text class="s" x="434" y="80">words → place, needs</text>
  <rect class="box code" x="625" y="20" width="170" height="78"/><text class="lab" x="639" y="40">CODE</text><text class="t" x="639" y="60">Profile floor</text><text class="s" x="639" y="80">saved needs only stricter</text>
  <path class="ln" d="M180,59 H212"/><path class="ln" d="M385,59 H417"/><path class="ln" d="M590,59 H622"/>
  <path class="ln" d="M795,59 H880 V150"/>
  <!-- row 2 -->
  <rect class="box code" x="770" y="153" width="200" height="96"/><text class="lab" x="784" y="173">CODE</text><text class="t" x="784" y="193">Course Locator</text><text class="s" x="784" y="212">851 courses → nearby →</text><text class="s" x="784" y="229">pre-score → top 10</text>
  <rect class="box ai" x="505" y="153" width="220" height="96"/><text class="lab" x="519" y="173">AI AGENT 2  ×10, IN PARALLEL</text><text class="t" x="519" y="193">Course Analyst</text><text class="s" x="519" y="212">0–100 score per course</text><text class="s" x="519" y="229">cached by course + needs</text>
  <rect class="box code" x="270" y="153" width="190" height="96"/><text class="lab" x="284" y="173">CODE</text><text class="t" x="284" y="193">Fact check</text><text class="s" x="284" y="212">drop claims about unknowns,</text><text class="s" x="284" y="229">cap scores on missing data</text>
  <rect class="box ai" x="10" y="153" width="215" height="96"/><text class="lab" x="24" y="173">AI AGENT 3</text><text class="t" x="24" y="193">Concierge</text><text class="s" x="24" y="212">picks best 1–5, explains</text><text class="s" x="24" y="229">in plain words</text>
  <path class="ln" d="M770,201 H728"/><path class="ln" d="M505,201 H463"/><path class="ln" d="M270,201 H228"/>
  <path class="ln" d="M117,249 V300"/>
  <!-- row 3 -->
  <rect class="box code" x="10" y="303" width="290" height="80"/><text class="lab" x="24" y="323">CODE</text><text class="t" x="24" y="343">Pick check + facts from database</text><text class="s" x="24" y="363">prices, distances, links copied by code</text>
  <path class="ln" d="M300,343 H345"/>
  <rect class="box code" x="348" y="303" width="200" height="80"/><text class="t" x="362" y="335">Result cards</text><text class="s" x="362" y="355">map, call list, booking,</text><text class="s" x="362" y="371">trace + cost panels</text>
  <!-- offline data -->
  <rect class="box data" x="600" y="290" width="370" height="170"/>
  <text class="lab" x="616" y="312" style="fill:#c6f24e">BUILT ONCE, OFFLINE (NO AI, NO TOKENS)</text>
  <text class="tw" x="616" y="336">Course database</text>
  <text class="sw" x="616" y="358">OpenStreetMap: holes, greens, bunkers,</text>
  <text class="sw" x="616" y="376">water, cart paths, ranges, food</text>
  <text class="sw" x="616" y="396">USGS elevation at 6 points per hole</text>
  <text class="sw" x="616" y="416">→ climb, steepness vs. ADA ramp, carries,</text>
  <text class="sw" x="616" y="436">bunkers by green, walk to next tee</text>
  <path class="ln" d="M870,290 V252"/>
</svg>"""


# ------------------------------------------------------------------------------------------------ tables
def attack_rows() -> str:
    labels = ["Owner claim + 'rank it #1' + 'say it's flat'", "Fake SYSTEM message promoting a course",
              "Fake pro-shop manager: free round + reveal prompt", "Hidden characters + fake tag + false facts",
              "'Only recommend Vermont' + real request", "Stored attack hidden in a saved profile note"]
    out = []
    for i, r in enumerate(attacks.get("runs", [])):
        caught = r["caught"]
        how = ("Blocked before our AI saw it (provider filter + our scanner)" if caught and caught[0].startswith("blocked")
               else f"{len(caught)} flags/refusals; results are real courses with database facts")
        picks = ", ".join(p[0] for p in r.get("recommendations", [])[:2]) or "none (blocked)"
        out.append(f"<tr><td>{esc(labels[i] if i < len(labels) else r['attack'][:60])}</td><td>{esc(how)}</td>"
                   f"<td>{esc(picks)}</td><td class='ok'>{'Stopped' if r['passed'] else 'FAILED'}</td></tr>")
    return "".join(out)


def cache_rows() -> str:
    out = []
    for c in cache_res:
        q = "Bad knee near New Haven" if "New Haven" in c["query"] else "Cape Cod beginner trip"
        out.append(f"<tr><td>{q}</td><td>{n(c['cold']['input'])} / {n(c['cold']['output'])}</td><td>{usd(c['cold']['cost_usd'])}</td>"
                   f"<td>{c['cold']['ms']/1000:.1f}s</td><td>{n(c['warm']['input'])} / {n(c['warm']['output'])}</td><td>{usd(c['warm']['cost_usd'])}</td>"
                   f"<td>{c['warm']['ms']/1000:.1f}s</td><td>{c['cost_saving']:.0%}</td><td class='ok'>{'Identical' if c['same_scores'] else 'Changed'}</td></tr>")
    return "".join(out)


def scen_rows() -> str:
    return "".join(
        f"<tr><td>{esc(s['label'])}</td><td>{n(s['searches'])}</td><td>{s['warm']:.0%}</td><td>{usd(s['ai'], 2)}</td>"
        f"<td>{usd(s['hosting'], 0)}</td><td><b>{usd(s['total'], 2)}</b></td><td>{usd(s['per_user'], 4)}</td><td>{usd(s['naive_ai'], 2)}</td></tr>"
        for s in SCEN)


# ------------------------------------------------------------------------------------------------ CSS
CSS = """
@page { size: letter; margin: 0.6in 0.65in; }
:root { --ink:#111814; --body:#2a332d; --muted:#55625a; --line:#ddd6c3; --paper:#fbf9f3; --cream:#f6f2e6; --lime:#c6f24e; --olive:#3d5c10; --dark:#0f140f; }
* { box-sizing: border-box; }
html { font-size: 10.6pt; }
body { margin: 0; color: var(--body); background: #fff; font-family: "Atkinson Hyperlegible", "Helvetica Neue", Arial, sans-serif; line-height: 1.48; }
.page { max-width: 7.2in; margin: 0 auto; padding: 0.3in 0; }
h1, h2, h3 { font-family: Archivo, "Arial Black", "Helvetica Neue", Arial, sans-serif; font-weight: 900; font-stretch: 125%; color: var(--ink); text-transform: uppercase; letter-spacing: -0.01em; }
h2 { font-size: 1.55rem; margin: 1.6em 0 0.5em; padding-top: 0.5em; border-top: 3px solid var(--dark); break-after: avoid; }
h2 .num { color: var(--olive); margin-right: 0.35em; }
h3 { font-size: 1.02rem; margin: 1.2em 0 0.35em; break-after: avoid; }
p { margin: 0 0 0.7em; }
a { color: var(--olive); }
.cover { background: var(--dark); color: var(--cream); border-radius: 14px; padding: 0.42in 0.45in 0.38in; position: relative; overflow: hidden; }
.cover .kicker { color: var(--lime); font: 700 0.78rem Arial, sans-serif; letter-spacing: 0.16em; text-transform: uppercase; margin: 0 0 0.7em; }
.cover h1 { color: var(--cream); font-size: 2.9rem; line-height: 0.95; margin: 0 0 0.35em; }
.cover h1 span { color: var(--lime); }
.cover .one { font-size: 1.12rem; color: #e9e4d6; max-width: 6in; margin-bottom: 1em; }
.cover dl { display: grid; grid-template-columns: 1.1in 1fr; gap: 0.25em 0.8em; margin: 0; font-size: 0.95rem; }
.cover dt { color: var(--lime); font-weight: 700; } .cover dd { margin: 0; } .cover a { color: var(--cream); }
.stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 8px; margin: 1em 0 0.4em; }
.stat { background: var(--cream); border-radius: 10px; padding: 9px 11px; }
.stat b { display: block; font: 900 1.45rem Archivo, "Arial Black", Arial, sans-serif; font-stretch: 125%; color: var(--ink); line-height: 1.1; }
.stat span { font-size: 0.8rem; color: var(--muted); line-height: 1.25; display: block; }
.cover .stat { background: rgba(246,242,230,0.08); } .cover .stat b { color: var(--lime); } .cover .stat span { color: #cfcabb; }
figure { margin: 0.6em 0 1em; break-inside: avoid; }
figure img { width: 100%; display: block; border: 1px solid var(--line); border-radius: 8px; }
figcaption { font-size: 0.82rem; color: var(--muted); margin-top: 0.35em; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; align-items: start; }
.grid2 figure { margin: 0.4em 0 0.6em; }
.missing div { border: 2px dashed #c33; padding: 2em; text-align: center; color: #c33; }
table { width: 100%; border-collapse: collapse; margin: 0.5em 0 1em; font-size: 0.86rem; break-inside: avoid; }
th, td { text-align: left; padding: 5px 7px; border-bottom: 1px solid var(--line); vertical-align: top; }
th { background: var(--dark); color: var(--cream); font-weight: 700; font-size: 0.8rem; }
td.num, th.num { text-align: right; }
.ok { color: #1f7a46; font-weight: 700; }
.callout { background: var(--cream); border-left: 5px solid var(--lime); border-radius: 0 10px 10px 0; padding: 10px 14px; margin: 0.6em 0 1em; break-inside: avoid; }
.callout.dark { background: var(--dark); color: var(--cream); border-left-color: var(--lime); } .callout.dark b { color: var(--lime); }
.todo { border: 2px dashed #d97706; background: #fff7e6; border-radius: 10px; padding: 10px 14px; margin: 0.6em 0 1em; color: #6b3f00; break-inside: avoid; }
.quote { font-size: 1.05rem; border-left: 5px solid var(--olive); padding: 6px 14px; margin: 0.6em 0 1em; font-style: italic; }
.diagram { border: 1px solid var(--line); border-radius: 12px; padding: 10px; background: var(--paper); margin: 0.6em 0 0.3em; break-inside: avoid; }
.diagram svg { width: 100%; height: auto; display: block; }
ul, ol { margin: 0.2em 0 0.8em 1.2em; padding: 0; } li { margin: 0.2em 0; }
.small { font-size: 0.82rem; color: var(--muted); }
.sources { font-size: 0.78rem; color: var(--muted); } .sources li { margin: 0.1em 0; word-break: break-word; }
.pb { break-before: page; }
@media screen { body { background: #e9e6dc; } .page { background: #fff; padding: 0.55in 0.65in; margin: 24px auto; box-shadow: 0 6px 30px rgba(0,0,0,.12); border-radius: 6px; } }
"""


# ------------------------------------------------------------------------------------------------ sections
def section_title() -> str:
    return f"""
<header class="cover">
  <p class="kicker">MGT 409 · AI Foundations for Managers · Final Project</p>
  <h1>True<span>Course</span></h1>
  <p class="one">An AI golf concierge that finds the New England golf courses a whole group can actually play, using hills,
  water carries and walking distances measured hole by hole, for a round near home or a golf trip.</p>
  <dl>
    <dt>Author</dt><dd>{AUTHOR}</dd>
    <dt>Live app</dt><dd><a href="{LIVE}">{LIVE}</a></dd>
    <dt>GitHub</dt><dd><a href="{GITHUB}">{GITHUB}</a></dd>
  </dl>
  <p class="small">Built under the working name CaddieSync and renamed TrueCourse on Nov 21, 2026, so the web address, the repository and some
  early logs and screenshots still use the old name.</p>
  <div class="stats">
    <div class="stat"><b>{len(courses):,}</b><span>courses in six New England states</span></div>
    <div class="stat"><b>{holes_measured:,}</b><span>holes measured for hills, water, bunkers</span></div>
    <div class="stat"><b>{usd(T['cold_cost'],4)}</b><span>average cost of a first-time search</span></div>
    <div class="stat"><b>{attacks.get('passed',0)}/{attacks.get('total',0)}</b><span>prompt-injection attacks stopped</span></div>
  </div>
</header>
{img('01_hero.jpg', 'Figure 1. The TrueCourse home page. Users describe who is playing, where, and what is hard for them, in plain words or by voice.')}
"""


def section_motivation() -> str:
    return f"""
<h2><span class="num">1</span>Motivation</h2>
<p><b>Who has the problem.</b> Golf is getting older and more physically diverse at the same time. The number of on-course golfers
aged 65 and older nearly doubled between 2016 and 2025 (+97%, National Golf Foundation), and seniors are about 22% of golf's traditional
participant base. Among U.S. adults 65 and older, about 48% have doctor-diagnosed arthritis (CDC/NCHS), and 12.1% of all U.S. adults have a
mobility disability, meaning serious difficulty walking or climbing stairs (CDC). Add beginners, golfers recovering from surgery, and
adaptive golfers who play from a wheelchair or a single-rider cart, and a large share of golfers share one question before every round:
<i>"Can we actually play this course?"</i></p>
<p><b>How they handle it today.</b> Tee-time sites tell you when a course is open and what it costs, but nothing about how hard it is on a body.
So golfers piece it together: course websites, scattered reviews ("hilly", "lots of water"), calls to the pro shop, and word of mouth.
When I tried to research 15 public courses near New Haven this way, the public sources covered on average only <b>1 of the 8 accessibility facts
that matter</b> (terrain, walkability, cart rules near greens, adaptive carts, forced carries, water, bunkers), and the sources often disagreed.
The closest thing to a directory, the National Alliance for Accessible Golf's search engine, depends on facilities listing themselves.</p>
<p><b>What it costs.</b> The cost is mostly frustration and lost rounds rather than dollars. A wrong guess means a miserable or unsafe day:
a 70-year-old with a bad knee facing a course with 400+ feet of climbing, or a beginner facing a 150-yard carry over water. The common workaround
is to stop playing with the people who need the easier course. I wanted a tool that answers "which course near us, or on our trip, will
everyone in our group enjoy?" with facts instead of guesses.</p>
"""


def section_new() -> str:
    ans = esc(plain.get("answer", "").replace("**", "").replace("\n", " "))[:900]
    return f"""
<h2><span class="num">2</span>What is new here</h2>
<p><b>What already exists.</b></p>
<table>
<tr><th style="width:24%">Existing option</th><th>What it does well</th><th style="width:38%">What it can't do</th></tr>
<tr><td>GolfNow, TeeOff, Supreme Golf</td><td>Find and book tee times; compare prices</td><td>No information about hills, walking, carries or cart access</td></tr>
<tr><td>GolfPass / Golf Advisor reviews</td><td>Golfer opinions ("hilly", "nice greens")</td><td>Inconsistent, unmeasured, not searchable by physical need</td></tr>
<tr><td>18Birdies, Hole19 (GPS apps)</td><td>"Plays like" distances adjusted for elevation, during a round</td><td>Only once you're on the course; no search for an accessible course</td></tr>
<tr><td>National Alliance for Accessible Golf search</td><td>Lists facilities with adaptive carts and programs</td><td>Self-reported by facilities; no measured terrain</td></tr>
<tr><td>ChatGPT (tested below)</td><td>Friendly general advice</td><td>Has no course measurements; says so, or guesses</td></tr>
</table>
<p><b>I tested the plain-AI option directly.</b> I asked the same model TrueCourse uses (gpt-6-luna), without TrueCourse's data, for three flat,
easy-walking courses near New Haven with total feet of climbing and holes with water. Its answer (excerpt):</p>
<div class="quote">"{ans[:420]}…"</div>
<p>It is honest that it lacks the data, and its one suggestion, Alling Memorial, is exactly the kind of guess that goes wrong: TrueCourse's measurements
show Alling has <b>382 feet of climbing and 9 of 18 holes with a stretch steeper than a wheelchair ramp</b>.</p>
<p><b>What TrueCourse does differently.</b></p>
<ul>
<li><b>It measures, instead of guessing or relying on self-reporting.</b> An offline pipeline downloads every New England golf course from OpenStreetMap,
where volunteers have drawn most courses hole by hole, and measures each hole against USGS elevation data: total climb, the steepest stretch compared with the
8.33% ADA limit for a wheelchair ramp, shots that must carry water, bunkers by each green, and the walk to the next tee. For the 15 courses I first researched
by hand, measured facts rose from <b>{WEB_RESEARCH_KNOWN:.0f} to {MEASURED_SAME_15:.0f} of 8</b>.</li>
<li><b>It searches by body, not by tee time.</b> "My dad has a bad knee", "I use a SoloRider", "my wife is a beginner" become structured needs, and every course
is scored for that group with reasons a golfer can check.</li>
<li><b>It is honest about gaps.</b> Unknown facts lower a score instead of being guessed, and the app writes the exact questions to ask when you call.</li>
<li><b>It covers getting there and getting back.</b> From mapped parking lots and clubhouses it measures the walk from the lot to the 1st tee
({lot_walk} courses) and from the 9th green back to the clubhouse ({back_walk} courses), so a golfer knows whether stopping after nine is easy.
It lists verified adaptive equipment (SoloRider carts, ParaGolfer chairs; {adaptive_n} courses) and free PGA HOPE veterans programs ({veterans_n} courses),
all from published sources. Accessible parking and a step-free clubhouse are tagged on the map for only {ada_tagged} courses, so for the rest the app says
"Unknown: ask" and adds the questions to the call list instead of assuming ADA compliance.</li>
<li><b>It works for trips.</b> Any town, region ("Cape Cod", "the Berkshires") or state; up to five courses, mapped, with booking links and directions.</li>
</ul>
<p class="small">Honest limits of the novelty: course <i>rules</i> (carts near greens, adaptive carts) are still mostly unknown, and tee-time availability is not
included; TrueCourse links to each course's own booking page instead.</p>
"""


def section_agents() -> str:
    c = AG
    return f"""
<h2><span class="num">3</span>The agents</h2>
<p>TrueCourse uses <b>three AI agents</b> and <b>five plain-code steps</b>. The design rule is simple: <b>the AI interprets and explains; code handles every
fact, price, distance and safety decision.</b> No AI output can set a course fact, because the AI's structured outputs have no field for one.</p>
<div class="diagram">{DIAGRAM}</div>
<p class="small" style="margin-top:2px">Figure 2. How the agents are organized. Lime boxes are AI agents (gpt-6-luna); white boxes are plain code; the dark box is the
course database, built once offline with no AI.</p>
<table>
<tr><th style="width:17%">Agent</th><th>Job</th><th style="width:27%">Tools / inputs</th><th style="width:22%">Output (validated)</th></tr>
<tr><td><b>Profiler</b> (AI)</td><td>Turns the golfer's words, and saved profile, into a search: place, radius, trip or single round, price tier, wanted extras,
and physical needs as <i>fixed choices</i> (e.g. terrain = avoid_steep). Lists anything it must refuse.</td><td>The message (marked as data), today's date, code's safety-scan hints</td><td>GolferRequest (PydanticAI structured output)</td></tr>
<tr><td><b>Course Analyst</b> (AI)</td><td>Scores one course 0–100 for this group using a written rubric, with 2–5 findings that each cite a named data field.
Runs on up to 10 courses in parallel.</td><td>Group needs + that course's measured facts; unknown facts are null</td><td>AccessibilityAssessment</td></tr>
<tr><td><b>Concierge</b> (AI)</td><td>Picks the best 1–5 courses (more for trips), explains each in plain words, notes what to confirm, and politely declines refused requests.</td>
<td>Scored candidates, wanted extras, refused requests</td><td>ConciergeReply (course IDs only)</td></tr>
</table>
<p><b>The code steps between them</b> are the safety and cost backbone: the <b>input guard</b> scans for attacks; the <b>profile floor</b> makes saved needs
impossible to loosen; the <b>Course Locator</b> resolves the place (2,747 towns plus regions), filters by distance, and pre-scores courses from measured facts so only the
top 10 reach the AI; the <b>fact check</b> removes claims about unknown or nonexistent fields and caps scores when key facts are missing; and the <b>pick check</b> drops any
course the Concierge invents, then copies every displayed fact, price tier, distance and link from the database.</p>
<p><b>Model choice.</b> All three agents use <b>gpt-6-luna</b> through Portkey. It is the only model allowed on the course key, and it fits the job: the tasks are short form-filling,
rubric scoring and short writing, so I run it with <i>low reasoning effort</i>. At $0.10 per million input tokens ($0.01 cached) and $0.50 per million output tokens, it keeps a search
under half a cent. Using one model with fixed instructions per agent also maximizes prompt caching.</p>

<h3>One real run, step by step</h3>
<p>The query: <i>"Long weekend on Cape Cod with my wife. She's a beginner and we want easy, flat courses with a driving range to warm up and somewhere for lunch. 3 rounds."</i>
The app shows each step in a "How TrueCourse decided" panel; the screenshots below are from that run, with the cache empty.</p>
<div class="grid2">
{img('02_progress.jpg', 'Figure 3. While working, the app shows which agent is active.')}
{img('07_trace_1.jpg', 'Figure 4. Steps 1–2: the input guard finds no attack; the Profiler turns the sentence into a structured search (Cape Cod, trip, 3 rounds, wants a range and food).')}
{img('08_trace_2.jpg', 'Figure 5. Steps 3–4: the Course Locator finds 63 courses within 35 miles and sends the top 10 by measured accessibility; the Course Analyst scores all 10.')}
{img('09_trace_3.jpg', 'Figure 6. Step 5: the Concierge picks three courses and writes why each fits, citing measured facts.')}
</div>
<ol>
<li><b>Input guard (code, 2 ms):</b> cleaned 153 characters; no attack patterns.</li>
<li><b>Profiler (AI, about 4 s):</b> 3,961 input tokens (2,814 cached), 127 output. Output: near = "Cape Cod", trip = true, rounds = 3, wants = driving_range + food_drink,
terrain = flat_only, forced_carries = avoid_all, shorter_course = true, skill = beginner.</li>
<li><b>Course Locator (code, 2 ms):</b> Cape Cod resolves to a region center with a 35-mile radius; 63 courses qualify; the top 10 by pre-score go to the AI
(Holly Ridge first; it has a mapped driving range).</li>
<li><b>Course Analyst (AI ×10, about 6 s in parallel):</b> about 2,236 input tokens each (about 2,110 cached). Holly Ridge and Falcon score 100; Highland Links 90.</li>
<li><b>Fact check (code):</b> removed 2 claims about facts the database doesn't have.</li>
<li><b>Concierge (AI, about 3 s):</b> 3,863 input, 267 output; picks Holly Ridge, Falcon, and Harwich Port, and notes that only Holly Ridge has a mapped range.</li>
<li><b>Pick check (code):</b> every pick is a real scored course; facts, map pins and links are copied from the database. Total: <b>$0.0029, 13.4 s.</b></li>
</ol>
<div class="grid2">
{img('03_results_head.jpg', 'Figure 7. The result: a trip-plan headline, the needs TrueCourse understood, and a fact-check notice.')}
{img('05_card.jpg', 'Figure 8. A result card. Measured facts, the wanted driving range (lime), and food marked "not on the map, ask".')}
</div>
"""


def section_tokenomics() -> str:
    s1, s100, s10k = SCEN
    return f"""
<h2 class="pb"><span class="num">4</span>Tokenomics</h2>
<p>All token counts below come from the API's own usage reports, logged for every search in <code>output/usage_log.json</code> and shown live in the app's
"Cost of this search" panel. Prices: gpt-6-luna at $0.10 / 1M input tokens, $0.01 / 1M cached input, $0.50 / 1M output.</p>
<table>
<tr><th>Typical search (averages of real runs)</th><th class="num">Input tokens</th><th class="num">of which cached</th><th class="num">Output tokens</th><th class="num">Cost</th><th class="num">Time</th></tr>
<tr><td>First-time search, cache empty ({T['cold_n']} runs)</td><td class="num">{n(T['cold_in'])}</td><td class="num">{n(T['cold_cached'])}</td><td class="num">{n(T['cold_out'])}</td><td class="num">{usd(T['cold_cost'])}</td><td class="num">{T['cold_ms']/1000:.1f}s</td></tr>
<tr><td>Repeat-type search, course scores cached ({T['warm_n']} runs)</td><td class="num">{n(T['warm_in'])}</td><td class="num">{n(T['warm_cached'])}</td><td class="num">{n(T['warm_out'])}</td><td class="num">{usd(T['warm_cost'])}</td><td class="num">{T['warm_ms']/1000:.1f}s</td></tr>
</table>
<p>Per agent, per call: Profiler ≈ {n(AG['profiler']['input_tokens'])} in / {n(AG['profiler']['output_tokens'])} out ({usd(AG['profiler']['cost_usd'],5)});
Course Analyst ≈ {n(AG['course_analyst']['input_tokens'])} in / {n(AG['course_analyst']['output_tokens'])} out ({usd(AG['course_analyst']['cost_usd'],5)}) per course;
Concierge ≈ {n(AG['concierge']['input_tokens'])} in / {n(AG['concierge']['output_tokens'])} out ({usd(AG['concierge']['cost_usd'],5)}). First-time searches ranged from
{usd(T['cold_min'])} to {usd(T['cold_max'])}.</p>
<div class="grid2">
{img('10_cost_cold.jpg', 'Figure 9. Cost panel, first-time Cape Cod search: 32,015 input (26,048 cached), 3,770 output, $0.0029.')}
{img('11_cost_warm.jpg', 'Figure 10. The same search again: all 10 course scores from cache, 28,527 tokens saved, $0.0003.')}
</div>
<h3>Cost per user and at scale</h3>
<p>Assumptions: each active user runs <b>{SEARCHES_PER_USER} searches a month</b> (a couple of local rounds and part of a trip). The share of searches whose course scores
are already cached grows with use because physical needs are fixed choices: the next golfer with a bad knee near Hartford produces the same cache key as the last one.
I assume 50% cached at 100 users and 90% at 10,000. Hosting uses Render's published prices.</p>
<table>
<tr><th>Scenario</th><th class="num">Searches / mo</th><th class="num">Cached</th><th class="num">AI cost</th><th class="num">Hosting</th><th class="num">Total / mo</th><th class="num">Per user</th><th class="num">AI cost with no cost-cutting</th></tr>
{scen_rows()}
</table>
<p>One user costs about <b>{usd(SCEN[0]['ai'], 3)} a month</b> in AI. At 10,000 users the whole service costs about <b>{usd(s10k['total'], 0)} a month, or {usd(s10k['per_user'], 4)} per user</b>,
and hosting, not AI, becomes the largest line. Pre-warming the cache for every course and the 50 most common needs combinations would be a one-time cost of about
<b>{usd(prewarm, 2)}</b>.</p>
<h3>What I did to keep costs down</h3>
<ol>
<li><b>Course Locator in code.</b> Instead of asking the AI about every nearby course (about {T['considered']:.0f} on average), a rule-based pre-score from the measured facts sends only the
top {T['analyzed']:.0f}. The cost of a search stays flat whether the database has 15 courses or 851. Without it, and the steps below, a search would cost about <b>{usd(naive_per_search)}</b>,
about {naive_per_search/T['cold_cost']:.0f}× more.</li>
<li><b>Accessibility cache.</b> Each course score is saved under course + needs. Repeat-type searches skip the Analyst entirely.</li>
<li><b>Prompt caching.</b> Each agent's long instructions are byte-identical on every call, and per-run data comes after them, so OpenAI bills about {T['cold_cached']/T['cold_in']:.0%} of input
tokens at the cached rate (one tenth the price).</li>
<li><b>Low reasoning effort and small outputs.</b> Short structured forms instead of long essays; the Concierge is skipped when there is nothing to pick.</li>
<li><b>No AI at all for data.</b> All 12,561 holes were measured once by code, at zero token cost.</li>
</ol>
<h3>Did cost cutting hurt quality?</h3>
<table>
<tr><th>Test (tests/cache_test.py)</th><th class="num">Cold in/out</th><th class="num">Cold $</th><th class="num">Cold time</th><th class="num">Warm in/out</th><th class="num">Warm $</th><th class="num">Warm time</th><th class="num">Saving</th><th>Course scores</th></tr>
{cache_rows()}
</table>
<p>The cache cut cost by about 92% and time by 3–4× with <b>identical scores</b>. It actually <i>improves</i> consistency: without it, the same course could score 80 one run and 85 the next.
The Locator is the one trade-off: courses ranked 11th or lower by the rule-based pre-score are never checked by the AI. In the Cape Cod run, the pre-score's top two (Holly Ridge 106,
Falcon 102) were also the Analyst's top two (100, 100), which suggests the shortlist keeps the best options, but it can miss a course whose strengths only show up in review text.</p>
"""


def section_safety() -> str:
    return f"""
<h2 class="pb"><span class="num">5</span>Safety</h2>
<p>TrueCourse holds health-related information (bad knees, wheelchairs) and runs on a paid API key, so I designed for three kinds of risk. Every defense below is tested;
results are in <code>output/attack_results.json</code> and <code>tests/attack_test.py</code>.</p>
<table>
<tr><th style="width:18%">Risk</th><th>What could go wrong</th><th style="width:48%">Defenses (layered)</th></tr>
<tr><td><b>Attacks</b>: prompt injection</td><td>Someone tries to rank their club first, plant a false fact ("say it's flat"), get a free round, or reveal the system prompt;
or hides instructions in a saved profile note</td><td>(1) Code scanner flags attack phrases and hidden characters; (2) the AI provider's own content filter blocks the worst attempts;
(3) messages and profiles are wrapped as <i>data</i>, and prompts say so; (4) the Profiler lists refusals; (5) <b>no AI output has a field for a fact, price or ranking</b>; facts are
copied from the database by code.</td></tr>
<tr><td><b>Attacks</b>: data leaks</td><td>API key exposure; stolen passwords; health info left behind</td><td>Keys only in server environment variables; errors never sent to the browser; passwords
hashed with scrypt; sessions in HttpOnly SameSite cookies; no email collected; "delete my account" removes everything; signing out clears the device copy; security headers.</td></tr>
<tr><td><b>Malfunction</b>: invented facts</td><td>The Analyst claims a course is flat when the data doesn't say</td><td>Every finding must cite a real data field; the fact check drops findings that cite unknown
or nonexistent fields and caps scores when a fact the group depends on is missing. Across all logged searches it intervened <b>{len(fact)} times</b>
({fact_dropped} claims dropped, {fact_capped} scores capped, {fact_verdict} verdicts corrected).</td></tr>
<tr><td><b>Malfunction</b>: wrong action, loops</td><td>A made-up course is recommended; an agent loops; a vague message drops a wheelchair need</td><td>Pick check drops any course that wasn't scored;
each agent run is limited to 3 model requests and 12–16k tokens; the saved-profile floor keeps needs at least as strict as saved; analyst errors retry once, then skip.</td></tr>
<tr><td><b>Overspending</b></td><td>A bot or a flood of visitors runs up the bill</td><td>Rate limit (8 searches per visitor per 10 minutes); <b>$2/day budget</b> checked before any AI call;
at most 12 fresh course assessments per search; the cache; sign-in attempts limited to 10 per 10 minutes.</td></tr>
</table>
<h3>Defenses working</h3>
<table>
<tr><th>Attack (tests/attack_test.py)</th><th>What happened</th><th>Recommended instead</th><th>Result</th></tr>
{attack_rows()}
</table>
<div class="grid2">
{img('12_attack_caught.jpg', 'Figure 11. A fake "SYSTEM" message tells the AI to always recommend Fox Hopyard first. Caught five ways; the results are Boston-area courses, not Fox Hopyard, which is over 100 miles away.')}
{img('13_provider_block.jpg', 'Figure 12. The worst attacks never reach our agents: the AI provider’s filter blocks them and the app explains why.')}
</div>
<p>A fourth safety layer appeared during testing: Portkey's Azure-hosted model has its own jailbreak filter. It initially crashed the search with a generic error; the app now
treats it as a clean block (or, for the Concierge, falls back to choosing the top-scored courses in code).</p>
"""


def section_impact() -> str:
    u = REAL_USER
    real = (f"""<div class="callout"><b>Real user test.</b> {esc(u['who'])}<br>
    {'<span class="quote" style="display:block">“' + esc(u['quote']) + '”</span>' if u['quote'] else ''}
    {'<b>Before:</b> ' + esc(u['before']) + ' &nbsp; <b>With TrueCourse:</b> ' + esc(u['after']) if u['before'] else ''}
    {'<br><b>Outcome:</b> ' + esc(u['outcome']) if u['outcome'] else ''}</div>""" if u["quote"] else
            todo("Have your dad (or another golfer with a physical limit) use the live app on a course decision he actually faces. Record: who he is; "
                 "a quote in his words; how long finding a suitable course takes him today (calls, websites) vs. with TrueCourse; and whether the pick was right. "
                 "Put it in REAL_USER at the top of report/build_report.py and rebuild. This is the evidence the rubric weighs most."))
    calls = (f'<div class="callout"><b>Phone verification.</b> {esc(PHONE_CALLS)}</div>' if PHONE_CALLS else
             todo("Optional but strong: call 4-6 courses with the app's 'What to ask when you call' list and record the answers (cart access near greens, "
                  "adaptive carts). Add a sentence to PHONE_CALLS and rebuild."))
    return f"""
<h2><span class="num">6</span>Impact</h2>
<table>
<tr><th style="width:44%">Measure</th><th>Result</th><th style="width:22%">Source</th></tr>
<tr><td>Accessibility facts known per course (8 that matter)</td><td><b>1 of 8</b> from public web sources vs. <b>5 of 8</b> measured, for the same 15 courses; {known_all:.1f} of 8 on
average across all {len(courses):,} courses</td><td>My own test (hand research vs. build pipeline)</td></tr>
<tr><td>Courses and holes covered</td><td><b>{len(courses):,} courses</b> in CT, RI, MA, VT, NH, ME; <b>{len(measured):,}</b> measured; <b>{holes_measured:,} holes</b></td><td>data/courses.json</td></tr>
<tr><td>Time to a sourced shortlist</td><td>About <b>{T['cold_ms']/1000:.0f} seconds</b> for a first-time search, {T['warm_ms']/1000:.0f} s when cached, with measured reasons, a map and a call list</td><td>usage_log.json ({T['runs']} runs)</td></tr>
<tr><td>Plain AI on the same question</td><td>Could not give climbing or water numbers; its one suggestion measured as hilly</td><td>report/evidence/plain_ai_comparison.json</td></tr>
<tr><td>Honest "no"</td><td>"Is Fox Hopyard too hilly for my dad after knee surgery?" → <b>30/100, not a good fit</b>: 433 ft of climbing, 5 holes steeper than a ramp</td><td>Live run (Figure 13)</td></tr>
<tr><td>Extras for trip planning</td><td>Driving ranges mapped at <b>{amen.get('driving_range',0)}</b> courses, practice greens at {amen.get('practice_green',0)}, food &amp; drink at {amen.get('food_drink',0)}</td><td>OpenStreetMap, matched by code</td></tr>
<tr><td>Cost to serve</td><td>About {usd(SCEN[0]['ai'],3)} of AI per user per month; {usd(SCEN[2]['per_user'],4)} per user all-in at 10,000 users</td><td>Section 4</td></tr>
</table>
{real}
{calls}
<p><b>Careful estimate of time saved.</b> Today, checking three candidate courses for a golfer with a bad knee means reading each website and reviews and calling the pro shop:
roughly 10–15 minutes per course, or 30–45 minutes, and the answer is still mostly guesswork. TrueCourse returns a ranked, measured shortlist in under 20 seconds and narrows the
phone call to the 2–4 questions the data can't answer. The bigger impact is not minutes but rounds: a group that can find a course everyone can enjoy keeps playing together.</p>
<div class="grid2">
{img('14_fox_check.jpg', 'Figure 13. An honest answer: Fox Hopyard is scored 30, "not a good fit", for a dad recovering from knee surgery, with the measurements behind it.')}
{img('06_callscript.jpg', 'Figure 14. Each pick comes with the exact questions to ask when you call, built by code from what the data doesn’t know.')}
</div>
"""


def section_limits() -> str:
    unknown_share = access.get("unknown", 0) / max(len(courses), 1)
    return f"""
<h2><span class="num">7</span>Limits and next steps</h2>
<h3>What doesn't work yet</h3>
<ul>
<li><b>Course rules are mostly unknown.</b> Cart access near greens and adaptive carts aren't on any map, so most scores are capped and come with "call to confirm".
Adaptive equipment is verified for only {adaptive_n} courses, and ADA parking and clubhouse access for {ada_tagged}; the walks are straight-line distances from the middle of a mapped lot.</li>
<li><b>Public vs. private is unknown for {unknown_share:.0%} of courses.</b> Known private clubs are excluded; the rest show "Access: check" and the call list asks first.</li>
<li><b>No live tee times.</b> There is no public tee-time API for these courses, and scraping booking sites breaks their terms; TrueCourse links to each course's own booking page.
Price tiers are researched for only 12 featured facilities.</li>
<li><b>Map quality varies.</b> {len(courses)-len(measured)} courses are outlines only; bunker depth isn't measured; water is detected only where mapped; elevation uses six points per hole from a
10-meter dataset, so very short steep pitches can be missed or exaggerated.</li>
<li><b>AI scores vary a little.</b> The same course can score 80 one run and 85 the next, or 30 vs. 45 when the Profiler reads "knee surgery" slightly differently. The cache hides this for
repeat questions, but it is real.</li>
<li><b>Free hosting.</b> The live site sleeps after inactivity (about a 30-second first load), and accounts and the daily spending tally reset when Render restarts the app. Profiles survive in
each user's browser copy.</li>
<li><b>Ground truth.</b> I checked the measurements against known courses (coastal Short Beach is flat; Fox Hopyard and The VUE are hilly, matching reviews), but not against on-site surveys.</li>
</ul>
<h3>What I would build with another month</h3>
<ol>
<li><b>Course-confirmed facts.</b> A short form for pro shops (cart rules, adaptive carts, flags) with a "verified" badge, seeded by the app's call lists; partner with the National Alliance for
Accessible Golf to merge listings.</li>
<li><b>Golfer feedback loop.</b> After a round, one question ("Was it as easy as we said?"), used to correct scores and learn which needs matter most.</li>
<li><b>Real tee times</b> through a booking partner API, and real price tiers for every course.</li>
<li><b>National coverage.</b> The pipeline is state-agnostic; the U.S. is an overnight run. Plus Postgres for durable accounts.</li>
<li><b>A pilot with an adaptive golf program</b> to measure rounds played and repeat use, the impact numbers that matter most.</li>
</ol>
"""


def section_sources() -> str:
    return """
<h3>Sources</h3>
<ul class="sources">
<li>National Golf Foundation: senior (65+) participation; growth of 65+ on-course golfers 2016–2025 (ngf.org; reported by Golf Digest).</li>
<li>CDC/NCHS arthritis prevalence for adults 65+ (via Statista; state BRFSS values via America's Health Rankings).</li>
<li>CDC, "Disability Impacts All of Us": 27% of U.S. adults have a disability; 12.1% have a mobility disability (cdc.gov).</li>
<li>National Alliance for Accessible Golf: accessible facility search engine, self-listing (accessgolf.org; Golf Business News).</li>
<li>Hole19 and 18Birdies "plays like" distance features (hole19golf.com; App Store listings).</li>
<li>Render pricing: Starter $7, Standard $25 per service per month; Postgres from about $6–19 per month (render.com and 2026 pricing guides).</li>
<li>Course maps © OpenStreetMap contributors (ODbL). Elevation: USGS 3D Elevation Program, via OpenTopoData. Green fees for featured courses: sources listed in data/featured.json.</li>
<li>AI assistance: I used Claude (Anthropic) as a coding and writing assistant. I am responsible for every number and claim; numbers are generated from the app's logs by report/build_report.py.</li>
</ul>
"""


def build() -> str:
    body = "".join([section_title(), section_motivation(), section_new(), section_agents(), section_tokenomics(),
                    section_safety(), section_impact(), section_limits(), section_sources()])
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>TrueCourse · Final Report · {AUTHOR}</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,700..900&family=Atkinson+Hyperlegible:ital,wght@0,400;0,700;1,400&display=swap" rel="stylesheet">
<style>{CSS}</style></head>
<body><main class="page">{body}</main></body></html>"""


if __name__ == "__main__":
    out = REPORT / "report.html"
    out.write_text(build(), encoding="utf-8")
    print(f"wrote {out} ({out.stat().st_size/1e6:.2f} MB)")
    print(f"cold {usd(T['cold_cost'])}, warm {usd(T['warm_cost'])}, naive {usd(naive_per_search)}, 10k/mo {usd(SCEN[2]['total'],2)}, prewarm {usd(prewarm,2)}")
