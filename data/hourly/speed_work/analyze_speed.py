#!/usr/bin/env python3
"""Phase 2d Worker 1 — build speed profiles + correlation / lag analysis.

Reads data/hourly/raw/trip_speed_chunks/speed_*.parquet|json (pulled by
speed_pull_one.py). Writes:
  data/hourly/processed/speed_profiles.parquet
  data/hourly/processed/trip_speed_hourly_by_area.parquet (holdout daily-hourly, valid trips)
  data/hourly/processed/trip_speed_citywide_daily_hourly.parquet
  data/hourly/SPEED_FINDINGS.md (written by hand after reviewing output;
    this script prints the numbers and saves speed_analysis.json)
Does NOT touch hourly_risk.py / hourly_model.py / any shipped model.
"""
import json, os
from pathlib import Path
import holidays
import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr

BASE = Path(__file__).resolve().parents[3]  # redunator/ (…/redunator/data/hourly/speed_work/analyze_speed.py)
HOURLY = BASE / "data" / "hourly"
CHUNK = HOURLY / "raw" / "trip_speed_chunks"
PROCESSED = HOURLY / "processed"

_us = holidays.US(years=range(2019, 2027))
def day_type(d):
    d = pd.Timestamp(d)
    if d.date() in _us: return "holiday"
    return "weekend" if d.day_of_week >= 5 else "weekday"

PROFILE_START, PROFILE_END = "2023-01-01", "2026-08-01"  # pre-holdout
# dow from Socrata date_extract_dow: 0=Sun .. 6=Sat
DOW2DAYTYPE = {0: "weekend", 1: "weekday", 2: "weekday", 3: "weekday",
               4: "weekday", 5: "weekday", 6: "weekend"}

def load_valid():
    frames = []
    for f in sorted(CHUNK.glob("speed_*_valid_*.parquet")):
        df = pd.read_parquet(f)
        df["dataset"] = f.name.split("_")[1]
        frames.append(df)
    v = pd.concat(frames, ignore_index=True)
    return v

def main():
    v = load_valid()
    print(f"valid chunks rows: {len(v):,}  valid trips: {int(v['n'].sum()):,}")
    print("datasets:", sorted(v["dataset"].unique()))
    # --- reconciliation vs 2c totals + edge classes -------------------------
    t2 = pd.read_parquet(PROCESSED / "trip_hourly_by_area.parquet")
    t2["day"] = pd.to_datetime(t2["day"])
    ranges = {"wrvz-psew": ("2023-01-01", "2024-01-01"),
              "ajtu-isnz": ("2024-01-01", "2026-08-01"),
              "m6dm-c72p": ("2019-01-01", "2023-01-01"),
              "n26f-ihde": ("2023-01-01", "2025-01-01"),
              "6dvr-xwnh": ("2025-01-01", "2026-08-01")}
    recon = {}
    for did, (s, e) in ranges.items():
        total2c = int(t2[(t2.dataset == did) & (t2.day >= s) & (t2.day < e)]["trips"].sum())
        valid = int(v[v["dataset"] == did]["n"].sum()) if len(v) and did in v["dataset"].unique() else 0
        edge = {}
        for cls in ("zero", "missing"):
            # edge counts may be split across files (edge_<cls>_<range>.json)
            tot = 0; found = False
            for p in sorted(CHUNK.glob(f"speed_{did}_edge_{cls}*.json")):
                tot += int(json.load(open(p))["n"]); found = True
            edge[cls] = tot if found else None
        # 'absurd' is DERIVED, not queried: the arithmetic $where makes its
        # count(*) query the slowest job and it proxy-drops; the partition is
        # exact, so absurd = total - valid - zero - missing. Where an absurd
        # file was pulled (taxi), it is kept as a cross-check.
        absurd_file = sum(int(json.load(open(p))["n"])
                          for p in sorted(CHUNK.glob(f"speed_{did}_edge_absurd*.json")))
        edge["absurd_file"] = absurd_file if list(CHUNK.glob(f"speed_{did}_edge_absurd*.json")) else None
        zm = [edge[c] for c in ("zero", "missing") if edge[c] is not None]
        edge["absurd"] = (total2c - valid - sum(zm)) if (len(zm) == 2 and valid) else None
        ssum = valid + sum(x for x in (edge.get("zero"), edge.get("missing"),
                                       edge.get("absurd")) if x is not None)
        recon[did] = {"range": [s, e], "total_2c": total2c, "valid": valid,
                      "edge": edge, "valid+edge": ssum,
                      "residual_total_minus_parts": total2c - ssum,
                      "valid_share": valid / total2c if total2c else None}
        print(did, "2c total", f"{total2c:,}", "valid", f"{valid:,}",
              "edge", edge, "residual", total2c - ssum)
    # --- profiles ------------------------------------------------------------
    # valid chunks in profile window only (wrvz 2023, ajtu, n26f, 6dvr);
    # m6dm (2019-2022) is out of window, kept for staleness context only.
    inwin = v[~v["dataset"].isin(["m6dm-c72p"])].copy()
    inwin["day_type_dow"] = inwin["dow"].map(DOW2DAYTYPE)
    # holiday aggregates: (hour, area) sums over holiday dates in window
    hol_frames = []
    for f in sorted(CHUNK.glob("speed_*_holiday.parquet")):
        df = pd.read_parquet(f)
        did = f.name.split("_")[1]
        if did == "m6dm-c72p":
            continue
        hol_frames.append(df)
    hol = pd.concat(hol_frames, ignore_index=True) if hol_frames else None
    # weekday(dow) minus holidays, weekend(dow) minus holidays (holidays on
    # Sat/Sun are rare but subtracted the same way), holiday as pulled.
    prof_rows = []
    def agg_speed(df):
        g = (df.groupby(["hour"]) .agg(n=("n", "sum"), sum_miles=("sum_miles", "sum"),
                                       sum_seconds=("sum_seconds", "sum")).reset_index())
        g["mean_speed_mph"] = g["sum_miles"] / (g["sum_seconds"] / 3600.0)
        return g
    # per-area profiles: weekday / weekend / holiday
    # count holiday days in window from the holiday jsons (union of dates in window)
    import datetime as dt
    hol_dates = set()
    for d in ("wrvz-psew", "ajtu-isnz", "n26f-ihde", "6dvr-xwnh"):
        for x in json.load(open(Path(__file__).parent / f"holidays_{d}.json")):
            if PROFILE_START <= x < PROFILE_END: hol_dates.add(x)
    n_days = {}
    d0, d1 = pd.Timestamp(PROFILE_START), pd.Timestamp(PROFILE_END)
    days = pd.date_range(d0, d1 - pd.Timedelta(days=1), freq="D")
    for dtp in ("weekday", "weekend", "holiday"):
        n_days[dtp] = int(sum(1 for d in days if day_type(d) == dtp))
    print("profile window days:", n_days, "holiday dates:", len(hol_dates))
    areas = sorted(inwin.loc[inwin["area"].notna(), "area"].unique().astype(int))
    rows = []
    # holiday (hour, area) aggregates -> subtract from dow aggregates
    hol_by = {}
    if hol is not None:
        hol_g = hol.groupby(["area", "hour"]).agg(
            n=("n", "sum"), sum_miles=("sum_miles", "sum"),
            sum_seconds=("sum_seconds", "sum")).reset_index()
        for _, r in hol_g.iterrows():
            hol_by[(r["area"] if pd.notna(r["area"]) else None, int(r["hour"]))] = r
    def sub_hol(n, sm, ss, area, hour):
        h = hol_by.get((area, hour))
        if h is None: return n, sm, ss
        return n - int(h["n"]), sm - float(h["sum_miles"]), ss - float(h["sum_seconds"])
    for area in areas + [0]:
        sub = inwin if area == 0 else inwin[inwin["area"] == area]
        for dtp in ("weekday", "weekend"):
            dows = [d for d, t in DOW2DAYTYPE.items() if t == dtp]
            s2 = sub[sub["dow"].isin(dows)]
            g = s2.groupby("hour").agg(n=("n", "sum"), sum_miles=("sum_miles", "sum"),
                                       sum_seconds=("sum_seconds", "sum")).reset_index()
            for _, r in g.iterrows():
                h = int(r["hour"])
                n, sm, ss = sub_hol(int(r["n"]), float(r["sum_miles"]),
                                    float(r["sum_seconds"]),
                                    (area if area != 0 else None), h)
                rows.append({"area": area, "day_type": dtp, "hour": h,
                             "mean_speed_mph": sm / (ss / 3600.0) if ss > 0 else np.nan,
                             "n_trips": n, "sum_miles": sm, "sum_seconds": ss,
                             "n_days": n_days[dtp],
                             "window": f"{PROFILE_START} -> {PROFILE_END} (pre-holdout)"})
        # holiday
        if hol is not None:
            hs = hol if area == 0 else hol[hol["area"] == area]
            g = hs.groupby("hour").agg(n=("n", "sum"), sum_miles=("sum_miles", "sum"),
                                       sum_seconds=("sum_seconds", "sum")).reset_index()
            have = set()
            for _, r in g.iterrows():
                h = int(r["hour"]); have.add(h)
                rows.append({"area": area, "day_type": "holiday", "hour": h,
                             "mean_speed_mph": float(r["sum_miles"]) / (float(r["sum_seconds"]) / 3600.0),
                             "n_trips": int(r["n"]), "sum_miles": float(r["sum_miles"]),
                             "sum_seconds": float(r["sum_seconds"]),
                             "n_days": n_days["holiday"],
                             "window": f"{PROFILE_START} -> {PROFILE_END} (pre-holdout)"})
            for h in range(24):
                if h not in have:
                    rows.append({"area": area, "day_type": "holiday", "hour": h,
                                 "mean_speed_mph": np.nan, "n_trips": 0,
                                 "sum_miles": 0.0, "sum_seconds": 0.0,
                                 "n_days": n_days["holiday"],
                                 "window": f"{PROFILE_START} -> {PROFILE_END} (pre-holdout)"})
    prof = pd.DataFrame(rows).sort_values(["area", "day_type", "hour"])
    prof.to_parquet(PROCESSED / "speed_profiles.parquet", index=False)
    print(f"speed_profiles: {len(prof)} rows, {prof['area'].nunique()} areas (0=citywide)")
    cw = prof[prof["area"] == 0]
    for dtp in ("weekday", "weekend", "holiday"):
        s2 = cw[cw["day_type"] == dtp].sort_values("hour")
        print(f"  citywide {dtp}: min speed {s2['mean_speed_mph'].min():.2f} mph at "
              f"{int(s2.loc[s2['mean_speed_mph'].idxmin(),'hour'])}:00, max "
              f"{s2['mean_speed_mph'].max():.2f} at {int(s2.loc[s2['mean_speed_mph'].idxmax(),'hour'])}:00")
    # --- citywide daily-hourly speed series + crashes ------------------------
    ser_frames = []
    for f in sorted(CHUNK.glob("speed_*_series_*.parquet")):
        df = pd.read_parquet(f); df["dataset"] = f.name.split("_")[1]
        ser_frames.append(df)
    ser = pd.concat(ser_frames, ignore_index=True)
    ser["day"] = pd.to_datetime(ser["day"])
    city = (ser.groupby(["day", "hour"]).agg(n=("n", "sum"),
            sum_miles=("sum_miles", "sum"), sum_seconds=("sum_seconds", "sum"))
            .reset_index())
    city["mean_speed_mph"] = city["sum_miles"] / (city["sum_seconds"] / 3600.0)
    city["timestamp"] = city["day"] + pd.to_timedelta(city["hour"], unit="h")
    city.to_parquet(PROCESSED / "trip_speed_citywide_daily_hourly.parquet", index=False)
    # per-dataset speeds (taxi vs TNP comparison)
    for did, s2 in ser.groupby("dataset"):
        s2 = s2.copy()
        s2["mph"] = s2["sum_miles"] / (s2["sum_seconds"] / 3600.0)
        w = (s2["sum_miles"].sum() / (s2["sum_seconds"].sum() / 3600.0))
        print(f"  series {did}: overall mean speed {w:.2f} mph, trips {int(s2['n'].sum()):,}")
    # holdout per-area daily-hourly
    af = []
    for f in sorted(CHUNK.glob("speed_*_area_2026-*.parquet")):
        df = pd.read_parquet(f); df["dataset"] = f.name.split("_")[1]
        af.append(df)
    area_h = pd.concat(af, ignore_index=True) if af else pd.DataFrame()
    if len(area_h):
        area_h["day"] = pd.to_datetime(area_h["day"])
        area_h.to_parquet(PROCESSED / "trip_speed_hourly_by_area.parquet", index=False)
        print(f"holdout per-area speed rows: {len(area_h):,}")
    # --- correlations ---------------------------------------------------------
    crashes = pd.read_parquet(PROCESSED / "crashes_hourly_citywide.parquet")
    crashes["timestamp"] = pd.to_datetime(crashes["timestamp"])
    m = city.merge(crashes, on="timestamp", how="inner")
    print(f"citywide series merged rows: {len(m)} ({m['timestamp'].min()}..{m['timestamp'].max()})")
    m["slowness"] = 1.0 / m["mean_speed_mph"]
    m["congestion"] = m["mean_speed_mph"].max() - m["mean_speed_mph"]  # placeholder, replaced below
    free = m["mean_speed_mph"].max()
    m["congestion"] = free - m["mean_speed_mph"]
    out = {}
    for col in ("mean_speed_mph", "slowness", "congestion"):
        r = pearsonr(m[col], m["crash_count"])
        out[col] = {"pearson_r": float(r.statistic), "p": float(r.pvalue)}
        print(f"  series {col} vs crashes: Pearson r={r.statistic:.3f} p={r.pvalue:.2g}")
    # profile-level (24h) correlation: citywide speed profile (holdout day-type mix) vs crash share
    hold_dates = pd.date_range("2026-08-01", "2026-10-29", freq="D")
    mix = {dtp: int(sum(1 for d in hold_dates if day_type(d) == dtp))
           for dtp in ("weekday", "weekend", "holiday")}
    prof_mix = np.zeros(24)
    for dtp, nd in mix.items():
        s2 = cw[cw["day_type"] == dtp].sort_values("hour")
        prof_mix += nd * s2["mean_speed_mph"].to_numpy()
    prof_mix /= sum(mix.values())
    ch = crashes[(crashes["timestamp"] >= "2026-08-01") & (crashes["timestamp"] < "2026-10-30")].copy()
    ch["hour"] = ch["timestamp"].dt.hour
    obs = ch.groupby("hour")["crash_count"].sum().reindex(range(24)).to_numpy(dtype=float)
    obs_share = obs / obs.sum()
    for name, vec in (("speed_profile", prof_mix), ("slowness_profile", 1.0 / prof_mix),
                      ("congestion_profile", prof_mix.max() - prof_mix)):
        r = pearsonr(vec, obs_share)
        print(f"  profile(24h) {name} vs holdout crash share: r={r.statistic:.3f} p={r.pvalue:.2g}")
        out[f"profile_{name}"] = {"pearson_r": float(r.statistic), "p": float(r.pvalue)}
    out["profile_speed_by_hour"] = [round(float(x), 2) for x in prof_mix]
    out["holdout_crash_share_by_hour"] = [round(float(x), 4) for x in obs_share]
    out["holdout_crash_counts_by_hour"] = [float(x) for x in obs]
    out["free_flow_speed_mph"] = float(free)
    # hourly speed table (citywide, holdout mix) + crash counts
    print("  hour | speed | crashes(holdout citywide)")
    for h in range(24):
        print(f"  {h:02d}   | {prof_mix[h]:6.2f} | {obs[h]:7.0f}")
    # lag test (series): speed at h-1/h/h+1 vs crashes at h
    m = m.sort_values("timestamp").reset_index(drop=True)
    m["speed_lag1"] = m["mean_speed_mph"].shift(1)
    m["speed_lead1"] = m["mean_speed_mph"].shift(-1)
    m["slow_lag1"] = m["slowness"].shift(1)
    m["slow_lead1"] = m["slowness"].shift(-1)
    lag = {}
    for col in ("speed_lag1", "mean_speed_mph", "speed_lead1",
                "slow_lag1", "slowness", "slow_lead1"):
        sub = m.dropna(subset=[col])
        r = pearsonr(sub[col], sub["crash_count"])
        lag[col] = {"pearson_r": float(r.statistic), "p": float(r.pvalue), "n": int(len(sub))}
        print(f"  lag {col}: r={r.statistic:.3f} p={r.pvalue:.2g} n={len(sub)}")
    out["lag_citywide_series"] = lag
    # holdout-only lag
    mh = m[(m["timestamp"] >= "2026-08-01") & (m["timestamp"] < "2026-10-30")]
    lag_h = {}
    for col in ("speed_lag1", "mean_speed_mph", "speed_lead1",
                "slow_lag1", "slowness", "slow_lead1"):
        sub = mh.dropna(subset=[col])
        r = pearsonr(sub[col], sub["crash_count"])
        lag_h[col] = {"pearson_r": float(r.statistic), "p": float(r.pvalue), "n": int(len(sub))}
        print(f"  lag HOLDOUT {col}: r={r.statistic:.3f} p={r.pvalue:.2g} n={len(sub)}")
    out["lag_citywide_holdout"] = lag_h
    # demand lag (from existing 2c file, citywide daily-hourly)
    dem = t2.copy()
    dem["day"] = pd.to_datetime(dem["day"])
    dem = dem[(dem["day"] >= "2025-01-01") & (dem["day"] < "2026-10-30")]
    dc = dem.groupby(["day", "hour"])["trips"].sum().reset_index()
    dc["timestamp"] = dc["day"] + pd.to_timedelta(dc["hour"], unit="h")
    d = dc.merge(crashes, on="timestamp", how="inner").sort_values("timestamp").reset_index(drop=True)
    d["dem_lag1"] = d["trips"].shift(1); d["dem_lead1"] = d["trips"].shift(-1)
    lag_d = {}
    for col in ("dem_lag1", "trips", "dem_lead1"):
        sub = d.dropna(subset=[col])
        r = pearsonr(sub[col], sub["crash_count"])
        lag_d[col] = {"pearson_r": float(r.statistic), "p": float(r.pvalue), "n": int(len(sub))}
        print(f"  lag DEMAND {col}: r={r.statistic:.3f} p={r.pvalue:.2g} n={len(sub)}")
    dh = d[(d["timestamp"] >= "2026-08-01") & (d["timestamp"] < "2026-10-30")]
    lag_dh = {}
    for col in ("dem_lag1", "trips", "dem_lead1"):
        sub = dh.dropna(subset=[col])
        r = pearsonr(sub[col], sub["crash_count"])
        lag_dh[col] = {"pearson_r": float(r.statistic), "p": float(r.pvalue), "n": int(len(sub))}
        print(f"  lag DEMAND HOLDOUT {col}: r={r.statistic:.3f} p={r.pvalue:.2g} n={len(sub)}")
    out["lag_demand_series"] = lag_d
    out["lag_demand_holdout"] = lag_dh
    out["series_correlation"] = {k: v for k, v in out.items()
        if k in ("mean_speed_mph", "slowness", "congestion")}
    # per-area profile correlation (speed profile vs holdout crash share per area)
    cam_area = pd.read_csv(PROCESSED / "camera_community_area.csv")
    cam_area["camera_id"] = cam_area["camera_id"].astype(str)
    per_cam = pd.read_parquet(PROCESSED / "crashes_hourly_per_camera.parquet")
    per_cam["timestamp"] = pd.to_datetime(per_cam["timestamp"])
    per_cam = per_cam[(per_cam["timestamp"] >= "2026-08-01") & (per_cam["timestamp"] < "2026-10-30")]
    per_cam["hour"] = per_cam["timestamp"].dt.hour
    per_cam = per_cam.merge(cam_area[["camera_id", "community_area"]],
                            on="camera_id", how="left")
    area_rows = []
    for area, sub in per_cam.groupby("community_area"):
        if pd.isna(area): continue
        tot = sub["crash_count"].sum()
        if tot < 20: continue
        obs_a = sub.groupby("hour")["crash_count"].sum().reindex(range(24), fill_value=0).to_numpy(dtype=float)
        obs_a /= obs_a.sum()
        w = np.zeros(24)
        for dtp, nd in mix.items():
            s2 = prof[(prof["area"] == int(area)) & (prof["day_type"] == dtp)].sort_values("hour")
            if len(s2) == 24 and s2["mean_speed_mph"].notna().all():
                w += nd * s2["mean_speed_mph"].to_numpy()
            else:
                s2 = cw[cw["day_type"] == dtp].sort_values("hour")
                w += nd * s2["mean_speed_mph"].to_numpy()
        w /= sum(mix.values())
        for nm, vec in (("speed", w), ("slowness", 1.0 / w)):
            r = pearsonr(vec, obs_a)
            area_rows.append({"area": int(area), "n_crashes": float(tot),
                             "signal": nm, "pearson_r": float(r.statistic)})
    ar = pd.DataFrame(area_rows)
    per_area = {}
    for nm, s2 in ar.groupby("signal"):
        rs = s2["pearson_r"].to_numpy()
        per_area[nm] = {"n_areas": int(len(rs)), "median_r": float(np.median(rs)),
                        "mean_r": float(rs.mean()), "min_r": float(rs.min()),
                        "max_r": float(rs.max()), "n_r_gt_0.5": int((rs > 0.5).sum()),
                        "n_r_lt_neg0.5": int((rs < -0.5).sum())}
        print(f"  per-area profile {nm}: n={len(rs)} median r={np.median(rs):.3f} "
              f"mean {rs.mean():.3f} min {rs.min():.3f} max {rs.max():.3f}")
    out["per_area_profile"] = per_area
    out["per_area_rows"] = ar.to_dict("records")
    out["reconciliation"] = recon
    out["profile_window"] = {"start": PROFILE_START, "end": PROFILE_END,
                             "n_days": n_days, "n_areas": len(areas)}
    out["day_type_mix_holdout"] = mix
    json.dump(out, open(Path(__file__).parent / "speed_analysis.json", "w"), indent=2)
    print("wrote speed_analysis.json")

if __name__ == "__main__":
    main()
