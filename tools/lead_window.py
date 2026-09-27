#!/usr/bin/env python3
"""Print the first and last 'Created On' date in raw/sheets/funnel.csv, for the F1/F2 Windsor pulls.

  python3 tools/lead_window.py   ->   date_from=2026-09-01 date_to=2026-09-24
The funnel's spend must cover exactly these dates; a longer spend window inflates every cost per lead / SQL."""
import csv, datetime as dt, os, sys

p = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "raw", "sheets", "funnel.csv")
if not os.path.exists(p):
    sys.exit("no raw/sheets/funnel.csv — skip F1/F2")
ds = set()
for r in csv.DictReader(open(p, encoding="utf-8-sig")):
    v = (r.get("Created On") or "").strip()[:10]
    try:
        ds.add(dt.date.fromisoformat(v))
    except ValueError:
        pass
if not ds:
    sys.exit("no readable Created On dates")
print(f"date_from={min(ds)} date_to={max(ds)}")
