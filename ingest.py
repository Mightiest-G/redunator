#!/usr/bin/env python3
"""Redunator Phase 2 — Step 1: ingest Chicago camera violation datasets.

Downloads the two public Chicago Data Portal datasets via the bulk CSV
export endpoint (no API key needed):

  - spqx-js37  Red Light Camera Violations
  - hhkd-xvj4  Speed Camera Violations

Both are DAILY granularity (one row per camera per day) — no hour-of-day
data exists in these sources. Do not invent hourly data downstream.

Raw output:
  data/raw/red_light_violations.csv
  data/raw/speed_violations.csv
  data/raw/INGEST_SUMMARY.md   (written by validate.py, or by hand after run)

Usage:
  python ingest.py            # downloads both datasets (retries once each)
"""
import csv
import hashlib
import os
import sys
import time

import requests

BASE = "https://data.cityofchicago.org/api/views/{}/rows.csv?accessType=DOWNLOAD"

DATASETS = {
    "red_light": {
        "id": "spqx-js37",
        "name": "Red Light Camera Violations",
        "path": os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data", "raw", "red_light_violations.csv",
        ),
    },
    "speed": {
        "id": "hhkd-xvj4",
        "name": "Speed Camera Violations",
        "path": os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            "data", "raw", "speed_violations.csv",
        ),
    },
}

CHUNK = 1024 * 1024  # 1 MB streaming chunks


def download_one(key, spec, max_attempts=2):
    url = BASE.format(spec["id"])
    os.makedirs(os.path.dirname(spec["path"]), exist_ok=True)
    last_exc = None
    for attempt in range(1, max_attempts + 1):
        try:
            print(f"[{key}] attempt {attempt}: GET {url}", flush=True)
            with requests.get(url, stream=True, timeout=120) as r:
                r.raise_for_status()
                ctype = r.headers.get("Content-Type", "")
                print(f"[{key}] HTTP {r.status_code} Content-Type: {ctype}", flush=True)
                sha = hashlib.sha256()
                nbytes = 0
                with open(spec["path"], "wb") as f:
                    for chunk in r.iter_content(chunk_size=CHUNK):
                        if chunk:
                            sha.update(chunk)
                            f.write(chunk)
                            nbytes += len(chunk)
                print(f"[{key}] done: {nbytes:,} bytes sha256={sha.hexdigest()[:16]}...",
                      flush=True)
            # sanity: file must look like CSV, not an HTML error page
            with open(spec["path"], "rb") as f:
                head = f.read(512)
            if head.lstrip().lower().startswith(b"<!doctype") or head.lstrip().lower().startswith(b"<html"):
                raise ValueError("downloaded file looks like an HTML error page, not CSV")
            return nbytes
        except Exception as exc:  # noqa: BLE001 - retry once, then surface
            last_exc = exc
            print(f"[{key}] attempt {attempt} FAILED: {exc}", flush=True)
            if attempt < max_attempts:
                time.sleep(10)
    raise RuntimeError(f"[{key}] download failed after {max_attempts} attempts: {last_exc}")


def main():
    for key, spec in DATASETS.items():
        download_one(key, spec)
    print("All downloads complete.", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
