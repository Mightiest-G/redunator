"""TNP monthly-split jobs (the proven-survivable unit from Phase 2c:
yearly AND half-yearly TNP valid aggregates get proxy-dropped after
~4-6 min of server compute; monthly TNP chunks succeeded in 2c).
Stream A: n26f valid monthly + edges + holiday + 6dvr series monthly.
Stream B: 6dvr valid monthly + edges + holiday + area holdout.
m6dm (2019-2022) is OUT of the profile window (2023-01-01 -> 2026-07-31);
its monthly re-pull is deferred to a third stream if time permits."""
import json, os
W = os.path.dirname(os.path.abspath(__file__))
def months(y0, m0, y1, m1):
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        nm = m + 1; ny = y + (1 if nm == 13 else 0); nm = nm if nm <= 12 else 1
        out.append((f"{y:04d}-{m:02d}", f"{y:04d}-{m:02d}-01T00:00:00",
                    f"{ny:04d}-{nm:02d}-01T00:00:00"))
        y, m = ny, nm
    return out
def halves(did):
    if did == "n26f-ihde":
        return [("2023H1","2023-01-01T00:00:00","2023-07-01T00:00:00"),
                ("2023H2","2023-07-01T00:00:00","2024-01-01T00:00:00"),
                ("2024H1","2024-01-01T00:00:00","2024-07-01T00:00:00"),
                ("2024H2","2024-07-01T00:00:00","2025-01-01T00:00:00")]
    return [("2025H1","2025-01-01T00:00:00","2025-07-01T00:00:00"),
            ("2025H2","2025-07-01T00:00:00","2026-01-01T00:00:00"),
            ("2026H1","2026-01-01T00:00:00","2026-08-01T00:00:00")]
A, B = [], []
for lab, ys, ye in months(2023, 1, 2024, 12):
    A.append({"kind": "valid", "did": "n26f-ihde", "label": f"valid_{lab}", "start": ys, "end": ye})
A.append({"kind": "holiday", "did": "n26f-ihde", "label": "holiday",
          "start": "2023-01-01T00:00:00", "end": "2025-01-01T00:00:00",
          "holidays": os.path.join(W, "holidays_n26f-ihde.json")})
for lab, ys, ye in halves("n26f-ihde"):
    for cls in ("zero", "missing", "absurd"):
        A.append({"kind": "edge", "did": "n26f-ihde", "label": f"edge_{cls}_{lab}",
                  "start": ys, "end": ye, "class": cls})
for lab, ys, ye in months(2025, 1, 2026, 9):
    A.append({"kind": "series", "did": "6dvr-xwnh", "label": f"series_{lab}", "start": ys, "end": ye})
for lab, ys, ye in months(2025, 1, 2026, 7):
    B.append({"kind": "valid", "did": "6dvr-xwnh", "label": f"valid_{lab}", "start": ys, "end": ye})
B.append({"kind": "holiday", "did": "6dvr-xwnh", "label": "holiday",
          "start": "2025-01-01T00:00:00", "end": "2026-08-01T00:00:00",
          "holidays": os.path.join(W, "holidays_6dvr-xwnh.json")})
for lab, ys, ye in halves("6dvr-xwnh"):
    for cls in ("zero", "missing", "absurd"):
        B.append({"kind": "edge", "did": "6dvr-xwnh", "label": f"edge_{cls}_{lab}",
                  "start": ys, "end": ye, "class": cls})
for lab, ys, ye in [("2026-08a","2026-08-01T00:00:00","2026-08-16T00:00:00"),
                    ("2026-08b","2026-08-16T00:00:00","2026-09-01T00:00:00"),
                    ("2026-09a","2026-09-01T00:00:00","2026-09-16T00:00:00"),
                    ("2026-09b","2026-09-16T00:00:00","2026-10-01T00:00:00")]:
    B.append({"kind": "area", "did": "6dvr-xwnh", "label": f"area_{lab}", "start": ys, "end": ye})
json.dump(A, open(os.path.join(W, "jobs_tnp_A.json"), "w"), indent=1)
json.dump(B, open(os.path.join(W, "jobs_tnp_B.json"), "w"), indent=1)
print("stream A jobs:", len(A), " stream B jobs:", len(B))
