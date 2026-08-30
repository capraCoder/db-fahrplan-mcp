"""Smoke test for db_fahrplan_mcp — needs network. Run: python test_smoke.py"""
import json
import sys
from datetime import datetime, timedelta

import db_fahrplan_mcp as m

fails = 0


def check(label, cond):
    global fails
    print(("PASS " if cond else "FAIL ") + label)
    if not cond:
        fails += 1


# 1. station search — a real station must come back first
hits = json.loads(m.db_search_station("Köln Hbf"))
check("search returns Köln Hbf", bool(hits) and hits[0]["name"] == "Köln Hbf")

# 2. journey tomorrow morning — must return at least one connection with legs
when = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d 09:00")
j = json.loads(m.db_journeys("Köln Hbf", "Düsseldorf Hbf", when))
check("journeys non-empty", len(j["journeys"]) > 0)
check("journey has legs with line", all(any("line" in l for l in x["legs"]) for x in j["journeys"]))

# 3. boards — rail-only window must contain no bus/tram
d = json.loads(m.db_departures("Köln Hbf", when, 30))
check("departures non-empty", len(d["abfahrten"]) > 0)
check("departures rail-only", not any(str(r["line"]).startswith(("Bus", "STR")) for r in d["abfahrten"]))
a = json.loads(m.db_arrivals("Köln Hbf", when, 30))
check("arrivals non-empty", len(a["ankuenfte"]) > 0)

# 4. negative control — a nonsense station must be rejected, not fuzzy-matched
try:
    m.db_journeys("Xyzzyqq Nowhere", "Köln Hbf")
    check("bogus station rejected", False)
except ValueError:
    check("bogus station rejected", True)

# 5. negative control — bad time format must be rejected
try:
    m.db_journeys("Köln Hbf", "Düsseldorf Hbf", "tomorrow 9am")
    check("bad time rejected", False)
except ValueError:
    check("bad time rejected", True)

print(f"\n{fails} failure(s)")
sys.exit(1 if fails else 0)
