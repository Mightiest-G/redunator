#!/usr/bin/env python3
"""Redunator Phase 2c — camera -> community-area mapping, trip-demand
profiles, and staleness check (ESTIMATES).

Step 1 — mapping: point-in-polygon join of the 365 camera lat/lons
(from `data/processed/red_light_features.parquet`, first row per
camera) against `data/hourly/raw/community_areas.geojson` (dataset
igwz-8jzy, 77 areas, key `area_numbe`, CRS84 lon/lat). Cameras with no
coordinates (1623, 2552, 2592, 2662) and any camera falling outside
all polygons get the citywide demand profile as fallback.

Step 2 — profiles: per community area, 24-hour trip-volume profiles
split by day type (weekday / weekend / holiday), from the combined
taxi + rideshare (TNP) counts in
`data/hourly/processed/trip_hourly_by_area.parquet`. Blend = summed
counts (TNP dominates by volume, ~11x taxi, so the blend is
effectively the TNP shape with a small taxi tilt — documented in
DEMAND_BUILD.md). Null pickup areas (taxi 5.5%, TNP 8.5%) are
excluded: they cannot be mapped to a camera. Window 2023-01-01 ->
present (post-COVID stable regime; see staleness check). Weights sum
to 1 per (area, day_type); a citywide profile (area 0) is built the
same way as the fallback.

Holidays use the same convention as `features.py`: `holidays.US`
federal holidays INCLUDING federally observed dates (Saturday holiday
-> preceding Friday, Sunday holiday -> following Monday). A holiday
takes precedence over weekday/weekend.

Step 3 — staleness: per-area weekday/weekend profiles for 2019 vs
2023 vs 2024 vs 2025 (full calendar years, summed counts); report
mean/max |delta weight|.

Outputs:
  data/hourly/processed/camera_community_area.csv
  data/hourly/processed/demand_profiles.parquet
  data/hourly/demand_staleness.json
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import holidays
import numpy as np
import pandas as pd
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

BASE = Path(__file__).resolve().parent
HOURLY = BASE / "data" / "hourly"
PROCESSED = HOURLY / "processed"

FEATURES_PATH = BASE / "data" / "processed" / "red_light_features.parquet"
GEOJSON_PATH = HOURLY / "raw" / "community_areas.geojson"
TRIP_PATH = PROCESSED / "trip_hourly_by_area.parquet"
MAP_PATH = PROCESSED / "camera_community_area.csv"
PROFILES_PATH = PROCESSED / "demand_profiles.parquet"
STALENESS_PATH = HOURLY / "demand_staleness.json"

PROFILE_START = "2023-01-01"   # post-COVID stable regime (see staleness)
CITYWIDE_AREA = 0               # area code used for the citywide profile

_us_holidays = holidays.US(years=range(2019, 2027))


def day_type_for_date(date) -> str:
    """weekday / weekend / holiday (holiday takes precedence).

    Same holiday convention as features.py: holidays.US including
    federally observed dates.
    """
    d = pd.Timestamp(date)
    if d.date() in _us_holidays:
        return "holiday"
    return "weekend" if d.day_of_week >= 5 else "weekday"


def build_camera_map() -> pd.DataFrame:
    """Point-in-polygon join: cameras -> community area."""
    geo = json.loads(GEOJSON_PATH.read_text())
    polys = []
    for f in geo["features"]:
        props = f["properties"]
        polys.append({
            "area": int(props["area_numbe"]),
            "name": props["community"],
            "geom": shape(f["geometry"]),
        })
    geoms = [p["geom"] for p in polys]
    tree = STRtree(geoms)

    feats = pd.read_parquet(FEATURES_PATH, columns=["camera_id", "latitude", "longitude"])
    cams = (feats.groupby("camera_id")
            .agg(latitude=("latitude", "first"), longitude=("longitude", "first"))
            .reset_index())
    cams["camera_id"] = cams["camera_id"].astype(str)

    rows = []
    for _, r in cams.iterrows():
        cam = r["camera_id"]
        lat, lon = r["latitude"], r["longitude"]
        if pd.isna(lat) or pd.isna(lon):
            rows.append({"camera_id": cam, "community_area": None,
                         "community_name": None, "mapped": False,
                         "latitude": None, "longitude": None,
                         "distance_to_nearest_area_m": None,
                         "nearest_area": None,
                         "note": "no coordinates in feature table"})
            continue
        pt = Point(float(lon), float(lat))
        hit = None
        # STRtree.query returns bbox candidates; test containment
        # explicitly (covers, not contains, so a point exactly on a
        # boundary still maps). NOTE: predicate="contains" in
        # tree.query would test point-contains-polygon — always False.
        for idx in tree.query(pt):
            if geoms[int(idx)].covers(pt):
                hit = polys[int(idx)]
                break
        if hit is not None:
            rows.append({"camera_id": cam, "community_area": hit["area"],
                         "community_name": hit["name"], "mapped": True,
                         "latitude": float(lat), "longitude": float(lon),
                         "distance_to_nearest_area_m": 0.0,
                         "nearest_area": hit["area"], "note": ""})
            continue
        # Outside every polygon: nearest polygon + approx distance.
        dists = [g.distance(pt) for g in geoms]
        j = int(np.argmin(dists))
        deg = float(dists[j])
        # Rough degrees -> metres at this latitude (diagnostic only).
        m_per_deg = 111_320.0 * math.cos(math.radians(float(lat)))
        rows.append({"camera_id": cam, "community_area": None,
                     "community_name": None, "mapped": False,
                     "latitude": float(lat), "longitude": float(lon),
                     "distance_to_nearest_area_m": round(deg * m_per_deg, 1),
                     "nearest_area": polys[j]["area"],
                     "note": "outside all community-area polygons"})
    df = pd.DataFrame(rows)
    return df


def _profiles_from(df: pd.DataFrame, label: str) -> pd.DataFrame:
    """Aggregate (area, day_type, hour) trip counts -> weights summing to 1."""
    df = df.copy()
    df["day"] = pd.to_datetime(df["day"])
    df["day_type"] = df["day"].map(day_type_for_date)
    # Null pickup areas cannot be mapped to a camera: exclude.
    df = df[df["area"].notna()]
    df["area"] = df["area"].astype(int)

    agg = (df.groupby(["area", "day_type", "hour"])["trips"].sum()
           .reset_index())
    # Citywide rows (area 0), same aggregation over all areas.
    cw = (df.groupby(["day_type", "hour"])["trips"].sum().reset_index())
    cw["area"] = CITYWIDE_AREA
    agg = pd.concat([agg, cw], ignore_index=True)

    totals = agg.groupby(["area", "day_type"])["trips"].transform("sum")
    agg["weight"] = agg["trips"] / totals
    # Exact renormalisation: residual on the largest weight.
    for _, idx in agg.groupby(["area", "day_type"]).groups.items():
        sub = agg.loc[idx]
        agg.loc[sub["weight"].idxmax(), "weight"] += 1.0 - sub["weight"].sum()
    n_days = (df.groupby("day_type")["day"].nunique().to_dict())
    agg["n_days"] = agg["day_type"].map(n_days)
    agg["window"] = label
    return agg[["area", "day_type", "hour", "weight", "trips", "n_days",
                "window"]].sort_values(["area", "day_type", "hour"])


def build_profiles() -> pd.DataFrame:
    """Demand profiles for the chosen window (2023-01-01 -> present)."""
    trips = pd.read_parquet(TRIP_PATH)
    trips["day"] = pd.to_datetime(trips["day"])
    win = trips[trips["day"] >= PROFILE_START]
    prof = _profiles_from(win, f"{PROFILE_START} -> present")
    return prof


def staleness_check() -> dict:
    """Compare per-area profiles across years (weekday + weekend only).

    Full calendar years, summed taxi+TNP counts, null areas excluded.
    2019 = pre-COVID; 2023/2024/2025 = post-COVID stable regime.
    """
    trips = pd.read_parquet(TRIP_PATH)
    trips["day"] = pd.to_datetime(trips["day"])
    trips = trips[trips["area"].notna()].copy()
    trips["area"] = trips["area"].astype(int)
    trips["year"] = trips["day"].dt.year
    trips["day_type"] = trips["day"].map(day_type_for_date)
    trips = trips[trips["day_type"].isin(["weekday", "weekend"])]

    def year_profiles(year: int) -> pd.DataFrame:
        sub = trips[trips["year"] == year]
        agg = (sub.groupby(["area", "day_type", "hour"])["trips"].sum()
               .reset_index())
        tot = agg.groupby(["area", "day_type"])["trips"].transform("sum")
        agg["weight"] = agg["trips"] / tot
        return agg.pivot_table(index=["area", "day_type"], columns="hour",
                               values="weight", fill_value=0.0)

    years = [2019, 2023, 2024, 2025]
    profs = {y: year_profiles(y) for y in years}
    out = {"years_compared": years, "comparisons": {}}
    for a, b in [(2019, 2023), (2023, 2024), (2024, 2025), (2019, 2025)]:
        d = (profs[b] - profs[a]).abs()
        per_area = d.max(axis=1)  # worst hour drift per (area, day_type)
        worst = per_area.sort_values(ascending=False).head(5)
        out["comparisons"][f"{a}_vs_{b}"] = {
            "mean_abs_delta": float(d.to_numpy().mean()),
            "max_abs_delta": float(d.to_numpy().max()),
            "n_cells": int(d.size),
            "worst_areas": [
                {"area": int(idx[0]), "day_type": idx[1],
                 "max_abs_delta": round(float(v), 4)}
                for idx, v in worst.items()],
        }
    return out


def main():
    # 1. Camera -> community area.
    cmap = build_camera_map()
    cmap.to_csv(MAP_PATH, index=False)
    n_mapped = int(cmap["mapped"].sum())
    print(f"camera map: {n_mapped}/{len(cmap)} mapped, "
          f"{len(cmap) - n_mapped} unmapped")
    print("unmapped cameras:")
    for _, r in cmap[~cmap["mapped"]].iterrows():
        print(f"  {r['camera_id']}: {r['note']} "
              f"(nearest area {r['nearest_area']}, "
              f"dist {r['distance_to_nearest_area_m']} m)")
    print(f"areas covered by cameras: {cmap['community_area'].nunique()} of 77")

    # 2. Demand profiles.
    prof = build_profiles()
    prof.to_parquet(PROFILES_PATH, index=False)
    dev = prof.groupby(["area", "day_type"])["weight"].sum()
    print(f"demand profiles: {len(prof)} rows, "
          f"{prof['area'].nunique()} areas (incl. citywide=0), "
          f"max |sum-1| = {float(np.abs(dev - 1).max()):.2e}")
    print(f"  window {PROFILE_START} -> present; "
          f"trips in window: {int(prof['trips'].sum()):,}")
    for dt in ["weekday", "weekend", "holiday"]:
        cw = prof[(prof["area"] == 0) & (prof["day_type"] == dt)].sort_values("hour")
        print(f"  citywide {dt}: peak {int(cw.loc[cw['weight'].idxmax(), 'hour'])}:00 "
              f"({cw['weight'].max():.4f}), trough "
              f"{int(cw.loc[cw['weight'].idxmin(), 'hour'])}:00 "
              f"({cw['weight'].min():.4f}), n_days={int(cw['n_days'].iloc[0])}")

    # 3. Staleness.
    stale = staleness_check()
    STALENESS_PATH.write_text(json.dumps(stale, indent=2))
    print("staleness (per-area weekday/weekend profiles):")
    for k, v in stale["comparisons"].items():
        print(f"  {k}: mean |dW| {v['mean_abs_delta']:.4f}, "
              f"max |dW| {v['max_abs_delta']:.4f}")
        for w in v["worst_areas"][:3]:
            print(f"    worst: area {w['area']} {w['day_type']} "
                  f"max|dW| {w['max_abs_delta']}")
    print(f"wrote {MAP_PATH}\n      {PROFILES_PATH}\n      {STALENESS_PATH}")


if __name__ == "__main__":
    main()
