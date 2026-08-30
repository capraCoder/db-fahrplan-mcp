"""Complex-trip test for db_fahrplan_mcp — needs network. Run: python test_complex.py

Route: Krefeld Hbf -> Garmisch-Partenkirchen. Roughly 7 h, always multi-modal:
regional feeder, one or two ICE/IC legs, regional tail. Exercises ICE parsing,
3+ transfers, walk legs, the Deutschlandticket filter, and arrive_by.
Date is the next Wednesday (>= 3 days out) so the test does not rot.
"""
import json
import sys
from datetime import datetime, timedelta

import db_fahrplan_mcp as m

FROM, TO = "Krefeld Hbf", "Garmisch-Partenkirchen"
LONG_DISTANCE = ("ICE", "IC ", "EC ", "ICE ", "IC", "EC")
fails = 0


def check(label, cond, detail=""):
    global fails
    print(("PASS " if cond else "FAIL ") + label + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        fails += 1


def is_ld(leg):
    line = str(leg.get("line", ""))
    return line.startswith(("ICE", "IC", "EC")) and not line.startswith("ICB")


def rail_legs(j):
    return [l for l in j["legs"] if "line" in l]


d = datetime.now() + timedelta(days=3)
while d.weekday() != 2:
    d += timedelta(days=1)
day = d.strftime("%Y-%m-%d")

# ---------------------------------------------------------------- unrestricted
u = json.loads(m.db_journeys(FROM, TO, f"{day} 09:00"))["journeys"]
check("unrestricted: journeys returned", len(u) >= 3, f"{len(u)}")
check("unrestricted: at least one ICE/IC leg somewhere",
      any(is_ld(l) for j in u for l in rail_legs(j)))
check("unrestricted: at least one journey with >= 3 changes",
      any(j["changes"] >= 3 for j in u))
check("unrestricted: at least one walk leg parsed",
      any(l.get("type") == "walk" for j in u for l in j["legs"]))
check("unrestricted: every journey has a Flexpreis",
      all(isinstance(j["price_eur"], (int, float)) and j["price_eur"] > 0 for j in u))
check("unrestricted: changes == rail legs - 1 for every journey",
      all(j["changes"] == len(rail_legs(j)) - 1 for j in u),
      "; ".join(f"{j['changes']} vs {len(rail_legs(j))}" for j in u))
_rl = [l for j in u for l in rail_legs(j)]
check("unrestricted: >= 90 % of rail legs carry a departure platform "
      "(DB leaves a few unassigned weeks ahead)",
      sum(1 for l in _rl if l["dep_platform"]) >= 0.9 * len(_rl),
      f"{sum(1 for l in _rl if l['dep_platform'])}/{len(_rl)}")
check("unrestricted: leg times chain (each leg's dep >= previous leg's arr)",
      all(all(rail_legs(j)[i]["dep"][:5] >= rail_legs(j)[i - 1]["arr"][:5]
              for i in range(1, len(rail_legs(j))))
          for j in u if j["dep"][:5] <= j["arr"][:5]))   # skip past-midnight journeys

# -------------------------------------------------------------- Deutschlandticket
t = json.loads(m.db_journeys(FROM, TO, f"{day} 09:00", deutschlandticket=True))["journeys"]
check("D-Ticket: journeys returned", len(t) >= 1, f"{len(t)}")
check("D-Ticket: NO ICE/IC/EC leg anywhere (negative control)",
      not any(is_ld(l) for j in t for l in rail_legs(j)),
      "; ".join(l["line"] for j in t for l in rail_legs(j) if is_ld(l)))
check("D-Ticket: >= 3 changes on every journey (regional-only forces it)",
      all(j["changes"] >= 3 for j in t))
check("D-Ticket: slower than fastest unrestricted",
      min(j["duration_min"] for j in t) > min(j["duration_min"] for j in u))

# ----------------------------------------------------------------------- arrive_by
a = json.loads(m.db_journeys(FROM, TO, f"{day} 18:00", arrive_by=True))["journeys"]
check("arrive_by: journeys returned", len(a) >= 1, f"{len(a)}")
check("arrive_by: every arrival <= 18:00", all(j["arr"][:5] <= "18:00" for j in a),
      "; ".join(j["arr"] for j in a))
check("arrive_by: latest arrival within 90 min of the bound (not stale)",
      max(j["arr"][:5] for j in a) >= "16:30", max(j["arr"][:5] for j in a))

# ------------------------------------------------------------- regional_only alias
r = json.loads(m.db_journeys(FROM, TO, f"{day} 09:00", regional_only=True))["journeys"]
check("regional_only: NO ICE/IC/EC leg (negative control)",
      not any(is_ld(l) for j in r for l in rail_legs(j)))

print(f"\nroute {FROM} -> {TO} on {day}: {fails} failure(s)")
sys.exit(1 if fails else 0)
