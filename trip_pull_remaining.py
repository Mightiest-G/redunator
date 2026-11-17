#!/usr/bin/env python3
"""Phase 2c — driver for the last TNP gap (m6dm-c72p 2020-02..2020-06).

Pulls each remaining week-chunk as its own fresh process
(trip_pull_one.py), 30s apart, so no long-lived worker accumulates
the egress-proxy connection failures seen before. Failed chunks are
logged and retried on the next run; the script is idempotent (it
skips chunks whose parquet already exists).
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from trip_demand_ingest import CHUNK_DIR, chunks_for_month, months  # noqa: E402

PY = os.path.expanduser("~/workspace/redunator-venv/bin/python")
HERE = os.path.dirname(os.path.abspath(__file__))

CHUNKS = []
for ym in months("2020-02", "2020-06"):
    for label, start, end in chunks_for_month(ym, False, True):
        CHUNKS.append((label, start, end))

failed = []
done = 0
for label, start, end in CHUNKS:
    path = os.path.join(CHUNK_DIR, f"tnp_m6dm-c72p_{label}.parquet")
    if os.path.exists(path):
        done += 1
        continue
    print(f"=== {label} ({start}..{end})", flush=True)
    try:
        r = subprocess.run(
            [PY, os.path.join(HERE, "trip_pull_one.py"),
             "tnp", "m6dm-c72p", label, start, end, "tnp-old1a-w"],
            timeout=1800, capture_output=True, text=True)
        print(r.stdout, end="", flush=True)
        if r.stderr:
            print(r.stderr[-2000:], end="", flush=True)
        if r.returncode == 0:
            done += 1
        else:
            print(f"!!! {label} FAILED rc={r.returncode}", flush=True)
            failed.append(label)
    except subprocess.TimeoutExpired:
        print(f"!!! {label} TIMEOUT after 1800s", flush=True)
        failed.append(label)
    time.sleep(30)

print(f"DONE: {done}/{len(CHUNKS)} chunks present; failed: {failed}",
      flush=True)
