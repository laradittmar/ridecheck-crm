#!/usr/bin/env bash
# L4.7W5-F2 Part 11 — agenda prep gate: report capacity PER ZONE, not "some zone is free".
#
# The L4.7W5-PREP gate accepted a week as usable because *some* zone had slots each day.
# Sur/Berazategui was already empty on Tue/Thu/Fri in that same output and it was not
# flagged, so the owner's natural choice of location was unbookable for the first three
# days of the complete Wild. Scarcity is legitimate test pressure and is NOT removed here —
# but it must be visible before a Wild starts, not discovered during one.
set -euo pipefail
CONTAINER="${1:-ridecheck-crm-backend-1}"
docker exec -i "$CONTAINER" python - <<'PY'
import sys; sys.path.insert(0, "/app")
from datetime import date, time, timedelta
from app.db import SessionLocal
from app.schemas.schedule import ScheduleCheckIn
from app.services.schedule import ScheduleService

ZONES = [("Norte", "San Isidro"), ("CABA", "Belgrano"),
         ("Oeste", "Ramos Mejía"), ("Sur", "Berazategui")]
db, svc = SessionLocal(), ScheduleService(SessionLocal())
start = date.today()
print(f"{'day':<16}" + "".join(f"{g+'/'+d:<22}" for g, d in ZONES))
scarce = []
for off in range(7):
    day = start + timedelta(days=off)
    row, per_zone = f"{day} {day.strftime('%a')}  ", []
    for g, d in ZONES:
        out = svc.list_slots(ScheduleCheckIn(
            preferred_day=day, preferred_time=time(9, 0),
            address=f"{d}, {g}, Buenos Aires, Argentina",
            zone_group=g, zone_detail=d, is_holiday=False))
        n = len(out.slots or [])
        per_zone.append(n)
        row += f"{('closed' if out.business_hours=='cerrado' else str(n)+' slots'):<22}"
    print(row)
    if out.business_hours != "cerrado":
        for (g, d), n in zip(ZONES, per_zone):
            if n == 0:
                scarce.append(f"{day} {g}/{d}")
print()
if scarce:
    print(f"ZONE SCARCITY ({len(scarce)}) — a customer in these zones cannot book that day:")
    for s in scarce:
        print("  ", s)
    print("\nThis is reported, not corrected. Scarcity is valid test pressure; the point is\n"
          "that it must be known before a Wild, not discovered inside one.")
else:
    print("No zone-specific scarcity in the next 7 days.")
db.close()
PY
