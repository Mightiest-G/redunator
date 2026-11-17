#!/usr/bin/env python3
"""Validate the raw Redunator downloads and emit stats as JSON.

Chunked reads keep memory low. Outputs stats JSON next to the raw files;
INGEST_SUMMARY.md is rendered from it (see validate output).
"""
import json
import os
import sys

import pandas as pd

RAW = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "raw")

FILES = {
    "red_light": os.path.join(RAW, "red_light_violations.csv"),
    "speed": os.path.join(RAW, "speed_violations.csv"),
}

CHUNK = 200_000


def validate(key, path):
    print(f"[{key}] validating {path}", flush=True)
    total = 0
    cols = None
    nulls = None
    vmin = v_max = None
    vsum = 0
    vcount = 0
    dmin = dmax = None
    cams = set()
    inter = set()
    dup_keys = set()
    ndup = 0
    dates = set()
    for chunk in pd.read_csv(path, chunksize=CHUNK, low_memory=False,
                             dtype=str, keep_default_na=True):
        chunk.columns = [c.strip().lower().replace(" ", "_") for c in chunk.columns]
        if cols is None:
            cols = list(chunk.columns)
            nulls = {c: 0 for c in cols}
        total += len(chunk)
        for c in cols:
            nulls[c] += int(chunk[c].isna().sum())
        # violations numeric
        v = pd.to_numeric(chunk["violations"], errors="coerce")
        vv = v.dropna()
        if len(vv):
            vmin = vv.min() if vmin is None else min(vmin, vv.min())
            v_max = vv.max() if v_max is None else max(v_max, vv.max())
            vsum += float(vv.sum())
            vcount += len(vv)
        # dates
        d = pd.to_datetime(chunk["violation_date"], errors="coerce")
        dd = d.dropna()
        if len(dd):
            dmin = dd.min() if dmin is None else min(dmin, dd.min())
            dmax = dd.max() if dmax is None else max(dmax, dd.max())
            dates.update(dd.dt.strftime("%Y-%m-%d").unique().tolist())
        cams.update(chunk["camera_id"].dropna().unique().tolist())
        if "intersection" in chunk.columns:
            inter.update(chunk["intersection"].dropna().unique().tolist())
        # duplicate (camera_id, violation_date) pairs within/between chunks
        k = list(zip(chunk["camera_id"].fillna("").tolist(),
                     chunk["violation_date"].fillna("").tolist()))
        for pair in k:
            if pair in dup_keys:
                ndup += 1
            else:
                dup_keys.add(pair)
        print(f"[{key}] ... {total:,} rows", flush=True)
    stats = {
        "file": os.path.basename(path),
        "size_bytes": os.path.getsize(path),
        "rows": total,
        "columns": cols,
        "nulls": nulls,
        "violations": {
            "min": None if vmin is None else float(vmin),
            "max": None if v_max is None else float(v_max),
            "mean": None if vcount == 0 else vsum / vcount,
            "non_null": vcount,
        },
        "violation_date_min": None if dmin is None else str(dmin.date()),
        "violation_date_max": None if dmax is None else str(dmax.date()),
        "distinct_dates": len(dates),
        "distinct_camera_ids": len(cams),
        "distinct_intersections": len(inter),
        "duplicate_camera_date_pairs": ndup,
    }
    # date-gap check: expected daily coverage from min to max
    if dmin is not None and dmax is not None:
        expected = (dmax - dmin).days + 1
        stats["expected_daily_dates"] = expected
        stats["missing_dates_in_range"] = expected - len(dates)
    return stats


def main():
    out = {}
    for key, path in FILES.items():
        out[key] = validate(key, path)
    outpath = os.path.join(RAW, "ingest_stats.json")
    with open(outpath, "w") as f:
        json.dump(out, f, indent=2)
    print(json.dumps(out, indent=2))
    print(f"Wrote {outpath}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
