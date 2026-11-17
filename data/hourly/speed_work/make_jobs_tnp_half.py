"""Fallback: TNP valid/edge jobs split by HALF-YEAR (yearly scans get
proxy-dropped). Holiday/series/area jobs unchanged. Reuses jobs_tnp.json
structure; skips chunks already pulled."""
import json, os
W = os.path.dirname(os.path.abspath(__file__))
HALVES = {  # did -> [(label, start, end)]
 "m6dm-c72p": [(f"{y}H1", f"{y}-01-01T00:00:00", f"{y}-07-01T00:00:00") for y in (2019,2020,2021,2022)] +
             [(f"{y}H2", f"{y}-07-01T00:00:00", f"{y+1}-01-01T00:00:00") for y in (2019,2020,2021,2022)],
 "n26f-ihde": [("2023H1","2023-01-01T00:00:00","2023-07-01T00:00:00"),
               ("2023H2","2023-07-01T00:00:00","2024-01-01T00:00:00"),
               ("2024H1","2024-01-01T00:00:00","2024-07-01T00:00:00"),
               ("2024H2","2024-07-01T00:00:00","2025-01-01T00:00:00")],
 "6dvr-xwnh": [("2025H1","2025-01-01T00:00:00","2025-07-01T00:00:00"),
               ("2025H2","2025-07-01T00:00:00","2026-01-01T00:00:00"),
               ("2026H1","2026-01-01T00:00:00","2026-08-01T00:00:00")],
}
YEARS = {"m6dm-c72p": [(str(y), f"{y}-01-01T00:00:00", f"{y+1}-01-01T00:00:00") for y in (2019,2020,2021,2022)],
         "n26f-ihde": [("2023","2023-01-01T00:00:00","2024-01-01T00:00:00"),
                       ("2024","2024-01-01T00:00:00","2025-01-01T00:00:00")],
         "6dvr-xwnh": [("2025","2025-01-01T00:00:00","2026-01-01T00:00:00"),
                       ("2026H1","2026-01-01T00:00:00","2026-08-01T00:00:00")]}
jobs = []
for did in ("m6dm-c72p", "n26f-ihde", "6dvr-xwnh"):
    for lab, ys, ye in HALVES[did]:
        jobs.append({"kind": "valid", "did": did, "label": f"valid_{lab}", "start": ys, "end": ye})
    hf = os.path.join(W, f"holidays_{did}.json")
    s = HALVES[did][0][1]; e = HALVES[did][-1][2]
    jobs.append({"kind": "holiday", "did": did, "label": "holiday", "start": s, "end": e, "holidays": hf})
    for lab, ys, ye in YEARS[did]:
        for cls in ("zero", "missing", "absurd"):
            jobs.append({"kind": "edge", "did": did, "label": f"edge_{cls}_{lab}", "start": ys, "end": ye, "class": cls})
jobs.append({"kind": "series", "did": "6dvr-xwnh", "label": "series_202501_202609",
             "start": "2025-01-01T00:00:00", "end": "2026-10-01T00:00:00"})
for lab, ys, ye in [("2026-08a","2026-08-01T00:00:00","2026-08-16T00:00:00"),
                    ("2026-08b","2026-08-16T00:00:00","2026-09-01T00:00:00"),
                    ("2026-09a","2026-09-01T00:00:00","2026-09-16T00:00:00"),
                    ("2026-09b","2026-09-16T00:00:00","2026-10-01T00:00:00")]:
    jobs.append({"kind": "area", "did": "6dvr-xwnh", "label": f"area_{lab}", "start": ys, "end": ye})
json.dump(jobs, open(os.path.join(W, "jobs_tnp_half.json"), "w"), indent=1)
print("half-year fallback jobs:", len(jobs))
