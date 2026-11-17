#!/usr/bin/env python3
"""Redunator — monthly retraining automation (Phase 1, item 4a).

Re-runs the daily-model pipeline end to end — ingest -> features ->
train -> export — into a STAGING directory, compares the candidate
model against the SHIPPED model on the SAME holdout, and promotes the
candidate ONLY if it beats the shipped model on BOTH holdout metrics.
The shipped v1 files are NEVER overwritten: a promotion writes
versioned files (redunator_brain_v2.joblib/.json/.onnx, ...) and moves
the live-model pointer (live_model.json) to the new version.

Why staging copies with patched dates?
  * train.py / features.py / export_models.py hardcode their paths
    (BASE = the script's own directory) and their split dates
    (train <= 2026-07-31, test >= 2026-08-01). Running them in place
    would overwrite the live model files.
  * The split dates are also FROZEN: with fresh data, a fixed split
    would retrain on byte-identical train rows (features.py computes
    cam aggregates over pre-2026-08-01 data only), so the "retrain"
    would be a no-op. Each run therefore uses a ROLLING split that
    preserves the v1 protocol — time-based, ~90-day holdout, the most
    recent 90 days as test — with train_end = latest_data_date - 90d
    and test_start = train_end + 1d. The staging copies are patched
    (regex on the date constants only); the live scripts are never
    edited. features.py's TEST_START is patched too, so the
    cam_mean_pre_test aggregates still exclude the test window (no
    leakage), and its holidays year range is extended to cover the
    new data years.

Promotion rule (baked in, not a manual step):
  PROMOTE iff  candidate RMSE < shipped RMSE
           AND candidate MAE  < shipped MAE
           AND candidate RMSE < naive-baseline RMSE   (sanity guard)
  where "shipped" is the shipped model RE-SCORED on the candidate's
  own test set (same rows, same feature prep as train.py) — never a
  stale number from an older, shorter holdout. A tie refuses.
  Any step failure -> FAILED report, exit non-zero, no promotion
  (fail closed).

Usage:
  # Full monthly run (downloads ~270 MB, retrains RF + XGBoost):
  ~/workspace/redunator-venv/bin/python retrain_monthly.py

  # Dry run: no downloads, no training. Re-scores the shipped model on
  # the live feature table (should reproduce RMSE 3.3068 / MAE 2.1452)
  # and exercises the promotion gate against real + synthetic metrics:
  ~/workspace/redunator-venv/bin/python retrain_monthly.py --dry-run

  # Ad-hoc gate check (dry-run only): compare given candidate metrics:
  ~/workspace/redunator-venv/bin/python retrain_monthly.py --dry-run \
      --candidate-rmse 3.20 --candidate-mae 2.05

  --holdout-days N   rolling holdout length (default 90, as in v1)
  --skip-export      skip the staging export_models.py step
  --run-dir NAME     override the run directory name under retrain_runs/
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

BASE = Path(__file__).resolve().parent
VENV_PYTHON = Path.home() / "workspace" / "redunator-venv" / "bin" / "python"
RUNS_DIR = BASE / "retrain_runs"

SHIPPED_JOBLIB = BASE / "redunator_brain_v1.joblib"
SHIPPED_SIDECAR = BASE / "redunator_brain_v1.json"
SHIPPED_ONNX = BASE / "redunator_brain_v1.onnx"
SHIPPED_METRICS = BASE / "data" / "processed" / "model_metrics.json"
LIVE_FEATURES = BASE / "data" / "processed" / "red_light_features.parquet"
LIVE_POINTER = BASE / "live_model.json"

PIPELINE_SCRIPTS = ["ingest.py", "features.py", "train.py", "export_models.py"]
FEATURES = [
    "dow", "month", "year", "season", "day_of_year",
    "is_weekend", "is_holiday",
    "days_since_first",
    "lag_1d", "roll_mean_7d", "roll_mean_30d",
    "cam_mean_pre_test", "cam_std_pre_test", "cam_n_days_pre_test",
    "latitude", "longitude",
    "cam_code",
]
TARGET = "violations"
HOLDOUT_DAYS_DEFAULT = 90


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------
def log(msg: str, log_fh=None) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    if log_fh:
        log_fh.write(line + "\n")
        log_fh.flush()


def md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def shipped_metrics() -> dict:
    """Authoritative shipped metrics, from the v1 sidecar (cross-checked
    against data/processed/model_metrics.json by the caller)."""
    sidecar = json.loads(SHIPPED_SIDECAR.read_text())
    m = sidecar["metrics"]["random_forest"]
    return {"rmse": float(m["rmse"]), "mae": float(m["mae"])}


def next_version() -> int:
    """Next model version number: max existing redunator_brain_vN + 1."""
    versions = [1]
    for p in BASE.glob("redunator_brain_v*.joblib"):
        m = re.fullmatch(r"redunator_brain_v(\d+)\.joblib", p.name)
        if m:
            versions.append(int(m.group(1)))
    return max(versions) + 1


RUNS_TO_KEEP = 6


def prune_old_runs(log_fh) -> list[str]:
    """Delete run dirs under retrain_runs/ beyond the newest 6.

    Each full run is ~440 MB, so unbounded history grows fast.
    Decision 2026-11-17: keep the 6 most recent runs (~6 months of
    monthly runs); versioned model files and live_model.json are
    never touched, and the current run is always the newest so it
    is never pruned. Returns the pruned directory names.
    """
    if not RUNS_DIR.exists():
        return []
    dirs = sorted(p for p in RUNS_DIR.iterdir() if p.is_dir())
    pruned = []
    for old in dirs[:-RUNS_TO_KEEP] if len(dirs) > RUNS_TO_KEEP else []:
        shutil.rmtree(old)
        pruned.append(old.name)
        log(f"pruned old retrain run: {old.name}", log_fh)
    return pruned


# --------------------------------------------------------------------------
# scorer — mirrors train.py::load_and_split exactly
# --------------------------------------------------------------------------
def score_model_on_table(model_path: Path, parquet_path: Path,
                         train_end: str, test_start: str) -> dict:
    """Score a saved model on the test window of a feature table.

    Same prep as train.py: cam_code fit on sorted TRAIN cameras only,
    lag/rolling NaNs -> 0, lat/lon NaN -> train medians, predictions
    clipped at 0. A test camera with no train history is dropped and
    reported in ``cameras_skipped`` instead of failing the run
    (decision 2026-11-17: skip-and-log, not fail-closed — same rule
    as train.py::load_and_split, so the candidate and the shipped
    model are still scored on the same test set).
    """
    df = pd.read_parquet(parquet_path)
    df["date"] = pd.to_datetime(df["date"])
    train_mask = df["date"] <= train_end
    train_cams = sorted(df.loc[train_mask, "camera_id"].unique())
    new_cams = sorted(set(df["camera_id"]) - set(train_cams))
    if new_cams:
        df = df[~df["camera_id"].isin(new_cams)].copy()
        # Re-derive the mask: the drop leaves a non-contiguous index
        # and the old boolean Series would misalign against it.
        train_mask = df["date"] <= train_end
    code_map = {c: i for i, c in enumerate(train_cams)}
    df["cam_code"] = df["camera_id"].map(code_map).astype("int32")
    for col in ("lag_1d", "roll_mean_7d", "roll_mean_30d"):
        df[col] = df[col].fillna(0)
    med_lat = df.loc[train_mask, "latitude"].median()
    med_lon = df.loc[train_mask, "longitude"].median()
    df["latitude"] = df["latitude"].fillna(med_lat)
    df["longitude"] = df["longitude"].fillna(med_lon)
    test = df[df["date"] >= test_start]
    assert set(test["camera_id"]) <= set(train_cams), \
        "test contains a camera with no train history"
    model = joblib.load(model_path)
    pred = np.clip(model.predict(test[FEATURES].to_numpy(dtype=np.float32)),
                   0, None)
    y = test[TARGET].to_numpy(dtype=np.float32)
    err = pred - y
    return {
        "rmse": float(np.sqrt(np.mean(err ** 2))),
        "mae": float(np.mean(np.abs(err))),
        "test_rows": int(len(test)),
        "test_cameras": int(test["camera_id"].nunique()),
        "cameras_skipped": new_cams,
        "test_start": str(test["date"].min().date()),
        "test_end": str(test["date"].max().date()),
    }


# --------------------------------------------------------------------------
# promotion gate — the rule, in one place
# --------------------------------------------------------------------------
def decide_promotion(candidate: dict, shipped: dict,
                     naive: dict | None = None) -> tuple[bool, list[str]]:
    """PROMOTE only if the candidate beats the shipped model on BOTH
    holdout metrics (strictly — a tie refuses) and also beats the naive
    baseline on RMSE. Returns (promote, reasons)."""
    reasons = []
    rmse_better = candidate["rmse"] < shipped["rmse"]
    mae_better = candidate["mae"] < shipped["mae"]
    reasons.append(
        f"RMSE candidate {candidate['rmse']:.4f} vs shipped "
        f"{shipped['rmse']:.4f}: {'BETTER' if rmse_better else 'NOT better'}")
    reasons.append(
        f"MAE  candidate {candidate['mae']:.4f} vs shipped "
        f"{shipped['mae']:.4f}: {'BETTER' if mae_better else 'NOT better'}")
    naive_ok = True
    if naive is not None:
        naive_ok = candidate["rmse"] < naive["rmse"]
        reasons.append(
            f"RMSE candidate {candidate['rmse']:.4f} vs naive baseline "
            f"{naive['rmse']:.4f}: {'BETTER' if naive_ok else 'NOT better'}")
    promote = rmse_better and mae_better and naive_ok
    reasons.append("DECISION: " + ("PROMOTE" if promote else "DO NOT PROMOTE"))
    return promote, reasons


# --------------------------------------------------------------------------
# staging
# --------------------------------------------------------------------------
def _patch_dates(text: str, train_end: str, test_start: str,
                 holidays_until_year: int, path_name: str) -> str:
    """Patch split-date constants in a STAGING copy only. Fails loudly
    if a pattern is not found (so a silent no-op patch is impossible)."""
    orig = text
    if path_name == "train.py":
        text = re.sub(r'^TRAIN_END = "[^"]*"',
                      f'TRAIN_END = "{train_end}"', text, flags=re.M)
        text = re.sub(r'^TEST_START = "[^"]*"',
                      f'TEST_START = "{test_start}"', text, flags=re.M)
    elif path_name == "export_models.py":
        text = re.sub(r'^TRAIN_END = "[^"]*"',
                      f'TRAIN_END = "{train_end}"', text, flags=re.M)
        text = re.sub(r'^TEST_START = "[^"]*"',
                      f'TEST_START = "{test_start}"', text, flags=re.M)
    elif path_name == "features.py":
        text = re.sub(r'TEST_START = pd\.Timestamp\("[^"]*"\)',
                      f'TEST_START = pd.Timestamp("{test_start}")', text)
        text = re.sub(r'holidays\.US\(years=range\(2014, \d+\)\)',
                      f'holidays.US(years=range(2014, {holidays_until_year}))',
                      text)
    if text == orig:
        raise RuntimeError(f"date patch made no change to staging {path_name}")
    return text


def stage_scripts(staging: Path, train_end: str | None,
                  test_start: str | None, log_fh) -> None:
    staging.mkdir(parents=True, exist_ok=True)
    for name in PIPELINE_SCRIPTS:
        src = BASE / name
        dst = staging / name
        shutil.copy2(src, dst)
        if train_end is not None and name in (
                "train.py", "features.py", "export_models.py"):
            run_year = date.today().year
            patched = _patch_dates(dst.read_text(), train_end, test_start,
                                   run_year + 2, name)
            dst.write_text(patched)
            log(f"staged {name} (dates patched: train_end={train_end}, "
                f"test_start={test_start})", log_fh)
        else:
            log(f"staged {name} (unpatched)", log_fh)


def run_step(name: str, cmd: list[str], cwd: Path, log_fh,
             timeout: int = 7200) -> None:
    log(f"--- step: {name}: {' '.join(cmd)}", log_fh)
    t0 = time.time()
    proc = subprocess.run(cmd, cwd=str(cwd), timeout=timeout,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True)
    log_fh.write(proc.stdout)
    log_fh.flush()
    if proc.returncode != 0:
        raise RuntimeError(f"step '{name}' failed (exit {proc.returncode}); "
                           f"see run log")
    log(f"--- step {name} OK ({time.time() - t0:.0f}s)", log_fh)


def latest_raw_date(raw_csv: Path) -> date:
    """Max VIOLATION DATE in the freshly ingested red-light CSV."""
    df = pd.read_csv(raw_csv, usecols=["VIOLATION DATE"],
                     parse_dates=["VIOLATION DATE"], date_format="%m/%d/%Y")
    return df["VIOLATION DATE"].max().date()


def export_winner_onnx(staging: Path, log_fh) -> dict:
    """Export the staging winner to ONNX (skl2onnx) and verify it
    against native predictions on 2,000 test rows. Returns info dict;
    on failure returns {'ok': False, 'error': ...} and the caller
    promotes joblib+json only (flagged in the report)."""
    try:
        from skl2onnx import to_onnx
        import onnxruntime as ort

        sidecar = json.loads((staging / "redunator_brain_v1.json").read_text())
        feats = sidecar["features"]
        winner = sidecar["model"]
        df = pd.read_parquet(
            staging / "data" / "processed" / "red_light_features.parquet")
        df["date"] = pd.to_datetime(df["date"])
        test_start = sidecar["test_start"]
        test = df[df["date"] >= test_start]
        X_test = test[feats].to_numpy(dtype=np.float32)
        # NOTE: cam_code / fills mirror train.py; the staging parquet
        # was built by the staging train run, so re-apply them here.
        train_mask = df["date"] <= sidecar["train_end"]
        train_cams = sorted(df.loc[train_mask, "camera_id"].unique())
        code_map = {c: i for i, c in enumerate(train_cams)}
        df["cam_code"] = df["camera_id"].map(code_map).astype("int32")
        for col in ("lag_1d", "roll_mean_7d", "roll_mean_30d"):
            df[col] = df[col].fillna(0)
        df["latitude"] = df["latitude"].fillna(
            df.loc[train_mask, "latitude"].median())
        df["longitude"] = df["longitude"].fillna(
            df.loc[train_mask, "longitude"].median())
        test = df[df["date"] >= test_start]
        X_test = test[feats].to_numpy(dtype=np.float32)
        model = joblib.load(staging / "redunator_brain_v1.joblib")
        onx = to_onnx(model, X_test[:100].astype(np.float32),
                      options={"zipmap": False})
        out = staging / "candidate.onnx"
        out.write_bytes(onx.SerializeToString())
        sess = ort.InferenceSession(str(out), providers=["CPUExecutionProvider"])
        sample = X_test[:2000]
        onnx_pred = sess.run(None, {sess.get_inputs()[0].name: sample})[0]
        native_pred = model.predict(sample)
        max_diff = float(np.abs(onnx_pred.ravel() - native_pred).max())
        log(f"winner ONNX export OK ({winner}): {out.name} "
            f"{out.stat().st_size / 1e6:.1f} MB, max |onnx-native| "
            f"= {max_diff:.2e} on 2,000 test rows", log_fh)
        return {"ok": True, "file": out.name, "size_mb": out.stat().st_size / 1e6,
                "max_abs_diff_vs_native": max_diff, "winner": winner}
    except Exception as exc:  # noqa: BLE001 - export is nice-to-have
        log(f"winner ONNX export FAILED: {type(exc).__name__}: {exc}", log_fh)
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


# --------------------------------------------------------------------------
# reporting
# --------------------------------------------------------------------------
def _fmt(v) -> str:
    return f"{v:.4f}" if isinstance(v, (int, float)) else "n/a"


def write_report(run_dir: Path, report: dict, log_fh) -> Path:
    (run_dir / "promotion_report.json").write_text(
        json.dumps(report, indent=2))
    ship = report["shipped"]
    cand = report["candidate"]
    naive = report["naive"]
    lines = [
        f"# Redunator monthly retrain — {report['run_id']}",
        "",
        f"- Mode: {report['mode']}",
        f"- Started: {report['started']}",
        f"- Split (rolling, time-based): train <= {report.get('train_end', 'n/a')} "
        f"| test >= {report.get('test_start', 'n/a')} "
        f"({report.get('holdout_days', 'n/a')}-day holdout)",
        f"- Shipped model re-scored on the SAME test set: "
        f"RMSE {_fmt(ship.get('rmse'))} / "
        f"MAE {_fmt(ship.get('mae'))} "
        f"({ship.get('test_rows', 'n/a')} rows, "
        f"{ship.get('test_start', '')} -> "
        f"{ship.get('test_end', '')})",
    ]
    if isinstance(cand, dict):
        lines.append(f"- Candidate: RMSE {_fmt(cand.get('rmse'))} / "
                     f"MAE {_fmt(cand.get('mae'))}")
    else:
        lines.append("- Candidate: (see gate tests below — dry run exercises "
                     "several candidates)")
    lines += [
        f"- Naive baseline on the same test set: "
        f"RMSE {_fmt(naive.get('rmse'))} / MAE {_fmt(naive.get('mae'))}",
        "",
        "## Promotion rule",
        "PROMOTE iff candidate RMSE < shipped RMSE **and** candidate MAE < "
        "shipped MAE **and** candidate RMSE < naive RMSE (strict — a tie refuses).",
        "",
        "## Gate reasoning",
    ]
    lines += [f"- {r}" for r in report["gate_reasons"]]
    lines += [
        "",
        f"## Decision: **{report['decision']}**",
        "",
    ]
    if report["decision"] == "PROMOTE":
        lines += [
            f"- Promoted to: `{report['promoted_files']['joblib']}`, "
            f"`{report['promoted_files']['json']}`"
            + (f", `{report['promoted_files']['onnx']}`"
               if report['promoted_files'].get('onnx') else " (no ONNX)"),
            f"- live_model.json now points at version {report['promoted_version']}.",
            "- The shipped v1 files were NOT overwritten (md5s verified).",
        ]
    elif report["decision"] == "DO NOT PROMOTE":
        lines += ["- Live model unchanged. No files promoted."]
    elif str(report["decision"]).startswith("DRY RUN"):
        lines += ["- Dry run only: no downloads, no training, no files "
                  "promoted. Gate tests are listed in "
                  "promotion_report.json."]
    else:
        lines += [f"- Failure: {report.get('error', 'see run log')}"]
    lines += [
        "",
        f"Run directory: `{run_dir}`",
        f"Log: `{run_dir / 'run.log'}`",
    ]
    (run_dir / "promotion_report.md").write_text("\n".join(lines) + "\n")
    log(f"report written: {run_dir / 'promotion_report.md'}", log_fh)
    return run_dir / "promotion_report.md"


# --------------------------------------------------------------------------
# dry run
# --------------------------------------------------------------------------
def do_dry_run(run_dir: Path, args, log_fh) -> int:
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    log("DRY RUN: no downloads, no training, no files promoted.", log_fh)
    # 1. Real staging comparison: re-score the shipped model on the live
    #    feature table's test window. Must reproduce the recorded metrics.
    ship_live = shipped_metrics()
    rescored = score_model_on_table(SHIPPED_JOBLIB, LIVE_FEATURES,
                                    "2026-07-31", "2026-08-01")
    log(f"shipped model re-scored on live table: RMSE {rescored['rmse']:.4f} "
        f"/ MAE {rescored['mae']:.4f} (recorded: RMSE {ship_live['rmse']:.4f} "
        f"/ MAE {ship_live['mae']:.4f}; test "
        f"{rescored['test_start']} -> {rescored['test_end']}, "
        f"{rescored['test_rows']:,} rows)", log_fh)
    if rescored.get("cameras_skipped"):
        log(f"new cameras skipped (no train history): "
            f"{rescored['cameras_skipped']}", log_fh)
    recorded = json.loads(SHIPPED_METRICS.read_text())["metrics"]
    naive = {"rmse": float(recorded["naive_baseline"]["rmse"]),
             "mae": float(recorded["naive_baseline"]["mae"])}
    xgb = {"rmse": float(recorded["xgboost"]["rmse"]),
           "mae": float(recorded["xgboost"]["mae"])}

    # 2. Gate tests. Each entry: (label, candidate metrics, expected).
    tests = [
        ("naive baseline as candidate (deliberately worse)",
         naive, False),
        ("shipped metrics as candidate (exact tie)", ship_live, False),
        ("XGBoost alternative as candidate (worse on both)",
         xgb, False),
        ("synthetic better candidate (RMSE 3.20 / MAE 2.05)",
         {"rmse": 3.20, "mae": 2.05}, True),
        ("synthetic mixed candidate (RMSE better, MAE worse)",
         {"rmse": 3.20, "mae": 2.20}, False),
    ]
    if args.candidate_rmse is not None and args.candidate_mae is not None:
        tests.append(("ad-hoc candidate from --candidate-rmse/--candidate-mae",
                      {"rmse": args.candidate_rmse,
                       "mae": args.candidate_mae}, None))
    gate_results = []
    all_ok = True
    for label, cand, expected in tests:
        promote, reasons = decide_promotion(cand, rescored, naive)
        verdict = "PROMOTE" if promote else "DO NOT PROMOTE"
        check = ""
        if expected is not None:
            check = " (expected)" if promote == expected else " (UNEXPECTED!)"
            all_ok = all_ok and (promote == expected)
        log(f"GATE TEST [{label}]: candidate RMSE {cand['rmse']:.4f} / "
            f"MAE {cand['mae']:.4f} -> {verdict}{check}", log_fh)
        for r in reasons:
            log(f"    {r}", log_fh)
        gate_results.append({"test": label, "candidate": cand,
                             "decision": verdict,
                             "expected_promote": expected,
                             "reasons": reasons})
    report = {
        "run_id": run_dir.name,
        "mode": "dry-run",
        "started": started,
        "train_end": "2026-07-31",
        "test_start": "2026-08-01",
        "holdout_days": 90,
        "shipped": rescored,
        "shipped_recorded": ship_live,
        "rescore_reproduces_recorded": (
            abs(rescored["rmse"] - ship_live["rmse"]) < 1e-6
            and abs(rescored["mae"] - ship_live["mae"]) < 1e-6),
        "candidate": gate_results[-1]["candidate"]
        if args.candidate_rmse is not None else None,
        "naive": naive,
        "gate_reasons": [f"{g['test']}: {g['decision']}" for g in gate_results],
        "gate_tests": gate_results,
        "gate_tests_all_as_expected": all_ok,
        "decision": "DRY RUN — no promotion performed",
    }
    write_report(run_dir, report, log_fh)
    log(f"DRY RUN complete. Gate tests all as expected: {all_ok}", log_fh)
    return 0 if all_ok else 1


# --------------------------------------------------------------------------
# full run
# --------------------------------------------------------------------------
def do_full_run(run_dir: Path, args, log_fh) -> int:
    started = time.strftime("%Y-%m-%d %H:%M:%S")
    staging = run_dir / "staging"
    # Pre-run integrity snapshot of the shipped files.
    pre_md5 = {p.name: md5(p) for p in
               (SHIPPED_JOBLIB, SHIPPED_ONNX, SHIPPED_SIDECAR)}
    log(f"pre-run shipped md5s: {pre_md5}", log_fh)
    report_base = {"run_id": run_dir.name, "mode": "full",
                   "started": started, "holdout_days": args.holdout_days}
    try:
        # 1. Stage unpatched scripts, ingest fresh data into staging.
        stage_scripts(staging, None, None, log_fh)
        run_step("ingest (staging)", [str(VENV_PYTHON), "ingest.py"],
                 staging, log_fh, timeout=1800)
        raw_csv = staging / "data" / "raw" / "red_light_violations.csv"
        latest = latest_raw_date(raw_csv)
        train_end_d = latest - timedelta(days=args.holdout_days)
        test_start_d = train_end_d + timedelta(days=1)
        train_end, test_start = str(train_end_d), str(test_start_d)
        log(f"latest red-light date in fresh pull: {latest}; rolling split: "
            f"train <= {train_end}, test >= {test_start} "
            f"({args.holdout_days}-day holdout)", log_fh)

        # 2. Patch dates in the staging copies, then features + train.
        stage_scripts(staging, train_end, test_start, log_fh)
        run_step("features (staging)", [str(VENV_PYTHON), "features.py",
                                        "--check"], staging, log_fh,
                 timeout=1800)
        run_step("train (staging)", [str(VENV_PYTHON), "train.py"],
                 staging, log_fh, timeout=7200)

        # 3. Export step (staging): XGB alternative + XGB ONNX + RF12.
        if not args.skip_export:
            run_step("export_models (staging)",
                     [str(VENV_PYTHON), "export_models.py"], staging, log_fh,
                     timeout=7200)
        onnx_info = export_winner_onnx(staging, log_fh)

        # 4. Compare on the SAME test set: candidate metrics from the
        #    staging train run; shipped model re-scored on the staging
        #    table's test window; naive baseline from the staging run.
        staging_metrics = json.loads(
            (staging / "data" / "processed" / "model_metrics.json")
            .read_text())["metrics"]
        staging_sidecar = json.loads(
            (staging / "redunator_brain_v1.json").read_text())
        winner_name = staging_sidecar["model"]
        candidate = {"rmse": float(staging_metrics[winner_name]["rmse"]),
                     "mae": float(staging_metrics[winner_name]["mae"])}
        naive = {"rmse": float(staging_metrics["naive_baseline"]["rmse"]),
                 "mae": float(staging_metrics["naive_baseline"]["mae"])}
        shipped = score_model_on_table(
            SHIPPED_JOBLIB,
            staging / "data" / "processed" / "red_light_features.parquet",
            train_end, test_start)
        log(f"candidate ({winner_name}): RMSE {candidate['rmse']:.4f} / "
            f"MAE {candidate['mae']:.4f}", log_fh)
        log(f"shipped re-scored on same test set: RMSE {shipped['rmse']:.4f} "
            f"/ MAE {shipped['mae']:.4f} ({shipped['test_rows']:,} rows, "
            f"{shipped['test_start']} -> {shipped['test_end']})", log_fh)
        if shipped.get("cameras_skipped"):
            log(f"new cameras skipped (no train history): "
                f"{shipped['cameras_skipped']}", log_fh)
        promote, reasons = decide_promotion(candidate, shipped, naive)
        for r in reasons:
            log(f"GATE: {r}", log_fh)

        # 5. Promote (versioned files only) or leave live untouched.
        report = {**report_base, "train_end": train_end,
                  "test_start": test_start,
                  "shipped": shipped, "candidate": candidate, "naive": naive,
                  "gate_reasons": reasons, "winner": winner_name,
                  "onnx_export": onnx_info}
        if promote:
            version = next_version()
            assert version >= 2, "refusing to promote as v1 (would overwrite)"
            joblib_dst = BASE / f"redunator_brain_v{version}.joblib"
            json_dst = BASE / f"redunator_brain_v{version}.json"
            assert not joblib_dst.exists() and not json_dst.exists(), \
                "promotion target already exists — refusing to overwrite"
            shutil.copy2(staging / "redunator_brain_v1.joblib", joblib_dst)
            shutil.copy2(staging / "redunator_brain_v1.json", json_dst)
            promoted = {"joblib": joblib_dst.name, "json": json_dst.name}
            if onnx_info.get("ok"):
                onnx_dst = BASE / f"redunator_brain_v{version}.onnx"
                assert not onnx_dst.exists()
                shutil.copy2(staging / "candidate.onnx", onnx_dst)
                promoted["onnx"] = onnx_dst.name
            LIVE_POINTER.write_text(json.dumps({
                "live_version": version,
                "joblib": promoted["joblib"],
                "json": promoted["json"],
                "onnx": promoted.get("onnx"),
                "promoted_by_run": run_dir.name,
                "promoted_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                "metrics": candidate,
                "previous_live_version": 1,
                "note": "v1 files (redunator_brain_v1.*) are never "
                        "overwritten; they remain the fallback.",
            }, indent=2))
            report["decision"] = "PROMOTE"
            report["promoted_version"] = version
            report["promoted_files"] = promoted
            log(f"PROMOTED as v{version}: {promoted}", log_fh)
        else:
            report["decision"] = "DO NOT PROMOTE"
            log("DO NOT PROMOTE — live model unchanged.", log_fh)
        write_report(run_dir, report, log_fh)
    except Exception as exc:  # noqa: BLE001 - fail closed, report it
        log(f"RUN FAILED: {type(exc).__name__}: {exc}", log_fh)
        failed = {**report_base, "decision": "FAILED",
                  "error": f"{type(exc).__name__}: {exc}",
                  "shipped": {"rmse": None, "mae": None},
                  "candidate": {"rmse": None, "mae": None},
                  "naive": {"rmse": None, "mae": None},
                  "gate_reasons": ["run failed before the gate; "
                                   "no promotion (fail closed)"]}
        write_report(run_dir, failed, log_fh)
        return 1
    finally:
        post_md5 = {p.name: md5(p) for p in
                    (SHIPPED_JOBLIB, SHIPPED_ONNX, SHIPPED_SIDECAR)}
        unchanged = post_md5 == pre_md5
        log(f"post-run shipped md5s unchanged: {unchanged} {post_md5}", log_fh)
        if not unchanged:
            log("WARNING: shipped v1 files changed during this run!", log_fh)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="no downloads/training; re-score shipped model and "
                         "exercise the promotion gate")
    ap.add_argument("--candidate-rmse", type=float, default=None,
                    help="dry-run only: ad-hoc candidate RMSE for the gate")
    ap.add_argument("--candidate-mae", type=float, default=None,
                    help="dry-run only: ad-hoc candidate MAE for the gate")
    ap.add_argument("--holdout-days", type=int, default=HOLDOUT_DAYS_DEFAULT,
                    help="rolling holdout length in days (default 90)")
    ap.add_argument("--skip-export", action="store_true",
                    help="full run only: skip the staging export_models.py step")
    ap.add_argument("--run-dir", default=None,
                    help="override run directory name under retrain_runs/")
    args = ap.parse_args()

    if (args.candidate_rmse is None) != (args.candidate_mae is None):
        ap.error("--candidate-rmse and --candidate-mae must be given together")
    if (args.candidate_rmse is not None) and not args.dry_run:
        ap.error("ad-hoc candidate metrics are dry-run only")

    stamp = args.run_dir or time.strftime("%Y-%m-%d")
    run_dir = RUNS_DIR / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    with open(run_dir / "run.log", "a") as log_fh:
        log(f"retrain_monthly.py starting (mode="
            f"{'dry-run' if args.dry_run else 'full'}, run_dir={run_dir})",
            log_fh)
        if args.dry_run:
            rc = do_dry_run(run_dir, args, log_fh)
        else:
            rc = do_full_run(run_dir, args, log_fh)
        prune_old_runs(log_fh)
        return rc


if __name__ == "__main__":
    sys.exit(main())
