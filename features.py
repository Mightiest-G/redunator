#!/usr/bin/env python3
"""
Redunator Phase 2 — Feature engineering (step 2 of the data pipeline).

Builds the modeling table from the ingested Chicago red-light camera
violations data (data/raw/red_light_violations.csv) and writes
data/processed/red_light_features.parquet.

Target: daily red-light violations per camera (one row per camera-day).

Design decisions (all documented in data/processed/FEATURES_SUMMARY.md):
  * Dedup: keep the MAX violations per (camera_id, date). Never sum
    (that double-counts a re-issued portal row).
  * Null camera_id rows (289) are DROPPED — they cannot be attributed to
    any camera (spread over 112 intersections, 2014-07-01..2017-09-01,
    almost all violations == 1).
  * Zero-fill: the raw file only has rows on days with >= 1 violation
    (min violations == 1), so missing camera-days are 0-violation days.
    The table is expanded to a full camera x day grid, but ONLY within
    each camera's active segments: a camera's activity is split into
    segments whenever consecutive observed dates are > 90 days apart
    (camera removed / reinstalled / long offline). No rows are created
    in the gaps between segments, or before a camera's first appearance
    or after its last.
  * Coordinates: per-camera median lat/lon backfilled from rows that
    have them. 4 cameras never have coordinates anywhere (1623, 2552,
    2592, 2662) and keep NaN lat/lon.
  * Intersection: the modal (most frequent) intersection string per
    camera. Addresses are truncated at 25 chars in the source and are
    never used as keys.
  * Holidays: `holidays` package (v0.108), US federal holidays,
    INCLUDING federally observed dates (Saturday holiday -> preceding
    Friday, Sunday holiday -> following Monday); 15 observed dates
    fall inside the table's range.
  * Lags / rolling means use ONLY past days (shifted), so no leakage.
  * Camera aggregates (cam_mean_pre_test / cam_std_pre_test) are
    computed over the zero-filled series EXCLUDING the final 3 months
    (data before 2026-08-01), so the train worker can use them with the
    recommended train/test split without leakage from the test window.

The speed-camera file is validated as loadable (row count, date range,
dedup pairs) for context only — no speed features are built here; the
primary feature table is red-light only.

Usage:
    python features.py            # builds from data/raw, writes parquet
    python features.py --check    # same, plus prints a verification block
"""

from __future__ import annotations

import argparse
from pathlib import Path

import holidays
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
RAW = BASE / "data" / "raw"
OUT_DIR = BASE / "data" / "processed"
OUT_PATH = OUT_DIR / "red_light_features.parquet"

# --- constants -------------------------------------------------------------
RED_LIGHT_CSV = RAW / "red_light_violations.csv"
SPEED_CSV = RAW / "speed_violations.csv"
TEST_START = pd.Timestamp("2026-08-01")  # recommended test window start
GAP_SPLIT_DAYS = 90  # split a camera's activity if unobserved this long
SEASON_OF_MONTH = {12: 0, 1: 0, 2: 0,   # 0 winter, 1 spring, 2 summer, 3 fall
                   3: 1, 4: 1, 5: 1,
                   6: 2, 7: 2, 8: 2,
                   9: 3, 10: 3, 11: 3}

# Raw CSV headers are UPPER-CASE with spaces.
RED_COLS = {"INTERSECTION": "intersection", "CAMERA ID": "camera_id",
            "VIOLATION DATE": "violation_date", "VIOLATIONS": "violations",
            "LATITUDE": "latitude", "LONGITUDE": "longitude"}
SPEED_COLS = {"CAMERA ID": "camera_id", "VIOLATION DATE": "violation_date",
              "VIOLATIONS": "violations"}


# --- step 1: load + clean red-light ---------------------------------------
def load_red_light() -> tuple[pd.DataFrame, dict]:
    """Load raw red-light CSV, normalise keys, dedup, drop null cameras."""
    log: dict = {}
    df = pd.read_csv(RED_LIGHT_CSV, usecols=list(RED_COLS),
                     parse_dates=["VIOLATION DATE"], date_format="%m/%d/%Y")
    df = df.rename(columns=RED_COLS)
    log["raw_rows"] = len(df)

    # Camera IDs arrive as floats (1233.0); normalise to clean strings.
    null_cam = df["camera_id"].isna()
    log["null_camera_rows_dropped"] = int(null_cam.sum())
    log["null_camera_violations_lost"] = int(df.loc[null_cam, "violations"].sum())
    df = df.loc[~null_cam].copy()
    df["camera_id"] = df["camera_id"].astype(int).astype(str)

    # Dedup: keep MAX violations per (camera_id, date). Never sum.
    grouped = df.groupby(["camera_id", "violation_date"], observed=True)
    dup_sizes = grouped.size()
    dup_pairs = dup_sizes[dup_sizes > 1]
    log["duplicate_pairs"] = int(len(dup_pairs))
    log["duplicate_rows_involved"] = int(dup_pairs.sum())
    log["rows_removed_by_max_dedup"] = int(len(df) - len(dup_sizes))
    conflicting = (grouped["violations"].nunique() > 1)
    log["conflicting_pairs"] = int(conflicting.sum())
    log["exact_duplicate_pairs"] = int(len(dup_pairs) - conflicting.sum())
    df = grouped["violations"].max().reset_index()

    # Per-camera identity: modal intersection, median lat/lon.
    # (lat/lon + intersection are dropped by the groupby above, so
    #  re-derive them from the pre-dedup frame.)
    raw = pd.read_csv(RED_LIGHT_CSV, usecols=list(RED_COLS),
                      parse_dates=["VIOLATION DATE"], date_format="%m/%d/%Y")
    raw = raw.rename(columns=RED_COLS)
    raw = raw.loc[raw["camera_id"].notna()].copy()
    raw["camera_id"] = raw["camera_id"].astype(int).astype(str)
    inter = (raw.groupby("camera_id")["intersection"]
             .agg(lambda s: s.mode(dropna=True).iloc[0]))
    coords = raw.groupby("camera_id")[["latitude", "longitude"]].median()
    identity = pd.concat([inter.rename("intersection"), coords], axis=1)
    log["cameras"] = int(identity.shape[0])
    log["cameras_never_with_coords"] = sorted(
        identity[identity["latitude"].isna()].index.tolist())
    df = df.merge(identity, on="camera_id", how="left")
    log["deduped_rows"] = len(df)
    return df, log


# --- step 2: expand to the camera x day grid ------------------------------
def expand_grid(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Zero-fill missing camera-days within each camera's active segments.

    A segment is a run of observed dates with no gap > GAP_SPLIT_DAYS.
    Returns the gridded frame plus a log of what was added.
    """
    log: dict = {}
    df = df.sort_values(["camera_id", "violation_date"]).reset_index(drop=True)
    df["prev_date"] = df.groupby("camera_id")["violation_date"].shift(1)
    df["gap"] = (df["violation_date"] - df["prev_date"]).dt.days
    df["new_segment"] = df["gap"].isna() | (df["gap"] > GAP_SPLIT_DAYS)
    df["segment_id"] = df.groupby("camera_id")["new_segment"].cumsum()

    segs = (df.groupby(["camera_id", "segment_id"])["violation_date"]
            .agg(["min", "max", "count"]).reset_index())
    log["n_segments"] = int(len(segs))
    log["cameras_with_multiple_segments"] = int(
        (segs.groupby("camera_id").size() > 1).sum())
    log["split_gaps_over_90d"] = int((df["gap"] > GAP_SPLIT_DAYS).sum())

    # Build the full date grid per segment (vectorised per segment).
    seg_ranges = [pd.date_range(r["min"], r["max"], freq="D")
                  for _, r in segs.iterrows()]
    grid = pd.DataFrame({
        "camera_id": np.repeat(segs["camera_id"].to_numpy(),
                               [len(x) for x in seg_ranges]),
        "segment_id": np.repeat(segs["segment_id"].to_numpy(),
                                [len(x) for x in seg_ranges]),
        "date": np.concatenate([x.to_numpy() for x in seg_ranges]),
    })
    grid = grid.merge(
        df[["camera_id", "violation_date", "violations"]].rename(
            columns={"violation_date": "date"}),
        on=["camera_id", "date"], how="left")
    grid["is_observed"] = grid["violations"].notna().astype("int8")
    grid["violations"] = grid["violations"].fillna(0).astype("int32")
    log["grid_rows"] = int(len(grid))
    log["zero_filled_rows"] = int((grid["violations"] == 0).sum())
    log["observed_rows"] = int(grid["is_observed"].sum())
    return grid, log


# --- step 3: features -------------------------------------------------------
def add_features(grid: pd.DataFrame, identity: pd.DataFrame) -> pd.DataFrame:
    grid = grid.merge(identity, on="camera_id", how="left")
    d = grid["date"]
    grid["dow"] = d.dt.day_of_week.astype("int8")          # 0 = Monday
    grid["month"] = d.dt.month.astype("int8")
    grid["year"] = d.dt.year.astype("int16")
    grid["season"] = d.dt.month.map(SEASON_OF_MONTH).astype("int8")
    grid["is_weekend"] = (grid["dow"] >= 5).astype("int8")
    grid["day_of_year"] = d.dt.day_of_year.astype("int16")

    us_holidays = holidays.US(years=range(2014, 2027))
    uniq = pd.Series(grid["date"].unique())
    hol_lookup = {d: (us_holidays.get(d.date()) or "")
                  for d in uniq}
    grid["holiday_name"] = grid["date"].map(hol_lookup)
    grid["is_holiday"] = (grid["holiday_name"] != "").astype("int8")

    grid = grid.sort_values(["camera_id", "date"]).reset_index(drop=True)
    # Lags / rolling means are computed WITHIN each (camera, segment):
    # a segment's first day must NOT inherit a lag from the previous
    # segment (which ended > 90 days earlier).
    g = grid.groupby(["camera_id", "segment_id"], sort=False)
    first_seen = grid.groupby("camera_id")["date"].transform("min")
    grid["days_since_first"] = (grid["date"] - first_seen).dt.days.astype("int32")

    # Lags / rolling means — shifted so ONLY past days are used.
    v = grid["violations"]
    grid["lag_1d"] = g["violations"].shift(1)
    grid["roll_mean_7d"] = (g["violations"]
                            .transform(lambda s: s.shift(1).rolling(7, min_periods=1).mean()))
    grid["roll_mean_30d"] = (g["violations"]
                             .transform(lambda s: s.shift(1).rolling(30, min_periods=1).mean()))
    grid["lag_1d"] = grid["lag_1d"].astype("float32")
    grid["roll_mean_7d"] = grid["roll_mean_7d"].astype("float32")
    grid["roll_mean_30d"] = grid["roll_mean_30d"].astype("float32")

    # Camera aggregates over the PRE-TEST period only (before 2026-08-01),
    # so the train worker can use them without leaking the test window.
    pre = grid.loc[grid["date"] < TEST_START]
    agg = (pre.groupby("camera_id")["violations"]
           .agg(["mean", "std", "count"])
           .rename(columns={"mean": "cam_mean_pre_test",
                            "std": "cam_std_pre_test",
                            "count": "cam_n_days_pre_test"}))
    grid = grid.merge(agg, on="camera_id", how="left")
    grid["cam_mean_pre_test"] = grid["cam_mean_pre_test"].astype("float32")
    grid["cam_std_pre_test"] = grid["cam_std_pre_test"].astype("float32")
    grid["cam_n_days_pre_test"] = grid["cam_n_days_pre_test"].astype("Int32")
    return grid


# --- speed validation (context only) ---------------------------------------
def validate_speed() -> dict:
    sp = pd.read_csv(SPEED_CSV, usecols=list(SPEED_COLS),
                     parse_dates=["VIOLATION DATE"], date_format="%m/%d/%Y")
    sp = sp.rename(columns=SPEED_COLS)
    log = {"raw_rows": len(sp),
           "date_min": str(sp["violation_date"].min().date()),
           "date_max": str(sp["violation_date"].max().date()),
           "distinct_cameras": int(sp["camera_id"].nunique()),
           "null_camera_rows": int(sp["camera_id"].isna().sum())}
    sp = sp.dropna(subset=["camera_id"])
    g = sp.groupby(["camera_id", "violation_date"])["violations"].agg(["count", "nunique"])
    dup = g[g["count"] > 1]
    log["duplicate_pairs"] = int(len(dup))
    log["conflicting_pairs"] = int((dup["nunique"] > 1).sum())
    log["violations_mean"] = round(float(sp["violations"].mean()), 2)
    log["violations_max"] = int(sp["violations"].max())
    return log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="print a verification block after building")
    args = ap.parse_args()

    df, log1 = load_red_light()
    identity = df[["camera_id", "intersection", "latitude", "longitude"]].drop_duplicates("camera_id")
    grid, log2 = expand_grid(df[["camera_id", "violation_date", "violations"]])
    feats = add_features(grid, identity)
    speed_log = validate_speed()

    cols = ["camera_id", "segment_id", "date", "violations", "is_observed",
            "intersection", "latitude", "longitude",
            "dow", "month", "year", "season", "day_of_year",
            "is_weekend", "is_holiday", "holiday_name",
            "days_since_first", "lag_1d", "roll_mean_7d", "roll_mean_30d",
            "cam_mean_pre_test", "cam_std_pre_test", "cam_n_days_pre_test"]
    feats = feats[cols]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    feats.to_parquet(OUT_PATH, index=False)
    print(f"wrote {OUT_PATH}  rows={len(feats):,}  cols={len(feats.columns)}")

    if args.check:
        print("\n--- verification ---")
        print(f"date range: {feats['date'].min().date()} -> {feats['date'].max().date()}")
        print(f"rows: {len(feats):,}  cameras: {feats['camera_id'].nunique()}")
        print(f"test rows (>= 2026-08-01): {(feats['date'] >= TEST_START).sum():,} "
              f"across {feats.loc[feats['date'] >= TEST_START, 'camera_id'].nunique()} cameras")
        print(f"violations: mean={feats['violations'].mean():.3f} "
              f"max={feats['violations'].max()} zeros={int((feats['violations'] == 0).sum()):,}")
        print(f"lag_1d NaN (segment starts): {int(feats['lag_1d'].isna().sum())}")
        print(f"cam aggregates NaN (no pre-test history): "
              f"{int(feats['cam_mean_pre_test'].isna().sum())} rows, "
              f"{feats.loc[feats['cam_mean_pre_test'].isna(), 'camera_id'].nunique()} cameras")
        print(f"lat/lon NaN rows: {int(feats['latitude'].isna().sum())} "
              f"({feats.loc[feats['latitude'].isna(), 'camera_id'].nunique()} cameras)")
        print(f"holiday days flagged: {int(feats['is_holiday'].sum())}")
        print(f"load log: {log1}")
        print(f"grid log: {log2}")
        print(f"speed validation: {speed_log}")


if __name__ == "__main__":
    main()
