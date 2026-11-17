#!/usr/bin/env python3
"""Phase 2d Worker 1 — pull ONE trip-speed chunk as a fresh process.

Mirrors trip_pull_one.py (fresh process per chunk: the egress proxy
drops long-lived connections). Server-side SoQL aggregation only.

Kinds:
  valid   — (dow,hour,area): n_valid, sum_miles, sum_seconds for VALID trips
            (trip_seconds>0 AND trip_miles>0 AND implied speed<=80 mph)
  holiday — (hour,area): same aggregates, restricted to US-holiday dates
            inside [start,end) (dates listed in --holidays file)
  edge    — single-row count(*) for one edge class in [start,end):
            zero (seconds=0), missing (seconds/miles NULL),
            absurd (seconds>0, miles not null, miles<=0 or speed>80)
  series  — (day,hour): citywide n, sum_miles, sum_seconds, valid trips
  area    — (day,hour,area): n, sum_miles, sum_seconds, valid trips
Usage: speed_pull_one.py <kind> <did> <label> <start> <end> [--holidays FILE] [--class NAME]
Writes data/hourly/raw/trip_speed_chunks/speed_<did>_<label>.parquet (or .json for edge)
and appends a record to chunk_summary_speed.json. Skips if output exists.
"""
import json, os, sys, time
import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))  # speed_work
HOURLY = os.path.dirname(BASE)
CHUNK_DIR = os.path.join(HOURLY, "raw", "trip_speed_chunks")
os.makedirs(CHUNK_DIR, exist_ok=True)
SUMMARY_PATH = os.path.join(CHUNK_DIR, "chunk_summary_speed.json")
API = "https://data.cityofchicago.org/resource/{did}.json"
VALID = ("trip_seconds > 0 AND trip_miles > 0 "
         "AND trip_miles * 3600 / trip_seconds <= 80")
EDGE_WHERE = {
    "zero": "trip_seconds = 0",
    "missing": "trip_seconds IS NULL OR trip_miles IS NULL",
    "absurd": ("trip_seconds > 0 AND trip_miles IS NOT NULL AND "
               "(trip_miles <= 0 OR trip_miles * 3600 / trip_seconds > 80)"),
}

def fetch(did, params, retries=6, timeout=1200):
    url = API.format(did=did)
    last_err = None
    for attempt in range(1, retries + 1):
        t0 = time.time()
        try:
            sess = requests.Session()
            sess.headers.update({"User-Agent": "redunator-phase2d-speed/1.0"})
            r = sess.get(url, params=params, timeout=timeout)
            sess.close()
            if r.status_code == 200:
                return r.json(), time.time() - t0
            last_err = f"HTTP {r.status_code}: {r.text[:200]}"
        except Exception as e:
            last_err = repr(e)
        print(f"    attempt {attempt} failed ({last_err}); retrying in {30*attempt}s", flush=True)
        time.sleep(30 * attempt)
    raise RuntimeError(f"{did} failed after {retries} attempts: {last_err}")

def main():
    kind, did, label, start, end = sys.argv[1:6]
    holidays_file, edge_class = None, None
    i = 6
    while i < len(sys.argv):
        if sys.argv[i] == "--holidays": holidays_file = sys.argv[i+1]; i += 2
        elif sys.argv[i] == "--class": edge_class = sys.argv[i+1]; i += 2
        else: i += 1
    rng = f"trip_start_timestamp >= '{start}' AND trip_start_timestamp < '{end}'"
    if kind == "valid":
        select = ("date_extract_dow(trip_start_timestamp) as dow, "
                  "date_extract_hh(trip_start_timestamp) as hour, "
                  "pickup_community_area as area, count(*) as n, "
                  "sum(trip_miles) as sum_miles, sum(trip_seconds) as sum_seconds")
        params = {"$select": select, "$where": f"{rng} AND {VALID}",
                  "$group": "dow, hour, area", "$order": "dow, hour, area",
                  "$limit": "50000"}
        cols = ["dow", "hour", "area", "n", "sum_miles", "sum_seconds"]
    elif kind == "holiday":
        dates = json.load(open(holidays_file))
        # one OR clause per holiday day-range
        import datetime as _dt
        parts = []
        for d in dates:
            nxt = (_dt.date.fromisoformat(d) + _dt.timedelta(days=1)).isoformat()
            parts.append(f"(trip_start_timestamp >= '{d}T00:00:00' AND trip_start_timestamp < '{nxt}T00:00:00')")
        where = "(" + " OR ".join(parts) + f") AND {VALID}"
        select = ("date_extract_hh(trip_start_timestamp) as hour, "
                  "pickup_community_area as area, count(*) as n, "
                  "sum(trip_miles) as sum_miles, sum(trip_seconds) as sum_seconds")
        params = {"$select": select, "$where": where,
                  "$group": "hour, area", "$order": "hour, area", "$limit": "50000"}
        cols = ["hour", "area", "n", "sum_miles", "sum_seconds"]
    elif kind == "edge":
        params = {"$select": "count(*) as n",
                  "$where": f"{rng} AND ({EDGE_WHERE[edge_class]})", "$limit": "5"}
        cols = ["n"]
    elif kind == "series":
        select = ("date_trunc_ymd(trip_start_timestamp) as day, "
                  "date_extract_hh(trip_start_timestamp) as hour, count(*) as n, "
                  "sum(trip_miles) as sum_miles, sum(trip_seconds) as sum_seconds")
        params = {"$select": select, "$where": f"{rng} AND {VALID}",
                  "$group": "day, hour", "$order": "day, hour", "$limit": "50000"}
        cols = ["day", "hour", "n", "sum_miles", "sum_seconds"]
    elif kind == "area":
        select = ("date_trunc_ymd(trip_start_timestamp) as day, "
                  "date_extract_hh(trip_start_timestamp) as hour, "
                  "pickup_community_area as area, count(*) as n, "
                  "sum(trip_miles) as sum_miles, sum(trip_seconds) as sum_seconds")
        params = {"$select": select, "$where": f"{rng} AND {VALID}",
                  "$group": "day, hour, area", "$order": "day, hour, area",
                  "$limit": "50000"}
        cols = ["day", "hour", "area", "n", "sum_miles", "sum_seconds"]
    else:
        raise SystemExit(f"unknown kind {kind}")

    out = os.path.join(CHUNK_DIR, f"speed_{did}_{label}.parquet" if kind != "edge"
                       else f"speed_{did}_{label}.json")
    if os.path.exists(out):
        print(f"{kind} {did} {label}: exists, skipping", flush=True); return 0
    print(f"[{kind} {did} {label} {start}..{end}]", flush=True)
    rows, secs = fetch(did, params)
    if kind == "edge":
        n = int(rows[0]["n"]) if rows else 0
        json.dump({"kind": kind, "dataset": did, "label": label, "class": edge_class,
                   "start": start, "end": end, "n": n, "seconds": round(secs, 1),
                   "where": params["$where"]}, open(out, "w"), indent=1)
        print(f"    n={n} ({secs:.1f}s)", flush=True)
    else:
        df = pd.DataFrame(rows)
        if df.empty:
            df = pd.DataFrame(columns=cols)
        for c in ("dow", "hour"):
            if c in df: df[c] = df[c].astype(int)
        if "day" in df: df["day"] = pd.to_datetime(df["day"]).dt.date
        if "area" in df: df["area"] = pd.to_numeric(df["area"], errors="coerce")
        df["n"] = df["n"].astype(int)
        for c in ("sum_miles", "sum_seconds"):
            if c in df: df[c] = pd.to_numeric(df[c], errors="coerce")
        df["dataset"] = did
        df.to_parquet(out, index=False)
        n_trips = int(df["n"].sum())
        print(f"    rows={len(df)} valid_trips={n_trips} ({secs:.1f}s)", flush=True)
        summary = json.load(open(SUMMARY_PATH)) if os.path.exists(SUMMARY_PATH) else {}
        summary[f"{kind}|{did}|{label}"] = {"kind": kind, "dataset": did, "label": label,
            "start": start, "end": end, "rows": int(len(df)), "valid_trips": n_trips,
            "seconds": round(secs, 1), "where": params["$where"],
            "select": params["$select"]}
        json.dump(summary, open(SUMMARY_PATH, "w"), indent=1)
    return 0

if __name__ == "__main__":
    sys.exit(main())
