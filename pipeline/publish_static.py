#!/usr/bin/env python3
"""Fetch US sector flow, rebuild root index.html, copy json/csv to Pages root.

Exit 0 on success.
Exit 78 on skip (holiday / empty / no usable RTH bars) so Actions succeeds without commit.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PAGES = ROOT.parent
SKIP = 78
SKIP_MARKERS = (
    "No RTH bars",
    "No bars for session",
    "No OHLCV data downloaded",
)


def main() -> int:
    sys.path.insert(0, str(ROOT))
    try:
        from fetch_flow import compute_flows, load_sector_map

        compute_flows(load_sector_map(), force=True)
    except Exception as e:
        msg = str(e)
        if any(m in msg for m in SKIP_MARKERS):
            print(f"SKIP: {e}", flush=True)
            return SKIP
        print(f"ERROR fetch: {e}", flush=True)
        return 1

    out_json = ROOT / "out" / "sector_flow.json"
    out_csv = ROOT / "out" / "sector_flow.csv"
    if not out_json.exists():
        print("SKIP: no sector_flow.json after fetch", flush=True)
        return SKIP

    payload = json.loads(out_json.read_text(encoding="utf-8"))
    n_ts = int(payload.get("n_timestamps") or 0)
    if n_ts < 1:
        print("SKIP: empty session (n_timestamps=0)", flush=True)
        return SKIP

    r2 = subprocess.run(
        [sys.executable, str(ROOT / "rebuild_index.py")],
        cwd=str(ROOT),
        check=False,
    )
    if r2.returncode != 0:
        print(f"ERROR: rebuild_index exited {r2.returncode}", flush=True)
        return 1

    shutil.copy2(out_json, PAGES / "sector_flow.json")
    if out_csv.exists():
        shutil.copy2(out_csv, PAGES / "sector_flow.csv")

    print(
        f"OK published session={payload.get('session_date')} "
        f"as_of={payload.get('as_of_et')} bars={n_ts} → {PAGES}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
