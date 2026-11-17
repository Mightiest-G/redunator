import json, os, holidays
W = os.path.dirname(os.path.abspath(__file__))
us = holidays.US(years=range(2019, 2027))
def hol_in(start, end):
    import datetime as dt
    out = []
    d = dt.date.fromisoformat(start[:10]); e = dt.date.fromisoformat(end[:10])
    while d < e:
        if d in us: out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out
# (did, start, end) ranges per dataset for valid/edge/holiday
RANGES = {
 "wrvz-psew": ("2023-01-01T00:00:00", "2024-01-01T00:00:00"),
 "ajtu-isnz": ("2024-01-01T00:00:00", "2026-08-01T00:00:00"),
 "m6dm-c72p": ("2019-01-01T00:00:00", "2023-01-01T00:00:00"),
 "n26f-ihde": ("2023-01-01T00:00:00", "2025-01-01T00:00:00"),
 "6dvr-xwnh": ("2025-01-01T00:00:00", "2026-08-01T00:00:00"),
}
VALID_YEARS = {  # (did, label, start, end)
 "wrvz-psew": [("2023","2023-01-01T00:00:00","2024-01-01T00:00:00")],
 "ajtu-isnz": [("2024","2024-01-01T00:00:00","2025-01-01T00:00:00"),
               ("2025","2025-01-01T00:00:00","2026-01-01T00:00:00"),
               ("2026H1","2026-01-01T00:00:00","2026-08-01T00:00:00")],
 "m6dm-c72p": [(str(y),f"{y}-01-01T00:00:00",f"{y+1}-01-01T00:00:00") for y in (2019,2020,2021,2022)],
 "n26f-ihde": [("2023","2023-01-01T00:00:00","2024-01-01T00:00:00"),
               ("2024","2024-01-01T00:00:00","2025-01-01T00:00:00")],
 "6dvr-xwnh": [("2025","2025-01-01T00:00:00","2026-01-01T00:00:00"),
               ("2026H1","2026-01-01T00:00:00","2026-08-01T00:00:00")],
}
def jobs_for(dids):
    jobs = []
    for did in dids:
        s, e = RANGES[did]
        for lab, ys, ye in VALID_YEARS[did]:
            jobs.append({"kind":"valid","did":did,"label":f"valid_{lab}","start":ys,"end":ye})
        hol = hol_in(s, e)
        hf = os.path.join(W, f"holidays_{did}.json")
        json.dump(hol, open(hf,"w"))
        jobs.append({"kind":"holiday","did":did,"label":"holiday","start":s,"end":e,
                     "holidays": hf, "n_holidays": len(hol)})
        for cls in ("zero","missing","absurd"):
            jobs.append({"kind":"edge","did":did,"label":f"edge_{cls}","start":s,"end":e,"class":cls})
    return jobs
taxi = jobs_for(["wrvz-psew","ajtu-isnz"])
tnp = jobs_for(["m6dm-c72p","n26f-ihde","6dvr-xwnh"])
# series + area-holdout jobs
taxi.append({"kind":"series","did":"ajtu-isnz","label":"series_202501_202610",
             "start":"2025-01-01T00:00:00","end":"2026-10-30T00:00:00"})
for ym, ys, ye in [("2026-08","2026-08-01T00:00:00","2026-09-01T00:00:00"),
                   ("2026-09","2026-09-01T00:00:00","2026-10-01T00:00:00"),
                   ("2026-10","2026-10-01T00:00:00","2026-11-01T00:00:00")]:
    taxi.append({"kind":"area","did":"ajtu-isnz","label":f"area_{ym}","start":ys,"end":ye})
tnp.append({"kind":"series","did":"6dvr-xwnh","label":"series_202501_202609",
            "start":"2025-01-01T00:00:00","end":"2026-10-01T00:00:00"})
for lab, ys, ye in [("2026-08a","2026-08-01T00:00:00","2026-08-16T00:00:00"),
                    ("2026-08b","2026-08-16T00:00:00","2026-09-01T00:00:00"),
                    ("2026-09a","2026-09-01T00:00:00","2026-09-16T00:00:00"),
                    ("2026-09b","2026-09-16T00:00:00","2026-10-01T00:00:00")]:
    tnp.append({"kind":"area","did":"6dvr-xwnh","label":f"area_{lab}","start":ys,"end":ye})
json.dump(taxi, open(os.path.join(W,"jobs_taxi.json"),"w"), indent=1)
json.dump(tnp, open(os.path.join(W,"jobs_tnp.json"),"w"), indent=1)
print("taxi jobs:", len(taxi)); print("tnp jobs:", len(tnp))
for j in taxi+tnp: print(j["kind"], j["did"], j["label"])
