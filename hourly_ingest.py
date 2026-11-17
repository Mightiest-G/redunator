#!/usr/bin/env python3
"""Redunator Phase 2b — hourly proxy ingest (worker A).

Ingests the HOURLY proxy sources that feed the hourly disaggregation layer
on top of the Phase 2 daily model. The daily violation data is DAILY only;
nothing here touches the daily model, its features, or its artifacts.

Sources (all verified live against the Socrata metadata API — see
data/hourly/SOURCES_VERIFIED.md for the exact columns/timestamps observed):
  * Chicago Traffic Tracker congestion by segment, historical 2024-2026:
    Socrata id 4g9f-3jbs (101,139,655 rows). ~10-minute slices per segment,
    speed in mph, speed = -1 is a missing-data sentinel. DATA ENDS
    2026-04-30T01:10:21 — the feed is discontinued ("no longer updated"),
    so it CANNOT cover the Phase 2 holdout window (2026-08-01..2026-10-29).
    Pulled as SERVER-SIDE hourly aggregates (mean of valid speed >= 0
    readings + n_reads) filtered to the segments nearest the red-light
    cameras, window 2025-01-01..2026-04-30.
  * Chicago Traffic Tracker congestion by region, historical 2018-2026:
    Socrata id kf7e-cur8. 29 regions, same ~10-min cadence, same end date
    2026-04-30T01:10:21. speed = 0 is the missing-data sentinel here.
    Pulled as server-side hourly aggregates (speed > 0) as a fallback /
    coverage check. Window 2025-01-01..2026-04-30.
  * Traffic Crashes - Crashes: Socrata id 85ca-t3if (1,113,360 rows, live,
    last crash 2026-11-14). crash_date is a full timestamp (Chicago local);
    crash_hour is the derived 0-23 hour. Pulled raw with a $where date
    filter for 2025-01-01..2026-10-29, then aggregated to hourly crash
    counts within 200 m of each red-light camera.
  * Hourly weather: Open-Meteo archive API (free, no key), Chicago
    41.8781,-87.6298, temperature_2m / precipitation / snowfall /
    wind_speed_10m, timezone America/Chicago, 2025-01-01..2026-10-29.

"Current" congestion datasets (n4j6-wkkf by segment, t2qc-9pjd by region)
are DEPRECATED frozen snapshots (1,257 rows / 29 rows, all _last_updt =
2026-04-30 01:10:xx). They hold no history; they are used ONLY for segment
geometry (start/end lat/lon) to map cameras to segments.

Camera coordinates come from data/processed/red_light_features.parquet
(365 cameras). Four cameras have NO coordinates and cannot be mapped or
counted in the crash aggregation: 1623, 2552, 2592, 2662.

Usage:
    python hourly_ingest.py --steps segments,map,congestion,region,crashes,aggregate,weather,verify
    python hourly_ingest.py --steps crashes,aggregate   # re-run one part
    python hourly_ingest.py --list-steps

Outputs (all under data/hourly/):
    raw/congestion_segments_hourly_raw.csv   server-side hourly aggregates, camera segments
    raw/congestion_regions_hourly_raw.csv    server-side hourly aggregates, all regions
    raw/crashes_20250101_20261029.csv        raw filtered crash rows
    raw/weather_chicago_hourly.csv           raw Open-Meteo hourly rows
    raw/segments_snapshot.csv                segment geometry (from n4j6-wkkf)
    processed/camera_segment_map.csv         camera -> nearest segment (+ distance)
    processed/congestion_hourly.parquet      hourly mean speed per camera segment
    processed/congestion_region_hourly.parquet  hourly mean speed per region
    processed/crashes_hourly_per_camera.parquet hourly crash counts within 200 m per camera
    processed/crashes_hourly_citywide.parquet   hourly crash counts, whole city
    processed/weather_hourly.parquet         hourly weather, Chicago
    INGEST_SUMMARY.json                      row counts + date ranges per file

Memory: chunked throughout; the 101M-row congestion table is NEVER
downloaded raw — only server-side hourly aggregates for ~hundreds of
segments are pulled, in monthly chunks.
"""

import argparse
import calendar
import json
import math
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests

BASE = Path(__file__).resolve().parent
RAW = BASE / "data" / "hourly" / "raw"
PROC = BASE / "data" / "hourly" / "processed"

# Socrata resource endpoints (verified live 2026-11-15; the ids in the
# original Phase 2b brief — 8v94-mt3p, 3v54-hgmi, n4j6-ws8y, ft7m-7xmn,
# 85fq-67pd — all return HTTP 404 "not_found"; see SOURCES_VERIFIED.md).
SEG_HIST = "4g9f-3jbs"   # Historical Congestion by Segment 2024-2026
REG_HIST = "kf7e-cur8"   # Historical Congestion by Region 2018-2026
SEG_SNAP = "n4j6-wkkf"   # Congestion by Segments - Deprecated (snapshot)
REG_SNAP = "t2qc-9pjd"   # Congestion by Regions - Deprecated (snapshot)
CRASHES = "85ca-t3if"    # Traffic Crashes - Crashes

# Windows. Congestion feed is dead after 2026-04-30, so its window ends
# there; crashes + weather run to the end of the Phase 2 holdout window.
CONG_START, CONG_END = date(2025, 1, 1), date(2026, 4, 30)
FULL_START, FULL_END = date(2025, 1, 1), date(2026, 10, 29)
CRASH_RADIUS_M = 200.0
EARTH_R_M = 6_371_000.0

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": "redunator-phase2b-ingest/1.0"})


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def socrata_get(dataset, params, tries=4, timeout=300):
    """GET a Socrata resource (json or csv) with retries."""
    url = f"https://data.cityofchicago.org/resource/{dataset}.json"
    if params.pop("_csv", False):
        url = f"https://data.cityofchicago.org/resource/{dataset}.csv"
    last = None
    for i in range(tries):
        try:
            r = SESSION.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as e:  # noqa: BLE001 - retry transient failures
            last = e
            log(f"  Socrata {dataset} try {i + 1}/{tries} failed: "
                f"{type(e).__name__}: {str(e)[:120]}")
            time.sleep(10 * (i + 1))
    raise RuntimeError(f"Socrata {dataset} failed after {tries} tries: {last}")


def soql_query(dataset, soql, csv=False):
    params = {"$query": soql}
    if csv:
        params["_csv"] = True
    return socrata_get(dataset, params)


def month_bounds(start, end):
    """Yield (first, last) date pairs covering [start, end] by calendar month."""
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        first = date(y, m, 1)
        last = date(y, m, calendar.monthrange(y, m)[1])
        yield max(first, start), min(last, end)
        m += 1
        if m == 13:
            y, m = y + 1, 1


def haversine_m(lat1, lon1, lat2, lon2):
    """Vectorised haversine distance in metres (numpy arrays / scalars)."""
    p1, p2 = np.radians(lat1), np.radians(lat2)
    dphi = np.radians(lat2 - lat1)
    dlmb = np.radians(lon2 - lon1)
    a = np.sin(dphi / 2) ** 2 + np.cos(p1) * np.cos(p2) * np.sin(dlmb / 2) ** 2
    return 2 * EARTH_R_M * np.arcsin(np.sqrt(np.clip(a, 0, 1)))


# ---------------------------------------------------------------- steps

def step_segments():
    """Segment geometry from the deprecated snapshot (1,257 rows, small)."""
    log("step segments: pulling segment geometry from n4j6-wkkf ...")
    rows, offset = [], 0
    while True:
        r = socrata_get(SEG_SNAP, {"$limit": 50000, "$offset": offset,
                                    "$order": "segmentid"})
        batch = r.json()
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        if len(batch) < 50000:
            break
    df = pd.DataFrame(rows)
    # Socrata-mangled names in this dataset: start_lon = start longitude,
    # _lif_lat = start latitude, _lit_lon = end longitude, _lit_lat = end latitude.
    df = df.rename(columns={
        "segmentid": "segment_id", "start_lon": "start_longitude",
        "_lif_lat": "start_latitude", "_lit_lon": "end_longitude",
        "_lit_lat": "end_latitude", "_fromst": "from_street",
        "_tost": "to_street", "_direction": "direction",
        "_length": "length_miles", "_strheading": "street_heading",
        "_traffic": "snapshot_speed", "_last_updt": "snapshot_last_update",
    })
    for c in ["segment_id", "start_latitude", "start_longitude",
              "end_latitude", "end_longitude", "length_miles",
              "snapshot_speed"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["mid_latitude"] = (df["start_latitude"] + df["end_latitude"]) / 2
    df["mid_longitude"] = (df["start_longitude"] + df["end_longitude"]) / 2
    df = df.sort_values("segment_id").reset_index(drop=True)
    out = RAW / "segments_snapshot.csv"
    df.to_csv(out, index=False)
    log(f"  wrote {out} ({len(df)} segments, "
        f"snapshot_last_update range "
        f"{df['snapshot_last_update'].min()} .. {df['snapshot_last_update'].max()})")
    return df


def load_cameras():
    pq = BASE / "data" / "processed" / "red_light_features.parquet"
    df = pd.read_parquet(pq, columns=["camera_id", "latitude", "longitude"])
    g = df.drop_duplicates("camera_id").copy()
    g["camera_id"] = g["camera_id"].astype(str)
    return g.sort_values("camera_id").reset_index(drop=True)


def step_map():
    """Map each camera (with coordinates) to its nearest congestion segment."""
    log("step map: camera -> nearest segment ...")
    seg_path = RAW / "segments_snapshot.csv"
    segs = pd.read_csv(seg_path) if seg_path.exists() else step_segments()
    cams = load_cameras()
    with_coords = cams.dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    log(f"  {len(cams)} cameras, {len(with_coords)} with coordinates, "
        f"{len(cams) - len(with_coords)} without: "
        f"{cams[cams['latitude'].isna()]['camera_id'].tolist()}")
    rows = []
    for _, cam in with_coords.iterrows():
        d_mid = haversine_m(cam["latitude"], cam["longitude"],
                            segs["mid_latitude"].values,
                            segs["mid_longitude"].values)
        d_start = haversine_m(cam["latitude"], cam["longitude"],
                              segs["start_latitude"].values,
                              segs["start_longitude"].values)
        d_end = haversine_m(cam["latitude"], cam["longitude"],
                            segs["end_latitude"].values,
                            segs["end_longitude"].values)
        d_min = np.minimum(np.minimum(d_mid, d_start), d_end)
        i = int(np.argmin(d_min))
        s = segs.iloc[i]
        rows.append({
            "camera_id": cam["camera_id"],
            "latitude": cam["latitude"], "longitude": cam["longitude"],
            "segment_id": int(s["segment_id"]), "segment_street": s["street"],
            "segment_direction": s["direction"],
            "distance_to_midpoint_m": round(float(d_mid[i]), 1),
            "distance_to_segment_m": round(float(d_min[i]), 1),
        })
    m = pd.DataFrame(rows)
    for _, cam in cams[cams["latitude"].isna()].iterrows():
        m = pd.concat([m, pd.DataFrame([{
            "camera_id": cam["camera_id"], "latitude": np.nan,
            "longitude": np.nan, "segment_id": np.nan,
            "segment_street": np.nan, "segment_direction": np.nan,
            "distance_to_midpoint_m": np.nan, "distance_to_segment_m": np.nan,
        }])], ignore_index=True)
    m = m.sort_values("camera_id").reset_index(drop=True)
    out = PROC / "camera_segment_map.csv"
    m.to_csv(out, index=False)
    mapped = m.dropna(subset=["segment_id"])
    log(f"  wrote {out} ({len(m)} cameras, {len(mapped)} mapped, "
        f"{mapped['segment_id'].nunique()} distinct segments, "
        f"median distance {mapped['distance_to_segment_m'].median():.0f} m, "
        f"max {mapped['distance_to_segment_m'].max():.0f} m, "
        f">500 m: {(mapped['distance_to_segment_m'] > 500).sum()})")
    return m


def _pull_hourly_aggregates(dataset, id_col, speed_pred, start, end,
                            id_values, out_path):
    """Pull SELECT day/hour/id, avg(speed), count(*) GROUP BY ... in
    monthly chunks (CSV), appending to out_path. Returns row count."""
    frames = []
    total = 0
    id_list = ",".join(f"'{v}'" for v in id_values)
    for first, last in month_bounds(start, end):
        where = (f"{id_col} IN ({id_list}) AND {speed_pred} AND "
                 f"time between '{first}T00:00:00' and '{last}T23:59:59'")
        offset = 0
        while True:
            soql = (f"SELECT date_trunc_ymd(time) AS day, hour, {id_col}, "
                    f"avg(speed) AS mean_speed, count(*) AS n_reads "
                    f"WHERE {where} GROUP BY date_trunc_ymd(time), hour, {id_col} "
                    f"ORDER BY day, hour, {id_col} LIMIT 50000 OFFSET {offset}")
            r = soql_query(dataset, soql, csv=True)
            text = r.text
            lines = text.strip().splitlines()
            n = max(len(lines) - 1, 0)  # minus header
            if n == 0:
                break
            import io
            chunk = pd.read_csv(io.StringIO(text))
            frames.append(chunk)
            total += len(chunk)
            offset += len(chunk)
            if len(chunk) < 50000:
                break
        log(f"  {dataset} {first}..{last}: cumulative {total} rows")
    if not frames:
        log(f"  WARNING: no rows returned for {dataset}")
        return 0
    df = pd.concat(frames, ignore_index=True)
    df["day"] = pd.to_datetime(df["day"]).dt.date
    df["timestamp"] = pd.to_datetime(df["day"].astype(str)) + \
        pd.to_timedelta(df["hour"].astype(int), unit="h")
    df = df.sort_values(["timestamp", id_col]).reset_index(drop=True)
    df.to_csv(out_path, index=False)
    log(f"  wrote {out_path} ({len(df)} rows, "
        f"{df['timestamp'].min()} .. {df['timestamp'].max()})")
    return len(df)


def step_congestion():
    """Hourly congestion for camera-nearest segments (server-side agg)."""
    log("step congestion: hourly aggregates for camera segments ...")
    m = pd.read_csv(PROC / "camera_segment_map.csv")
    seg_ids = sorted(m.dropna(subset=["segment_id"])["segment_id"]
                     .astype(int).unique().tolist())
    log(f"  {len(seg_ids)} distinct camera-nearest segments")
    return _pull_hourly_aggregates(
        SEG_HIST, "segment_id", "speed >= 0", CONG_START, CONG_END,
        seg_ids, RAW / "congestion_segments_hourly_raw.csv")


def step_region():
    """Hourly congestion for all 29 regions (server-side agg, fallback)."""
    log("step region: hourly aggregates for all regions ...")
    region_ids = list(range(1, 30))
    return _pull_hourly_aggregates(
        REG_HIST, "region_id", "speed > 0", CONG_START, CONG_END,
        region_ids, RAW / "congestion_regions_hourly_raw.csv")


def step_crashes():
    """Raw crash rows for the full window, paged $where download."""
    log(f"step crashes: pulling {CRASHES} {FULL_START}..{FULL_END} ...")
    cols = ["crash_record_id", "crash_date", "crash_hour", "latitude",
            "longitude", "crash_type", "first_crash_type",
            "most_severe_injury", "injuries_total", "injuries_fatal",
            "prim_contributory_cause"]
    where = (f"crash_date between '{FULL_START}T00:00:00' and "
             f"'{FULL_END}T23:59:59'")
    rows, offset = [], 0
    while True:
        params = {"$select": ",".join(cols), "$where": where,
                  "$order": "crash_date", "$limit": 50000, "$offset": offset}
        batch = socrata_get(CRASHES, params).json()
        if not batch:
            break
        rows.extend(batch)
        offset += len(batch)
        log(f"  ... {offset} rows")
        if len(batch) < 50000:
            break
    df = pd.DataFrame(rows, columns=cols)
    df["crash_date"] = pd.to_datetime(df["crash_date"])
    for c in ["crash_hour", "latitude", "longitude", "injuries_total",
              "injuries_fatal"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df = df.sort_values("crash_date").reset_index(drop=True)
    out = RAW / f"crashes_{FULL_START:%Y%m%d}_{FULL_END:%Y%m%d}.csv"
    df.to_csv(out, index=False)
    log(f"  wrote {out} ({len(df)} rows, "
        f"{df['crash_date'].min()} .. {df['crash_date'].max()}, "
        f"with lat/lon: {df['latitude'].notna().sum()})")
    return df


def step_aggregate():
    """Hourly crash counts within 200 m of each camera (chunked)."""
    log(f"step aggregate: hourly crashes within {CRASH_RADIUS_M:.0f} m ...")
    raw_path = RAW / f"crashes_{FULL_START:%Y%m%d}_{FULL_END:%Y%m%d}.csv"
    cams = load_cameras().dropna(subset=["latitude", "longitude"]).reset_index(drop=True)
    cam_lat = cams["latitude"].values
    cam_lon = cams["longitude"].values
    cam_ids = cams["camera_id"].values
    # full hourly index for the window (local, naive America/Chicago)
    hours = pd.date_range(f"{FULL_START} 00:00", f"{FULL_END} 23:00", freq="h")
    start_ts = pd.Timestamp(f"{FULL_START} 00:00")
    n_hours = len(hours)
    counts = np.zeros((len(cams), n_hours), dtype=np.int32)
    fatal = np.zeros((len(cams), n_hours), dtype=np.int32)
    inj = np.zeros((len(cams), n_hours), dtype=np.int32)
    city = np.zeros(n_hours, dtype=np.int32)
    n_raw = n_geo = 0
    for chunk in pd.read_csv(raw_path, chunksize=20000,
                             parse_dates=["crash_date"]):
        n_raw += len(chunk)
        chunk = chunk.dropna(subset=["latitude", "longitude"]).copy()
        n_geo += len(chunk)
        chunk["hour_floor"] = chunk["crash_date"].dt.floor("h")
        # hour position within the window, computed arithmetically
        h_all = ((chunk["hour_floor"] - start_ts).dt.total_seconds()
                 // 3600).astype(int).values
        # citywide hourly counts
        vc = chunk["hour_floor"].value_counts()
        for t, v in vc.items():
            j = int((t - start_ts).total_seconds() // 3600)
            if 0 <= j < n_hours:
                city[j] += int(v)
        # per-camera: chunk (<=20k) x cameras (361) haversine matrix
        d = haversine_m(chunk["latitude"].values[:, None],
                        chunk["longitude"].values[:, None],
                        cam_lat[None, :], cam_lon[None, :])
        hit = d <= CRASH_RADIUS_M
        if hit.any():
            rows_idx, cols_idx = np.where(hit)
            h_pos = h_all[rows_idx]
            ok = (h_pos >= 0) & (h_pos < n_hours)
            rows_idx, cols_idx, h_pos = (rows_idx[ok], cols_idx[ok],
                                         h_pos[ok])
            np.add.at(counts, (cols_idx, h_pos), 1)
            # injuries/fatalities of each hit crash, attributed to the camera
            fvals = chunk["injuries_fatal"].fillna(0).values[rows_idx].astype(int)
            ivals = chunk["injuries_total"].fillna(0).values[rows_idx].astype(int)
            np.add.at(fatal, (cols_idx, h_pos), fvals)
            np.add.at(inj, (cols_idx, h_pos), ivals)
    log(f"  raw rows read: {n_raw}, with lat/lon: {n_geo}")
    # long-form per-camera parquet (only non-zero hours to keep it small,
    # plus a full hourly grid is reconstructable from the hour index)
    ci, hi = np.where(counts > 0)
    per_cam = pd.DataFrame({
        "camera_id": cam_ids[ci],
        "timestamp": hours[hi],
        "crash_count": counts[ci, hi].astype(int),
        "injuries_fatal": fatal[ci, hi].astype(int),
        "injuries_total": inj[ci, hi].astype(int),
    })
    out = PROC / "crashes_hourly_per_camera.parquet"
    per_cam.to_parquet(out, index=False)
    citywide = pd.DataFrame({"timestamp": hours, "crash_count": city})
    out2 = PROC / "crashes_hourly_citywide.parquet"
    citywide.to_parquet(out2, index=False)
    log(f"  wrote {out} ({len(per_cam)} camera-hours with >=1 crash, "
        f"total attributed crashes {int(per_cam['crash_count'].sum())}, "
        f"cameras with >=1: {per_cam['camera_id'].nunique()})")
    log(f"  wrote {out2} ({len(citywide)} hours, "
        f"total {int(citywide['crash_count'].sum())} crashes)")
    return per_cam, citywide


def step_weather():
    """Hourly Chicago weather from the Open-Meteo archive API (no key)."""
    log("step weather: Open-Meteo archive 2025-01-01..2026-10-29 ...")
    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        "latitude": 41.8781, "longitude": -87.6298,
        "start_date": str(FULL_START), "end_date": str(FULL_END),
        "hourly": "temperature_2m,precipitation,snowfall,wind_speed_10m",
        "timezone": "America/Chicago",
    }
    last = None
    for i in range(4):
        try:
            r = SESSION.get(url, params=params, timeout=300)
            r.raise_for_status()
            data = r.json()
            break
        except Exception as e:  # noqa: BLE001
            last = e
            log(f"  Open-Meteo try {i + 1}/4 failed: {e}")
            time.sleep(10 * (i + 1))
    else:
        raise RuntimeError(f"Open-Meteo failed: {last}")
    h = data["hourly"]
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(h["time"]),
        "temperature_2m_c": h["temperature_2m"],
        "precipitation_mm": h["precipitation"],
        "snowfall_cm": h["snowfall"],
        "wind_speed_10m_kmh": h["wind_speed_10m"],
    })
    raw_out = RAW / "weather_chicago_hourly.csv"
    df.to_csv(raw_out, index=False)
    out = PROC / "weather_hourly.parquet"
    df.to_parquet(out, index=False)
    expected = len(pd.date_range(f"{FULL_START} 00:00", f"{FULL_END} 23:00", freq="h"))
    gaps = expected - len(df)
    log(f"  wrote {out} ({len(df)} rows, {df['timestamp'].min()} .. "
        f"{df['timestamp'].max()}, expected {expected} hours, gaps {gaps})")
    return df


def step_process_congestion():
    """Reshape the raw congestion aggregates into processed parquet files."""
    log("step process-congestion: reshaping raw aggregates ...")
    raw_seg = RAW / "congestion_segments_hourly_raw.csv"
    if raw_seg.exists():
        df = pd.read_csv(raw_seg, parse_dates=["timestamp"])
        df = df.rename(columns={"segment_id": "segment_id",
                                "mean_speed": "mean_speed_mph"})
        m = pd.read_csv(PROC / "camera_segment_map.csv")
        # Many cameras share one segment (361 cameras -> 212 segments), so
        # the segment->camera relation is one-to-many: store the full list.
        seg2cams = (m.dropna(subset=["segment_id"])
                     .groupby("segment_id")["camera_id"]
                     .apply(lambda s: ";".join(sorted(s.astype(str)))))
        df["camera_ids"] = df["segment_id"].map(seg2cams)
        df["n_cameras"] = df["camera_ids"].str.split(";").str.len()
        out = PROC / "congestion_hourly.parquet"
        df.to_parquet(out, index=False)
        log(f"  wrote {out} ({len(df)} rows, "
            f"{df['timestamp'].min()} .. {df['timestamp'].max()}, "
            f"{df['segment_id'].nunique()} segments, "
            f"{df['camera_ids'].str.split(';').explode().nunique()} cameras, "
            f"mean_speed NaN share: {df['mean_speed_mph'].isna().mean():.3f})")
    raw_reg = RAW / "congestion_regions_hourly_raw.csv"
    if raw_reg.exists():
        df = pd.read_csv(raw_reg, parse_dates=["timestamp"])
        df = df.rename(columns={"mean_speed": "mean_speed_mph"})
        out = PROC / "congestion_region_hourly.parquet"
        df.to_parquet(out, index=False)
        log(f"  wrote {out} ({len(df)} rows, "
            f"{df['timestamp'].min()} .. {df['timestamp'].max()}, "
            f"{df['region_id'].nunique()} regions)")


def step_verify():
    """Row counts + date ranges for every file written; sanity checks."""
    log("step verify:")
    summary = {}
    for p in sorted(list(RAW.glob("*")) + list(PROC.glob("*"))):
        if p.suffix not in (".csv", ".parquet"):
            continue
        try:
            df = pd.read_parquet(p) if p.suffix == ".parquet" else pd.read_csv(p, nrows=0)
            if p.suffix == ".csv":
                n = sum(1 for _ in open(p)) - 1
                info = {"rows": n}
            else:
                info = {"rows": len(df)}
                for col in ("timestamp", "crash_date", "day"):
                    if col in df.columns:
                        s = pd.to_datetime(df[col])
                        info[f"{col}_min"] = str(s.min())
                        info[f"{col}_max"] = str(s.max())
            summary[str(p.relative_to(BASE))] = info
            log(f"  {p.relative_to(BASE)}: {info}")
        except Exception as e:  # noqa: BLE001
            log(f"  {p}: UNREADABLE ({e})")
    # weather gap check
    w = PROC / "weather_hourly.parquet"
    if w.exists():
        df = pd.read_parquet(w)
        expected = len(pd.date_range(f"{FULL_START} 00:00", f"{FULL_END} 23:00", freq="h"))
        log(f"  weather: {len(df)} rows vs {expected} expected hours -> "
            f"{expected - len(df)} gaps")
        dups = df["timestamp"].duplicated().sum()
        log(f"  weather duplicate timestamps: {dups}")
    # congestion hour resolution check
    c = PROC / "congestion_hourly.parquet"
    if c.exists():
        df = pd.read_parquet(c)
        mins = pd.to_datetime(df["timestamp"]).dt.minute
        log(f"  congestion timestamps off-the-hour: {(mins != 0).sum()} / {len(df)}")
        log(f"  congestion n_reads: min {df['n_reads'].min()}, "
            f"median {df['n_reads'].median()}, max {df['n_reads'].max()} "
            f"(max 6 ten-min slices per hour)")
    # crash hour distribution sanity
    city = PROC / "crashes_hourly_citywide.parquet"
    if city.exists():
        df = pd.read_parquet(city)
        df["hour"] = pd.to_datetime(df["timestamp"]).dt.hour
        by_hour = df.groupby("hour")["crash_count"].sum()
        log(f"  citywide crashes by hour (0-23): {by_hour.tolist()}")
        log(f"  peak hour: {int(by_hour.idxmax())} ({int(by_hour.max())}), "
            f"quietest: {int(by_hour.idxmin())} ({int(by_hour.min())})")
        log(f"  citywide total: {int(df['crash_count'].sum())}")
    (BASE / "data" / "hourly" / "INGEST_SUMMARY.json").write_text(
        json.dumps(summary, indent=1, default=str))
    return summary


STEPS = {
    "segments": step_segments,
    "map": step_map,
    "congestion": step_congestion,
    "region": step_region,
    "crashes": step_crashes,
    "aggregate": step_aggregate,
    "weather": step_weather,
    "process-congestion": step_process_congestion,
    "verify": step_verify,
}

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--steps", default=",".join(STEPS),
                    help="comma-separated steps: " + ",".join(STEPS))
    ap.add_argument("--list-steps", action="store_true")
    args = ap.parse_args()
    if args.list_steps:
        print("\n".join(STEPS))
        sys.exit(0)
    RAW.mkdir(parents=True, exist_ok=True)
    PROC.mkdir(parents=True, exist_ok=True)
    for name in [s.strip() for s in args.steps.split(",") if s.strip()]:
        if name not in STEPS:
            sys.exit(f"unknown step: {name} (see --list-steps)")
        STEPS[name]()
    log("done.")
