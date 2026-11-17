#!/usr/bin/env python3
"""Redunator Phase 2c (Worker A) — combine monthly trip chunks and sanity-check.

Reads data/hourly/raw/trip_chunks/<source>_<dataset>_<YYYY-MM>.parquet,
concatenates per source, and writes:
  data/hourly/processed/taxi_hourly_by_area.parquet
  data/hourly/processed/tnp_hourly_by_area.parquet
  data/hourly/processed/trip_hourly_by_area.parquet   (both, + source col)

Sanity checks (printed + saved to TRIP_SANITY.json):
  - duplicate (day, hour, area) rows
  - day coverage: every calendar day in the pulled span present?
  - hours per day (24 expected; 23 on spring-forward days)
  - pickup_community_area null share (overall + per source + per dataset)
  - distinct areas present (expect 1..77)
  - citywide hour-of-day shape: peak / trough hour
  - per-source totals and date ranges
"""
import glob
import json
import os

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CHUNK_DIR = os.path.join(BASE_DIR, "data", "hourly", "raw", "trip_chunks")
PROC_DIR = os.path.join(BASE_DIR, "data", "hourly", "processed")


def load_source(source):
    files = sorted(glob.glob(os.path.join(CHUNK_DIR, f"{source}_*.parquet")))
    assert files, f"no chunks for {source}"
    df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
    df["day"] = pd.to_datetime(df["day"]).dt.date
    return df, files


def check(df, label):
    out = {"label": label, "rows": int(len(df))}
    dups = int(df.duplicated(subset=["day", "hour", "area"]).sum())
    out["duplicate_day_hour_area"] = dups
    out["total_trips"] = int(df["trips"].sum())
    out["null_area_trips"] = int(df.loc[df["area"].isna(), "trips"].sum())
    out["null_area_share"] = out["null_area_trips"] / max(out["total_trips"], 1)
    out["min_day"] = str(df["day"].min())
    out["max_day"] = str(df["day"].max())
    out["distinct_areas"] = sorted(
        int(a) for a in df["area"].dropna().unique())
    out["n_distinct_areas"] = len(out["distinct_areas"])
    out["missing_areas_1_77"] = [a for a in range(1, 78)
                                 if a not in out["distinct_areas"]]
    # day coverage
    all_days = pd.date_range(df["day"].min(), df["day"].max(), freq="D").date
    present = set(df["day"].unique())
    out["n_days_expected"] = len(all_days)
    out["n_days_present"] = len(present)
    out["missing_days"] = [str(d) for d in all_days if d not in present]
    # hours per day
    hpd = df.groupby("day")["hour"].nunique()
    out["hours_per_day_min"] = int(hpd.min())
    out["hours_per_day_max"] = int(hpd.max())
    out["days_with_lt_24_hours"] = int((hpd < 24).sum())
    out["days_with_lt_24_hours_list"] = [str(d) for d in hpd[hpd < 24].index]
    # hour-of-day shape (citywide)
    by_hour = df.groupby("hour")["trips"].sum()
    out["hour_shape"] = {str(int(h)): int(v) for h, v in by_hour.items()}
    out["peak_hour"] = int(by_hour.idxmax())
    out["trough_hour"] = int(by_hour.idxmin())
    out["peak_share"] = float(by_hour.max() / by_hour.sum())
    out["trough_share"] = float(by_hour.min() / by_hour.sum())
    # per-dataset totals
    out["by_dataset"] = {
        ds: {"rows": int(len(g)), "trips": int(g["trips"].sum()),
             "min_day": str(g["day"].min()), "max_day": str(g["day"].max())}
        for ds, g in df.groupby("dataset")
    }
    return out


def main():
    os.makedirs(PROC_DIR, exist_ok=True)
    report = {}
    combined = []
    for source in ["taxi", "tnp"]:
        df, files = load_source(source)
        print(f"{source}: {len(files)} chunks, {len(df)} rows", flush=True)
        df = df.sort_values(["day", "hour", "area"]).reset_index(drop=True)
        df.to_parquet(
            os.path.join(PROC_DIR, f"{source}_hourly_by_area.parquet"),
            index=False)
        report[source] = check(df, source)
        combined.append(df)
    both = pd.concat(combined, ignore_index=True).sort_values(
        ["source", "day", "hour", "area"]).reset_index(drop=True)
    both.to_parquet(os.path.join(PROC_DIR, "trip_hourly_by_area.parquet"),
                    index=False)
    report["combined"] = check(both, "combined")
    report["combined"]["rows"] = int(len(both))
    json.dump(report, open(os.path.join(BASE_DIR, "data", "hourly",
                                        "TRIP_SANITY.json"), "w"), indent=1)
    print(json.dumps(report, indent=1))


if __name__ == "__main__":
    main()
