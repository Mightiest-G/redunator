#!/usr/bin/env python3
"""Phase 2c — dataset-level metadata counts (total rows, min/max
trip_start_timestamp, null pickup_community_area share) via SoQL.
Resumable: skips datasets already present in dataset_meta.json.
"""
import json
import os
import time

import requests

S = requests.Session()
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "data", "hourly", "raw", "trip_chunks", "dataset_meta.json")
meta = {}
if os.path.exists(OUT):
    meta = json.load(open(OUT))


def q(did, select, where=None, timeout=600):
    params = {"$select": select}
    if where:
        params["$where"] = where
    for att in range(3):
        try:
            r = S.get(f"https://data.cityofchicago.org/resource/{did}.json",
                      params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001
            print(f"  {did} attempt {att + 1} failed: {e}", flush=True)
            time.sleep(20)
    raise RuntimeError(f"{did} {select} failed")


for did in ["wrvz-psew", "ajtu-isnz", "m6dm-c72p", "n26f-ihde", "6dvr-xwnh"]:
    if did in meta and meta[did].get("total"):
        print(f"{did}: already done, skipping", flush=True)
        continue
    print(f"{did}: counting...", flush=True)
    t0 = time.time()
    tot = int(q(did, "count(*) as n")[0]["n"])
    print(f"  total={tot} ({time.time() - t0:.0f}s)", flush=True)
    rng = q(did, "min(trip_start_timestamp) as mn, "
                 "max(trip_start_timestamp) as mx")[0]
    nulls = int(q(did, "count(*) as n",
                  "pickup_community_area IS NULL")[0]["n"])
    meta[did] = {"total": tot, "min_trip_start": rng["mn"],
                 "max_trip_start": rng["mx"], "null_pickup_area": nulls,
                 "null_share": nulls / tot}
    json.dump(meta, open(OUT, "w"), indent=1)
    print(f"  {did}: total={tot} min={rng['mn']} max={rng['mx']} "
          f"null={nulls} share={nulls / tot:.4f}", flush=True)

n = int(q("igwz-8jzy", "count(*) as n")[0]["n"])
meta["igwz-8jzy"] = {"total": n}
json.dump(meta, open(OUT, "w"), indent=1)
print(f"igwz-8jzy rows: {n}", flush=True)
print("DONE", flush=True)
