"""Minimal end-to-end example: optimize search conditions for a JD text file.

Usage: python example.py path/to/jd.txt
"""
import json
import sys

from dotenv import load_dotenv

load_dotenv()

from advanced_search_optimization_v3 import single_process, clear_optimization_caches

jd_path = sys.argv[1] if len(sys.argv) > 1 else None
if not jd_path:
    sys.exit("usage: python example.py path/to/jd.txt")

clear_optimization_caches()
result = single_process(
    initial_conditions={}, mandatory_skills=[], relaxation_options={},
    min_target=200, max_target=600,
    job_desc=open(jd_path).read(),
)

print(f"\narchetypes: {len(result.get('archetypes') or [])}")
for a in result.get("archetypes") or []:
    print(f"  - {a.get('label')}: est. {a.get('final_count')}"
          f"{' (widened)' if a.get('widen_geo') else ''}")
print(f"merged union estimated count: {result.get('final_count')}")
print("\nmerged condition:")
print(json.dumps(result.get("format_filter_conditions") or {}, ensure_ascii=False, indent=2)[:2000])