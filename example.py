"""Minimal end-to-end example: optimize search conditions for a JD text file, fetch
candidates per archetype condition, and union them deduplicated by linkedin_id.

Usage: python example.py path/to/jd.txt
"""
import sys

from optimize_and_fetch import optimize_and_fetch_union

jd_path = sys.argv[1] if len(sys.argv) > 1 else None
if not jd_path:
    sys.exit("usage: python example.py path/to/jd.txt")

union_df, stats = optimize_and_fetch_union(
    job_desc=open(jd_path).read(),
    min_target=200, max_target=600,
    max_search_num=500,
)

print(f"\narchetypes: {stats['n_archetypes']}")
for p in stats["per_archetype"]:
    print(f"  - {p['archetype']}: fetched {p['fetched']}"
          f"{' (widened)' if p['widen_geo'] else ''}")
print(f"merged union estimated count: {stats['final_count_estimate']}")
print(f"unique candidates fetched: {stats['union_unique']}")

union_df.to_csv("union_candidates.csv", index=False)
print(f"\nunion df saved: union_candidates.csv ({len(union_df)} candidates)")