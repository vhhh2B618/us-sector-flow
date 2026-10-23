#!/usr/bin/env python3
"""Download S&P 500 5m bars via yfinance, compute Chaikin-style sector money flow."""
from __future__ import annotations

import json
import time
import warnings
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import yfinance as yf

warnings.filterwarnings("ignore", category=FutureWarning)

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "raw"
OUT = ROOT / "out"
SECTORS_PATH = ROOT / "sp500_sectors.json"
ET = ZoneInfo("America/New_York")
HKT = ZoneInfo("Asia/Hong_Kong")
RTH_START = dtime(9, 30)
RTH_END = dtime(16, 0)
BATCH = 40  # tickers per yfinance batch
SECTOR_ORDER = [
    "Information Technology",
    "Financials",
    "Health Care",
    "Consumer Discretionary",
    "Consumer Staples",
    "Industrials",
    "Energy",
    "Utilities",
    "Real Estate",
    "Materials",
    "Communication Services",
]


def now_hkt() -> str:
    return datetime.now(HKT).strftime("%Y-%m-%d %H:%M:%S HKT")


def load_sector_map() -> dict:
    return json.loads(SECTORS_PATH.read_text(encoding="utf-8"))


def chaikin_flow(h, l, c, v) -> float:
    """((C-L)-(H-C))/(H-L) * V * avg_price; 0 if H==L."""
    rng = h - l
    if rng == 0 or np.isnan(rng) or np.isnan(h) or np.isnan(l) or np.isnan(c) or np.isnan(v):
        return 0.0
    mult = ((c - l) - (h - c)) / rng
    avg = (h + l + c) / 3.0
    return float(mult * v * avg)


def pick_last_rth_session(index: pd.DatetimeIndex) -> tuple[pd.Timestamp, pd.DatetimeIndex]:
    """Return session date (ET date) and boolean mask for last complete/in-progress RTH."""
    if index.tz is None:
        idx = index.tz_localize("UTC").tz_convert(ET)
    else:
        idx = index.tz_convert(ET)
    # Only regular-hours bars
    rth = idx[(idx.time >= RTH_START) & (idx.time <= RTH_END)]
    if len(rth) == 0:
        raise RuntimeError("No RTH bars in downloaded data")
    # Prefer last calendar day that has bars covering at least to 10:00 (session started),
    # or full session if market closed.
    days = sorted(set(rth.normalize()))
    # Use the most recent day with any RTH bars
    session_day = days[-1]
    mask = rth.normalize() == session_day
    session_idx = rth[mask]
    return session_day, session_idx


def download_batch(tickers: list[str], period: str = "5d", interval: str = "5m") -> pd.DataFrame:
    """Return multi-index columns DataFrame (OHLCV x ticker) or empty."""
    if not tickers:
        return pd.DataFrame()
    try:
        df = yf.download(
            tickers=" ".join(tickers),
            period=period,
            interval=interval,
            group_by="ticker",
            auto_adjust=False,
            threads=False,
            progress=False,
            timeout=60,
        )
    except Exception as e:
        print(f"  batch error: {e}")
        return pd.DataFrame()
    return df if df is not None else pd.DataFrame()


def extract_ohlcv(df: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if df is None or df.empty:
        return None
    try:
        if isinstance(df.columns, pd.MultiIndex):
            # yfinance may use (ticker, OHLCV) or (OHLCV, ticker)
            level0 = df.columns.get_level_values(0)
            if ticker in level0:
                sub = df[ticker].copy()
            elif ticker in df.columns.get_level_values(-1):
                sub = df.xs(ticker, axis=1, level=-1).copy()
            else:
                return None
        else:
            # single ticker download
            sub = df.copy()
        need = ["Open", "High", "Low", "Close", "Volume"]
        if not all(c in sub.columns for c in need):
            return None
        sub = sub[need].dropna(how="all")
        if sub.empty:
            return None
        return sub
    except Exception:
        return None


def compute_flows(sector_map: dict, force: bool = False) -> dict:
    RAW.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)

    tickers_meta = sector_map["tickers"]
    symbols = sorted(tickers_meta.keys())
    # yfinance: BRK-B style already
    cache_meta_path = RAW / "cache_meta.json"
    raw_parquet = RAW / "ohlcv_5m.parquet"

    # Try reuse cache if same ET session date still valid
    reuse = False
    if not force and raw_parquet.exists() and cache_meta_path.exists():
        try:
            meta = json.loads(cache_meta_path.read_text(encoding="utf-8"))
            # Reuse if cached within last 6h and same calendar day ET for closed market,
            # or always offer reuse for same session_date if market still that day.
            cached_session = meta.get("session_date")
            now_et = datetime.now(ET)
            today_et = now_et.date().isoformat()
            # If market closed (after 16:00 ET or weekend) and session matches last session, reuse
            if cached_session:
                age_h = (time.time() - meta.get("fetched_unix", 0)) / 3600
                if age_h < 12 and (cached_session == today_et or age_h < 6):
                    reuse = True
                    print(f"Reusing cached OHLCV (session={cached_session}, age={age_h:.1f}h)")
        except Exception:
            reuse = False

    if reuse:
        wide = pd.read_parquet(raw_parquet)
    else:
        print(f"Downloading {len(symbols)} tickers in batches of {BATCH}…")
        frames = []
        failed = []
        for i in range(0, len(symbols), BATCH):
            batch = symbols[i : i + BATCH]
            print(f"  batch {i // BATCH + 1}/{(len(symbols) + BATCH - 1) // BATCH}: {batch[0]}…{batch[-1]}")
            df = download_batch(batch)
            if df is None or df.empty:
                failed.extend(batch)
                time.sleep(1)
                continue
            for t in batch:
                sub = extract_ohlcv(df if len(batch) > 1 else (df if not isinstance(df.columns, pd.MultiIndex) else df), t)
                # When single-ticker in multi-ticker call, extract; when batch of 1, columns flat
                if sub is None and len(batch) == 1 and not isinstance(df.columns, pd.MultiIndex):
                    sub = extract_ohlcv(df, t)
                if sub is None:
                    # retry single
                    try:
                        one = yf.download(t, period="5d", interval="5m", auto_adjust=False, progress=False, timeout=45, threads=False)
                        sub = extract_ohlcv(one, t)
                    except Exception:
                        sub = None
                if sub is None or sub.empty:
                    failed.append(t)
                    continue
                sub = sub.copy()
                sub["Ticker"] = t
                frames.append(sub.reset_index().rename(columns={"Datetime": "Datetime", "index": "Datetime"}))
            time.sleep(0.35)

        if not frames:
            raise RuntimeError("No OHLCV data downloaded")

        long_df = pd.concat(frames, ignore_index=True)
        # Normalize datetime column name
        dtcol = None
        for c in long_df.columns:
            if str(c).lower() in ("datetime", "date", "index"):
                dtcol = c
                break
        if dtcol is None:
            # first column often Datetime from reset_index
            dtcol = long_df.columns[0]
        long_df = long_df.rename(columns={dtcol: "Datetime"})
        long_df["Datetime"] = pd.to_datetime(long_df["Datetime"], utc=True)
        wide = long_df
        wide.to_parquet(raw_parquet, index=False)
        print(f"Saved raw parquet: {raw_parquet} rows={len(wide)} tickers_ok={wide['Ticker'].nunique()} failed={len(failed)}")

    # Ensure Datetime tz-aware
    wide["Datetime"] = pd.to_datetime(wide["Datetime"], utc=True)
    idx_all = pd.DatetimeIndex(wide["Datetime"].unique()).tz_convert(ET)
    session_day, session_idx = pick_last_rth_session(idx_all)
    session_date = session_day.date().isoformat() if hasattr(session_day, "date") else str(session_day)[:10]
    # session_idx are ET timestamps — convert to UTC for filter
    session_utc = set(session_idx.tz_convert("UTC"))
    # Also match by floor to minute
    wide_et = wide.copy()
    wide_et["et"] = wide_et["Datetime"].dt.tz_convert(ET)
    day_mask = wide_et["et"].dt.normalize() == session_day.normalize() if hasattr(session_day, "normalize") else (
        wide_et["et"].dt.date == session_day.date()
    )
    # Fix: session_day from pick may be Timestamp normalized
    if isinstance(session_day, pd.Timestamp):
        sd = session_day.tz_convert(ET).normalize()
    else:
        sd = pd.Timestamp(session_day, tz=ET).normalize()
    day_mask = wide_et["et"].dt.normalize() == sd
    rth_mask = (wide_et["et"].dt.time >= RTH_START) & (wide_et["et"].dt.time <= RTH_END)
    sess = wide_et[day_mask & rth_mask].copy()
    if sess.empty:
        raise RuntimeError(f"No bars for session {session_date}")

    # Cache meta
    cache_meta_path.write_text(
        json.dumps(
            {
                "session_date": session_date,
                "fetched_unix": time.time(),
                "fetched_at_hkt": now_hkt(),
                "n_rows": len(wide),
                "n_tickers": int(wide["Ticker"].nunique()),
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    # Per-bar flow
    flows = []
    other_count = 0
    mapped = 0
    for ticker, g in sess.groupby("Ticker"):
        meta = tickers_meta.get(ticker) or tickers_meta.get(ticker.replace("-", "."))
        if meta is None:
            sector = "Other"
            sector_zh = "其他"
            other_count += 1
        else:
            sector = meta["sector"]
            sector_zh = meta["sector_zh"]
            mapped += 1
        for _, row in g.iterrows():
            f = chaikin_flow(row["High"], row["Low"], row["Close"], row["Volume"])
            flows.append(
                {
                    "Datetime": row["et"],
                    "Ticker": ticker,
                    "sector": sector,
                    "sector_zh": sector_zh,
                    "flow": f,
                }
            )

    flow_df = pd.DataFrame(flows)
    # Aggregate by timestamp × sector
    agg = (
        flow_df.groupby(["Datetime", "sector", "sector_zh"], as_index=False)["flow"]
        .sum()
        .sort_values(["Datetime", "sector"])
    )
    # Cumulative from session open
    agg["cum_flow"] = agg.groupby("sector")["flow"].cumsum()
    # USD millions
    agg["flow_m"] = agg["flow"] / 1e6
    agg["cum_flow_m"] = agg["cum_flow"] / 1e6

    # Pivot for CSV wide
    timestamps = sorted(agg["Datetime"].unique())
    sectors_present = [s for s in SECTOR_ORDER if s in set(agg["sector"])] + sorted(
        set(agg["sector"]) - set(SECTOR_ORDER)
    )
    zh_map = sector_map.get("sectors_zh", {})
    for s in sectors_present:
        if s not in zh_map and s != "Other":
            # from agg
            z = agg.loc[agg["sector"] == s, "sector_zh"]
            if len(z):
                zh_map[s] = z.iloc[0]
    zh_map.setdefault("Other", "其他")

    rows_csv = []
    series = []  # list of {ts, sectors: {name: cum_m}}
    for ts in timestamps:
        snap = agg[agg["Datetime"] == ts]
        by_sec = {r["sector"]: float(r["cum_flow_m"]) for _, r in snap.iterrows()}
        # also bar flow
        by_sec_bar = {r["sector"]: float(r["flow_m"]) for _, r in snap.iterrows()}
        et_str = pd.Timestamp(ts).tz_convert(ET).strftime("%Y-%m-%d %H:%M")
        clock = pd.Timestamp(ts).tz_convert(ET).strftime("%H:%M")
        row = {"timestamp_et": et_str, "clock": clock}
        sec_out = {}
        for s in sectors_present:
            v = by_sec.get(s, 0.0)
            row[s] = round(v, 3)
            sec_out[s] = {
                "zh": zh_map.get(s, s),
                "cum_m": round(v, 3),
                "bar_m": round(by_sec_bar.get(s, 0.0), 3),
            }
        rows_csv.append(row)
        series.append({"timestamp_et": et_str, "clock": clock, "sectors": sec_out})

    csv_df = pd.DataFrame(rows_csv)
    csv_path = OUT / "sector_flow.csv"
    csv_df.to_csv(csv_path, index=False)

    # Latest snapshot ranking
    last = series[-1] if series else {"sectors": {}}
    ranked = sorted(
        [
            {
                "sector": s,
                "zh": d["zh"],
                "cum_m": d["cum_m"],
            }
            for s, d in last.get("sectors", {}).items()
        ],
        key=lambda x: -abs(x["cum_m"]),
    )
    top_in = [x for x in sorted(ranked, key=lambda x: -x["cum_m"]) if x["cum_m"] > 0][:3]
    top_out = [x for x in sorted(ranked, key=lambda x: x["cum_m"]) if x["cum_m"] < 0][:3]

    session_end = pd.Timestamp(timestamps[-1]).tz_convert(ET) if timestamps else None
    session_start = pd.Timestamp(timestamps[0]).tz_convert(ET) if timestamps else None
    weekday_zh = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
    wd = weekday_zh[session_start.weekday()] if session_start is not None else ""

    payload = {
        "ok": True,
        "generated_at_hkt": now_hkt(),
        "session_date": session_date,
        "session_label": f"{session_date} ({wd})" if wd else session_date,
        "session_hours": "09:30–16:00 ET",
        "as_of_et": session_end.strftime("%Y-%m-%d %H:%M ET") if session_end is not None else None,
        "as_of_clock": session_end.strftime("%H:%M") if session_end is not None else None,
        "n_timestamps": len(timestamps),
        "n_tickers_mapped": mapped,
        "n_tickers_other": other_count,
        "n_tickers_downloaded": int(sess["Ticker"].nunique()),
        "sectors": [{"en": s, "zh": zh_map.get(s, s)} for s in sectors_present],
        "unit": "百萬美元 (USD millions, cumulative Chaikin-style)",
        "formula": "((C-L)-(H-C))/(H-L) * Volume * (H+L+C)/3 ; 0 if H==L",
        "data_source": "Yahoo Finance via yfinance (delayed)",
        "disclaimer_zh": "Yahoo 免費數據有延遲，非即時。Chaikin 公式為估算，非交易所逐筆資金流。僅供研究參考，不構成投資建議。",
        "disclaimer_en": "Yahoo free data is delayed. Chaikin money flow is an estimate, not exchange tick order-flow. Research only — not financial advice.",
        "top_inflow": top_in,
        "top_outflow": top_out,
        "series": series,
        "latest": last,
    }

    json_path = OUT / "sector_flow.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # Slim summary for API (like comparison.json)
    summary = {
        "ok": True,
        "generated_at_hkt": payload["generated_at_hkt"],
        "session_date": session_date,
        "session_label": payload["session_label"],
        "as_of_et": payload["as_of_et"],
        "as_of_clock": payload["as_of_clock"],
        "n_timestamps": payload["n_timestamps"],
        "n_tickers_downloaded": payload["n_tickers_downloaded"],
        "n_tickers_other": other_count,
        "top_inflow": top_in,
        "top_outflow": top_out,
        "unit": "USD millions (cumulative)",
    }
    (OUT / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")

    print(
        f"OK session={session_date} bars={len(timestamps)} tickers={payload['n_tickers_downloaded']} "
        f"other={other_count} → {json_path}"
    )
    if top_in:
        print(f"  top inflow: {top_in[0]['zh']} {top_in[0]['cum_m']:+.1f}M")
    if top_out:
        print(f"  top outflow: {top_out[0]['zh']} {top_out[0]['cum_m']:+.1f}M")
    return payload


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="Ignore raw cache and re-download")
    args = ap.parse_args()
    sector_map = load_sector_map()
    compute_flows(sector_map, force=args.force)


if __name__ == "__main__":
    main()
