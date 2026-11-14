"""Tokenomics evidence: the same searches with the accessibility cache cold, then warm.

    python tests/cache_test.py

Also checks that cost cutting did not change the answer: the warm run must give the same courses the same
scores as the cold run (the cache returns the exact assessment). Results are written to output/cache_results.json.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from caddiesync.config import OUTPUT
from caddiesync.pipeline import search
from caddiesync.tools import cache_clear, write_json

QUERIES = [
    "A course near New Haven for me and my 70-year-old dad. He has a bad knee and can't walk steep hills.",
    "Long weekend on Cape Cod with my wife. She's a beginner and we want easy, flat courses. 3 rounds.",
]


def summary(out: dict) -> dict:
    u = out["usage"]
    return {"input": u["input_tokens"], "cached_input": u["cached_input_tokens"], "output": u["output_tokens"],
            "cost_usd": u["cost_usd"], "ms": u["elapsed_ms"], "analyst_fresh": u["analyst_fresh"],
            "analyst_cache_hits": u["analyst_cache_hits"], "courses_considered": out["searched"]["courses_considered"],
            "scores": {o["course_name"]: o["score"] for o in out["other_courses"]}
            | {r["course_name"]: r["accessibility_match_score"] for r in out["recommendations"]}}


async def main() -> None:
    results = []
    for q in QUERIES:
        cache_clear()
        cold = summary(await search(q, visitor="cache-test"))
        warm = summary(await search(q, visitor="cache-test"))
        same = cold["scores"] == warm["scores"]
        saving = 1 - warm["cost_usd"] / cold["cost_usd"] if cold["cost_usd"] else 0
        results.append({"query": q, "cold": cold, "warm": warm, "same_scores": same, "cost_saving": round(saving, 3)})
        print(f"{q[:60]}...  ({cold['courses_considered']} courses considered)"
              f"\n  cold: {cold['input']:>6} in / {cold['output']:>5} out  ${cold['cost_usd']:.5f}  {cold['ms']} ms"
              f"\n  warm: {warm['input']:>6} in / {warm['output']:>5} out  ${warm['cost_usd']:.5f}  {warm['ms']} ms"
              f"\n  saving {saving:.0%}, same course scores: {same}")
    write_json(OUTPUT / "cache_results.json", results)


if __name__ == "__main__":
    asyncio.run(main())
