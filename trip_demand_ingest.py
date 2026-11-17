#!/usr/bin/env python3
"""Redunator Phase 2c (Worker A) — trip-demand layer ingest.

Pulls HOURLY trip counts by pickup community area from the City of
Chicago Socrata portal using server-side SoQL aggregation ONLY.
No raw rows are ever downloaded.

What each output row is (per the user's correction: real counts, never
averages): for one calendar day, one hour of day (0-23, Chicago local),
and one pickup community area (1-77, plus NULL where the source
suppressed the area), `trips` = the exact number of trips in the source
dataset whose trip_start_timestamp falls in that hour — a real
count(*) over real trip rows, grouped server-side by Socrata.

Datasets (all verified live 2026-11-15 via api/views/<id>):
  Taxi:
    wrvz-psew  Taxi Trips (2013-2023)          pull 2019-01-01 -> 2023-12-31
    ajtu-isnz  Taxi Trips (2024-)              pull 2024-01-01 -> present
  TNP (rideshare):
    m6dm-c72p  TNP - Trips (2018-2022)         pull 2019-01-01 -> 2022-12-31
    n26f-ihde  TNP - Trips (2023-2024)         pull 2023-01-01 -> 2024-12-31
    6dvr-xwnh  TNP - Trips (2025-)             pull 2025-01-01 -> present
  Boundaries:
    igwz-8jzy  Boundaries - Community Areas    GeoJSON download (77 areas)

SoQL pattern (verified working 2026-11-15; note the Socrata quirks):
  - grouping parameter is $group, NOT $group_by
  - hour extraction is date_extract_hh(), NOT datepart() / date_trunc_hour()
    (neither exists on data.cityofchicago.org)
  - date_trunc_ymd() gives the day

  $select = date_trunc_ymd(trip_start_timestamp) as day,
            date_extract_hh(trip_start_timestamp) as hour,
            pickup_community_area as area, count(*) as trips
  $where  = trip_start_timestamp >= '<start>' AND trip_start_timestamp < '<end>'
  $group  = day, hour, area
  $order  = day, hour, area
  $limit  = 50000

Chunking: monthly by default. TNP months yield up to ~58k (day, hour,
area) groups, over Socrata's 50000-row $limit, so they need a second
page via $offset — and $offset pages proved unreliable (the
$offset=50000 page for m6dm-c72p 2019-07 failed 10+ times across runs
while offset=0 pages usually succeed). With --half, each month is
split into 1st-15th / 16th-end halves (~29k groups max each), each a
single page with no $offset at all. Use --half for all TNP pulls.

Timestamps are Chicago-local naive labels (same flat 24/day grid as the
Phase 2b hourly files): the 02:00 row does not exist on spring-forward
days and 01:00 aggregates both fall-back occurrences.

Chunks are saved individually under data/hourly/raw/trip_chunks/ so the
run is resumable; the combine step (trip_demand_combine.py) merges them
into per-source parquet files. The exact queries are logged to
data/hourly/TRIP_QUERIES*.md.
"""
import argparse
import json
import os
import time

import pandas as pd
import requests

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RAW_DIR = os.path.join(BASE_DIR, "data", "hourly", "raw")
CHUNK_DIR = os.path.join(RAW_DIR, "trip_chunks")
os.makedirs(CHUNK_DIR, exist_ok=True)

API = "https://data.cityofchicago.org/resource/{did}.json"
SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "redunator-phase2c-workerA/1.0"})

# (source, dataset_id, first_month, last_month) — inclusive months
JOBS = [
    ("taxi", "wrvz-psew", "2019-01", "2023-12"),
    ("taxi", "ajtu-isnz", "2024-01", "2026-11"),
    ("tnp", "m6dm-c72p", "2019-01", "2022-12"),
    ("tnp", "n26f-ihde", "2023-01", "2024-12"),
    ("tnp", "6dvr-xwnh", "2025-01", "2026-11"),
]

SELECT = (
    "date_trunc_ymd(trip_start_timestamp) as day, "
    "date_extract_hh(trip_start_timestamp) as hour, "
    "pickup_community_area as area, count(*) as trips"
)


def month_bounds(ym):
    y, m = map(int, ym.split("-"))
    start = f"{y:04d}-{m:02d}-01T00:00:00"
    if m == 12:
        end = f"{y + 1:04d}-01-01T00:00:00"
    else:
        end = f"{y:04d}-{m + 1:02d}-01T00:00:00"
    return start, end


def months(first, last):
    y0, m0 = map(int, first.split("-"))
    y1, m1 = map(int, last.split("-"))
    out = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def chunks_for_month(ym, half, week):
    """Return [(label, start, end)] for one month.

    Whole month by default; --half gives 1st-15th / 16th-end (~29k
    groups max each); --week gives 1st-7th / 8th-14th / 15th-21st /
    22nd-end (~13k groups max each). Smaller chunks are single-page
    queries with no $offset (the $offset=50000 page proved unreliable)
    and complete faster, so they survive the flaky egress proxy better.
    """
    start, end = month_bounds(ym)
    if week:
        return [(ym + f"w{i + 1}", start[:8] + f"{d:02d}T00:00:00",
                 start[:8] + f"{e:02d}T00:00:00" if e < 32 else end)
                for i, (d, e) in enumerate(
                    [(1, 8), (8, 15), (15, 22), (22, 32)])]
    if half:
        mid = start[:8] + "16T00:00:00"
        return [(ym + "a", start, mid), (ym + "b", mid, end)]
    return [(ym, start, end)]


def fetch_range(did, start, end, retries=6):
    """Fetch one date range, paging with $offset until a short page.

    Socrata caps $limit at 50000 rows per response. Returns
    (rows, total_seconds, where, n_pages).
    """
    where = (f"trip_start_timestamp >= '{start}' "
             f"AND trip_start_timestamp < '{end}'")
    base_params = {
        "$select": SELECT,
        "$where": where,
        "$group": "day, hour, area",
        "$order": "day, hour, area",
        "$limit": "50000",
    }
    url = API.format(did=did)
    all_rows, total_secs, n_pages = [], 0.0, 0
    offset = 0
    while True:
        params = dict(base_params, **{"$offset": str(offset)})
        last_err = None
        page = None
        for attempt in range(1, retries + 1):
            t0 = time.time()
            try:
                # Fresh session per attempt: the shared session's pooled
                # keep-alive connections go stale while Socrata computes
                # (60-400s), and the egress proxy then drops the reused
                # connection ("Remote end closed connection"). A new
                # session per attempt avoids the stale pool entirely.
                sess = requests.Session()
                sess.headers.update(
                    {"User-Agent": "redunator-phase2c-workerA/1.0"})
                r = sess.get(url, params=params, timeout=600)
                sess.close()
                if r.status_code == 200:
                    page = r.json()
                    total_secs += time.time() - t0
                    break
                last_err = f"HTTP {r.status_code}: {r.text[:200]}"
            except Exception as e:  # noqa: BLE001
                last_err = repr(e)
            # Gentle exponential backoff: the sandbox egress proxy drops
            # connections in bursts when hammered; backing off longer
            # between attempts (and sleeping between chunks, below) keeps
            # it usable.
            print(f"    attempt {attempt} failed ({last_err}); "
                  f"retrying in {30 * attempt}s", flush=True)
            time.sleep(30 * attempt)
        if page is None:
            raise RuntimeError(
                f"{did} {start}..{end} offset {offset}: failed after "
                f"{retries} attempts: {last_err}")
        n_pages += 1
        all_rows.extend(page)
        if len(page) < 50000:
            return all_rows, total_secs, where, n_pages
        offset += 50000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=["taxi", "tnp", "all"], default="all")
    ap.add_argument("--datasets", default="",
                    help="comma-separated dataset ids to pull (default: all "
                    "for the chosen --only)")
    ap.add_argument("--half", action="store_true",
                    help="split each month into 1st-15th / 16th-end halves "
                    "(single-page queries, no $offset)")
    ap.add_argument("--week", action="store_true",
                    help="split each month into 7-day chunks (single-page "
                    "queries, no $offset)")
    ap.add_argument("--from-month", default="",
                    help="first month to pull, YYYY-MM (default: job start)")
    ap.add_argument("--to-month", default="",
                    help="last month to pull, YYYY-MM (default: job end)")
    ap.add_argument("--tag", default="")
    args = ap.parse_args()

    tag = f"_{args.tag}" if args.tag else ""
    summary_path = os.path.join(CHUNK_DIR, f"chunk_summary{tag}.json")
    summary = {}
    if os.path.exists(summary_path):
        summary = json.load(open(summary_path))

    queries_path = os.path.join(BASE_DIR, "data", "hourly",
                                f"TRIP_QUERIES{tag}.md")
    if not os.path.exists(queries_path):
        with open(queries_path, "w") as f:
            f.write("# Phase 2c — exact SoQL queries used\n\n")
            f.write("Pulled 2026-11-15 by `trip_demand_ingest.py`. ")
            f.write("Server-side aggregation only; no raw rows downloaded. "
                    "Each row is a real count(*) of trips for one "
                    "(day, hour, pickup community area) — never an "
                    "average.\n\n")
            f.write("## Query template (one per dataset-range)\n\n")
            f.write("```\nGET https://data.cityofchicago.org/resource/<DATASET>.json\n")
            f.write(f"$select={SELECT}\n")
            f.write("$where=trip_start_timestamp >= '<start>' "
                    "AND trip_start_timestamp < '<end>'\n")
            f.write("$group=day, hour, area\n$order=day, hour, area\n")
            f.write("$limit=50000 (+ $offset=50000, 100000, ... until a "
                    "short page — Socrata caps $limit at 50000 and a TNP "
                    "month yields up to ~58k groups, so one page would "
                    "silently truncate; --half splits months so every "
                    "chunk is a single page with no $offset)\n```\n\n")
            f.write("Socrata quirks found live: `$group` (not `$group_by`); "
                    "`date_extract_hh()` for the hour (no `datepart()` or "
                    "`date_trunc_hour()` on this portal); "
                    "`date_trunc_ymd()` for the day.\n\n")
            f.write("## Chunks pulled\n\n")
            f.write("| source | dataset | range | rows | trips | "
                    "null-area trips | seconds | pages |\n")
            f.write("|---|---|---|---|---|---|---|---|\n")

    jobs = JOBS if args.only == "all" else [j for j in JOBS if j[0] == args.only]
    if args.datasets:
        want = set(args.datasets.split(","))
        jobs = [j for j in jobs if j[1] in want]
    for source, did, first, last in jobs:
        first = args.from_month or first
        last = args.to_month or last
        for ym in months(first, last):
            for label, start, end in chunks_for_month(
                    ym, args.half, args.week):
                key = f"{source}|{did}|{label}"
                chunk_path = os.path.join(
                    CHUNK_DIR, f"{source}_{did}_{label}.parquet")
                # A month already pulled whole (pre-half-split run) must
                # not be re-pulled as halves — that would double-count it
                # at combine time.
                whole_path = os.path.join(
                    CHUNK_DIR, f"{source}_{did}_{ym}.parquet")
                if (args.half or args.week) and label != ym and os.path.exists(whole_path):
                    continue
                if os.path.exists(chunk_path):
                    # already pulled (possibly by a pre-restart run):
                    # rebuild the summary record from the file instead
                    # of re-querying
                    if key not in summary:
                        df0 = pd.read_parquet(chunk_path)
                        summary[key] = {
                            "source": source, "dataset": did,
                            "month": label,
                            "rows": int(len(df0)),
                            "trips": int(df0["trips"].sum()),
                            "null_area_trips": int(
                                df0.loc[df0["area"].isna(), "trips"].sum()),
                            "seconds": None, "pages": None,
                            "min_day": str(df0["day"].min()) if len(df0) else None,
                            "max_day": str(df0["day"].max()) if len(df0) else None,
                            "where": (f"trip_start_timestamp >= '{start}' "
                                      f"AND trip_start_timestamp < '{end}'"),
                        }
                        json.dump(summary, open(summary_path, "w"), indent=1)
                    continue
                print(f"[{source} {did} {label}]", flush=True)
                rows, secs, where, n_pages = fetch_range(did, start, end)
                df = pd.DataFrame(rows)
                if df.empty:
                    print(f"    EMPTY range ({secs:.1f}s)", flush=True)
                    df = pd.DataFrame(columns=["day", "hour", "area", "trips"])
                df["day"] = pd.to_datetime(df["day"]).dt.date
                df["hour"] = df["hour"].astype(int)
                df["area"] = pd.to_numeric(df["area"], errors="coerce")
                df["trips"] = df["trips"].astype(int)
                df["source"] = source
                df["dataset"] = did
                df = df[["day", "hour", "area", "trips", "source", "dataset"]]
                df.to_parquet(chunk_path, index=False)
                n_null = int(df.loc[df["area"].isna(), "trips"].sum())
                rec = {
                    "source": source, "dataset": did, "month": label,
                    "rows": int(len(df)), "trips": int(df["trips"].sum()),
                    "null_area_trips": n_null, "seconds": round(secs, 1),
                    "pages": n_pages,
                    "min_day": str(df["day"].min()) if len(df) else None,
                    "max_day": str(df["day"].max()) if len(df) else None,
                    "where": where,
                }
                summary[key] = rec
                json.dump(summary, open(summary_path, "w"), indent=1)
                with open(queries_path, "a") as f:
                    f.write(f"| {source} | {did} | {label} | {rec['rows']} | "
                            f"{rec['trips']} | {n_null} | {secs:.1f} | "
                            f"{n_pages} |\n")
                print(f"    rows={rec['rows']} trips={rec['trips']} "
                      f"null_area={n_null} pages={n_pages} ({secs:.1f}s)",
                      flush=True)
                # Pace the portal: sleep between chunks so the egress
                # proxy is not hammered with back-to-back long queries.
                time.sleep(20)

    print("ALL CHUNKS DONE", flush=True)


if __name__ == "__main__":
    main()
