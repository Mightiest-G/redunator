#!/usr/bin/env python3
"""
Redunator Phase 1 (model improvements) — ITEM 1: join the speed cameras.

Builds speed-history features for the red-light cameras and writes
data/processed/red_light_features_speed.parquet (the v1 feature table
plus speed columns). Does NOT modify features.py, train.py, or the
v1 parquet.

Join method
-----------
SPATIAL nearest neighbour (the only defensible join — see findings):
  * Red-light cameras: 365, 361 with coordinates (1623, 2552, 2592,
    2662 have none, same as v1).
  * Speed cameras: 236, all with coordinates.
  * Nearest-neighbour distances (haversine-equivalent local-metre
    projection, cKDTree): median 692 m. Match rates:
      <= 100 m:   15/361  ( 4.2%)
      <= 200 m:   68/361  (18.8%)   <- one Chicago block (1/8 mile);
                                    same radius the hourly layer uses
                                    for crash attribution
      <= 500 m:  132/361  (36.6%)
      <= 1000 m: 248/361  (68.7%)
  * Address matching was TESTED and REJECTED: after normalising
    (upper-case, strip street-type suffixes), ZERO of 361 red-light
    cameras exactly match a speed-camera address. Red-light addresses
    are intersection addresses ("2200 S WESTERN AVENUE"); speed
    addresses are mid-block point addresses ("4124 W FOSTER AVE").
    They name different things, so no address join exists to find.
  * Two radii are carried as separate feature sets (200 m = same-block
    / same-corridor claim; 500 m = wider net, more coverage) plus a
    citywide-speed set (no neighbour needed). train_v2.py tests each.
  * Diagnostic (pre-train): per-camera lag-1d correlation between a
    red-light camera's daily violations and its nearest speed
    camera's daily violations is ~0.02 (mean 0.024 at R<=200 m,
    0.023 at R<=500 m) vs the camera's OWN lag-1 autocorrelation of
    0.37. Citywide speed lag-1 corr is 0.069. The signal, if any,
    is weak — the train step is the real test.

Speed features (same no-leakage rules as features.py)
----------------------------------------------------
For each red-light camera, the matched speed camera's daily violation
series (max-dedup per camera-day, missing day = 0, exactly the
features.py convention) is mapped onto the red-light camera's grid,
then lag_1d / roll_mean_7d / roll_mean_30d are computed SHIFTED
(past only) WITHIN each (camera_id, segment_id) — a segment restart
resets the speed lags exactly as it resets the red-light lags.
Also: speed_mean_pre_test (matched camera's mean daily speed before
2026-08-01), has_speed_neighbour (0/1), speed_dist_m (9999 if none).

October / staleness handling (speed data ends 2026-09-24)
---------------------------------------------------------
The speed series is EXTENDED past 2026-09-24 by holding each matched
camera's last observed daily count (2026-09-24 value, 0 if no row
that day) constant through the end of the grid (2026-10-29), so
October lag/roll features are carry-forward values, never invented
from the future and never silently zeroed. A `speed_age_days`
feature records staleness explicitly: 0 while speed data is fresh,
then 1, 2, ... days past 2026-09-24. The PRIMARY evaluation window
in train_v2.py is 2026-08-01 -> 2026-09-24, where every lag/roll
window is fully inside observed speed data; the full holdout
(-> 2026-10-29) is reported as a secondary, carry-forward run.

Usage:
    python speed_features.py            # builds join + feature parquet
    python speed_features.py --check    # + prints a verification block
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.spatial import cKDTree

BASE = Path(__file__).resolve().parent
RAW = BASE / "data" / "raw"
OUT_DIR = BASE / "data" / "processed"
V1_PATH = OUT_DIR / "red_light_features.parquet"
OUT_PATH = OUT_DIR / "red_light_features_speed.parquet"
JOIN_PATH = OUT_DIR / "speed_camera_join.csv"

SPEED_CSV = RAW / "speed_violations.csv"
TEST_START = pd.Timestamp("2026-08-01")
SPEED_END = pd.Timestamp("2026-09-24")  # last date in the speed file
RADII = (200, 500)  # metres; feature sets built for each
NO_NEIGHBOUR_DIST = 9999.0
LAT0 = np.radians(41.8781)  # Chicago centre, for local-metre projection


def load_speed_daily() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Deduped daily speed series per speed camera + per-camera identity."""
    sp = pd.read_csv(SPEED_CSV,
                     usecols=["CAMERA ID", "VIOLATION DATE", "VIOLATIONS",
                              "LATITUDE", "LONGITUDE"],
                     parse_dates=["VIOLATION DATE"], date_format="%m/%d/%Y")
    sp = sp.rename(columns={"CAMERA ID": "camera_id",
                            "VIOLATION DATE": "violation_date",
                            "VIOLATIONS": "violations",
                            "LATITUDE": "latitude",
                            "LONGITUDE": "longitude"})
    sp = sp[sp["camera_id"].notna()].copy()
    log = {"raw_rows": len(sp),
           "date_min": str(sp["violation_date"].min().date()),
           "date_max": str(sp["violation_date"].max().date())}
    # Max-dedup, same rule as features.py (never sum a re-issued row).
    g = sp.groupby(["camera_id", "violation_date"], observed=True)
    log["duplicate_pairs"] = int((g.size() > 1).sum())
    daily = g["violations"].max().reset_index().rename(
        columns={"violation_date": "date"})
    identity = (sp.groupby("camera_id")
                .agg(latitude=("latitude", "median"),
                     longitude=("longitude", "median"))
                .reset_index())
    log["speed_cameras"] = int(len(identity))
    log["speed_cameras_with_coords"] = int(identity["latitude"].notna().sum())
    return daily, identity, log


def build_join(rl_identity: pd.DataFrame,
               sp_identity: pd.DataFrame) -> pd.DataFrame:
    """Nearest speed camera per red-light camera (local-metre cKDTree)."""
    rl = rl_identity[rl_identity["latitude"].notna()].copy()
    sp = sp_identity[sp_identity["latitude"].notna()].copy()

    def xy(df, lat_col="latitude", lon_col="longitude"):
        return np.column_stack([
            df[lon_col].to_numpy() * np.cos(LAT0) * 111320.0,
            df[lat_col].to_numpy() * 111320.0])

    tree = cKDTree(xy(sp))
    dist, idx = tree.query(xy(rl), k=1)
    join = pd.DataFrame({
        "camera_id": rl["camera_id"].to_numpy(),
        "nearest_speed_camera": sp["camera_id"].to_numpy()[idx],
        "nearest_dist_m": np.round(dist, 1),
    })
    # The 4 coordinate-less red-light cameras get no neighbour at all.
    missing = rl_identity[rl_identity["latitude"].isna()][["camera_id"]].copy()
    missing["nearest_speed_camera"] = np.nan
    missing["nearest_dist_m"] = np.nan
    join = pd.concat([join, missing], ignore_index=True)
    for r in (100, 150, 200, 250, 300, 500, 750, 1000, 1500, 2000):
        join[f"within_{r}m"] = (join["nearest_dist_m"] <= r).astype("int8")
    return join


def _speed_series_for(daily: pd.DataFrame, cam: str,
                      grid_dates: pd.DatetimeIndex) -> pd.Series:
    """One speed camera's daily series on the grid dates, zero-filled,
    then carry-forwarded past SPEED_END (last observed daily count
    held constant). Returns a Series indexed by date."""
    s = (daily[daily["camera_id"] == cam]
         .set_index("date")["violations"].reindex(grid_dates).fillna(0))
    last_val = float(s.loc[SPEED_END]) if SPEED_END in s.index else 0.0
    s.loc[s.index > SPEED_END] = last_val
    return s


def add_speed_features(grid: pd.DataFrame, daily: pd.DataFrame,
                       join: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    log: dict = {}
    grid = grid.sort_values(["camera_id", "date"]).reset_index(drop=True)
    grid_dates = pd.DatetimeIndex(sorted(grid["date"].unique()))
    log["grid_dates"] = (str(grid_dates.min().date()),
                         str(grid_dates.max().date()))

    # Citywide daily speed total (sum over all speed cameras), same
    # zero-fill + carry-forward treatment.
    city = (daily.groupby("date")["violations"].sum()
            .reindex(grid_dates).fillna(0))
    last_city = float(city.loc[SPEED_END])
    city.loc[city.index > SPEED_END] = last_city
    city_map = city.to_dict()
    grid["spd_city_daily"] = grid["date"].map(city_map).astype("float32")

    # Matched-camera daily speed per red-light camera.
    nearest = dict(zip(join["camera_id"], join["nearest_speed_camera"]))
    dist = dict(zip(join["camera_id"], join["nearest_dist_m"]))
    matched_daily: dict[str, pd.Series] = {}
    for cam in grid["camera_id"].unique():
        sp_cam = nearest.get(cam)
        if isinstance(sp_cam, str) and not pd.isna(sp_cam):
            matched_daily[cam] = _speed_series_for(daily, sp_cam, grid_dates)
    log["red_light_cameras_with_matched_series"] = len(matched_daily)

    grid["speed_daily_any"] = np.nan  # placeholder, replaced per radius
    for radius in RADII:
        tag = f"spd{radius}"
        has = (join.set_index("camera_id")["nearest_dist_m"] <= radius)
        has_map = has.to_dict()
        grid[f"has_{tag}"] = grid["camera_id"].map(has_map).fillna(False).astype("int8")
        grid[f"{tag}_dist_m"] = grid["camera_id"].map(dist).fillna(NO_NEIGHBOUR_DIST)
        grid[f"{tag}_dist_m"] = grid[f"{tag}_dist_m"].astype("float32")
        # Daily matched speed (0 where no neighbour within radius).
        vals = np.zeros(len(grid), dtype=np.float32)
        date_arr = grid["date"].to_numpy()
        cam_arr = grid["camera_id"].to_numpy()
        has_arr = grid[f"has_{tag}"].to_numpy(dtype=bool)
        for cam, series in matched_daily.items():
            m = (cam_arr == cam) & has_arr
            if m.any():
                vals[m] = series.reindex(pd.DatetimeIndex(date_arr[m])).to_numpy(dtype=np.float32)
        grid[f"{tag}_daily"] = vals
        log[f"cameras_with_neighbour_{radius}m"] = int(
            (join["nearest_dist_m"] <= radius).sum())
        log[f"distinct_speed_cameras_used_{radius}m"] = int(
            join.loc[join["nearest_dist_m"] <= radius,
                     "nearest_speed_camera"].nunique())

    # Pre-test means (before 2026-08-01) for the matched / citywide series.
    pre = grid["date"] < TEST_START
    for radius in RADII:
        tag = f"spd{radius}"
        m = grid.loc[pre].groupby("camera_id")[f"{tag}_daily"].mean()
        grid[f"{tag}_mean_pre_test"] = grid["camera_id"].map(m).fillna(0).astype("float32")
    grid["spd_city_mean_pre_test"] = float(grid.loc[pre, "spd_city_daily"].mean())

    # Lags / rolling means — shifted, past only, WITHIN (camera, segment),
    # exactly the features.py rule. Computed on the daily series (which
    # already carries the post-2026-09-24 carry-forward).
    g = grid.groupby(["camera_id", "segment_id"], sort=False)
    for col_src, prefix in [(f"spd{r}_daily", f"spd{r}") for r in RADII] + \
                          [("spd_city_daily", "spd_city")]:
        grid[f"{prefix}_lag_1d"] = g[col_src].shift(1)
        grid[f"{prefix}_roll_mean_7d"] = (
            g[col_src].transform(
                lambda s: s.shift(1).rolling(7, min_periods=1).mean()))
        grid[f"{prefix}_roll_mean_30d"] = (
            g[col_src].transform(
                lambda s: s.shift(1).rolling(30, min_periods=1).mean()))
    # Staleness: 0 while speed data is fresh, then days past SPEED_END.
    grid["speed_age_days"] = (
        (grid["date"] - SPEED_END).dt.days.clip(lower=0).astype("int16"))

    # Drop the raw daily intermediates; keep only model-ready columns.
    drop = ["speed_daily_any"] + [f"spd{r}_daily" for r in RADII] + ["spd_city_daily"]
    grid = grid.drop(columns=drop)
    return grid, log


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    v1 = pd.read_parquet(V1_PATH)
    identity = (v1[["camera_id", "latitude", "longitude"]]
                .drop_duplicates("camera_id"))
    daily, sp_identity, log_sp = load_speed_daily()
    join = build_join(identity, sp_identity)
    JOIN_PATH.parent.mkdir(parents=True, exist_ok=True)
    join.to_csv(JOIN_PATH, index=False)
    print(f"wrote {JOIN_PATH}  rows={len(join)}")

    feats, log = add_speed_features(v1, daily, join)
    feats.to_parquet(OUT_PATH, index=False)
    print(f"wrote {OUT_PATH}  rows={len(feats):,}  cols={len(feats.columns)}")

    if args.check:
        print("\n--- verification ---")
        print(f"speed load: {log_sp}")
        print(f"join log: {log}")
        for r in (100, 200, 500, 1000):
            n = int((join["nearest_dist_m"] <= r).sum())
            print(f"  match rate R<={r}m: {n}/361 with coords "
                  f"({100*n/361:.1f}%), "
                  f"{join.loc[join['nearest_dist_m'] <= r, 'nearest_speed_camera'].nunique()} "
                  f"distinct speed cameras")
        print(f"  median nearest distance: {join['nearest_dist_m'].median():.0f} m")
        test = feats[feats["date"] >= TEST_START]
        fresh = test[test["date"] <= SPEED_END]
        print(f"test rows: {len(test):,}  fresh-speed rows (<= {SPEED_END.date()}): "
              f"{len(fresh):,}  stale rows: {len(test) - len(fresh):,}")
        print(f"speed_age_days in test: max {test['speed_age_days'].max()}, "
              f"mean {test['speed_age_days'].mean():.1f}")
        for c in ["spd500_lag_1d", "spd200_lag_1d", "spd_city_lag_1d"]:
            print(f"  {c}: NaN {int(feats[c].isna().sum())} (segment starts), "
                  f"mean {feats[c].mean():.2f}, max {feats[c].max():.1f}")
        print(f"  has_spd500 mean: {feats['has_spd500'].mean():.3f} "
              f"(share of rows with a <=500 m neighbour)")


if __name__ == "__main__":
    main()
