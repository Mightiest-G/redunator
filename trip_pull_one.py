#!/usr/bin/env python3
"""Phase 2c — pull ONE trip chunk as a fresh, short-lived process.

Usage:
  python trip_pull_one.py <source> <dataset> <label> <start> <end> [tag]

Why this exists: the long-lived ingest workers' connections to the
egress proxy degrade over time ("Remote end closed connection" on
every attempt, even for queries that succeed instantly from a fresh
process). Each chunk pulled as its own process behaves like the
probes, which succeed reliably.

Writes data/hourly/raw/trip_chunks/<source>_<dataset>_<label>.parquet
and updates chunk_summary_<tag>.json. Skips if the parquet exists.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402

from trip_demand_ingest import CHUNK_DIR, fetch_range  # noqa: E402


def main():
    source, did, label, start, end = sys.argv[1:6]
    tag = sys.argv[6] if len(sys.argv) > 6 else ""
    tag_suffix = f"_{tag}" if tag else ""
    summary_path = os.path.join(CHUNK_DIR, f"chunk_summary{tag_suffix}.json")
    summary = {}
    if os.path.exists(summary_path):
        summary = json.load(open(summary_path))

    key = f"{source}|{did}|{label}"
    chunk_path = os.path.join(CHUNK_DIR, f"{source}_{did}_{label}.parquet")
    if os.path.exists(chunk_path):
        print(f"{label}: already exists, skipping", flush=True)
        return 0

    print(f"[{source} {did} {label} {start}..{end}]", flush=True)
    rows, secs, where, n_pages = fetch_range(did, start, end)
    df = pd.DataFrame(rows)
    if df.empty:
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
    summary[key] = {
        "source": source, "dataset": did, "month": label,
        "rows": int(len(df)), "trips": int(df["trips"].sum()),
        "null_area_trips": n_null, "seconds": round(secs, 1),
        "pages": n_pages,
        "min_day": str(df["day"].min()) if len(df) else None,
        "max_day": str(df["day"].max()) if len(df) else None,
        "where": where,
    }
    json.dump(summary, open(summary_path, "w"), indent=1)
    print(f"    rows={len(df)} trips={int(df['trips'].sum())} "
          f"null_area={n_null} pages={n_pages} ({secs:.1f}s)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
