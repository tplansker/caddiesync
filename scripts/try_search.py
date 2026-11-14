"""Run one search from the command line and print the trace + usage. Usage: python scripts/try_search.py "message" [--no-cache]"""
import asyncio, json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from caddiesync.pipeline import search

msg = next((a for a in sys.argv[1:] if not a.startswith("--")), "Find a Saturday morning tee time under $90 for me and my dad who has a bad knee and can't walk steep hills")
out = asyncio.run(search(msg, use_cache="--no-cache" not in sys.argv))
for s in out["trace"]:
    print(f"[{s['step']}] {s['who']} ({s['kind']}, {s['ms']} ms): {s['summary']}")
print("\nHEADLINE:", out["headline"])
for r in out["recommendations"]:
    print(f" - {r['course_name']} {r['tee_time']} ${r['price_per_player']} score {r['accessibility_match_score']} :: {r['why_it_fits']}")
print("OTHERS:", [(o['course_name'], o['score']) for o in out["other_courses"]])
print("SAFETY:", json.dumps(out["safety"], indent=1))
u = out["usage"]; print("USAGE:", {k: v for k, v in u.items() if k != "calls"})
