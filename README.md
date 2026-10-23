# 美股板塊資金流 / US Sector Capital Flow

Interactive Chaikin-style S&P 500 sector capital-flow chart (research tool).

**Live page:** https://vhhh2B618.github.io/us-sector-flow/

- Red = 淨流入 (inflow) · Green = 淨流出 (outflow)
- Play / scrubber over the latest US RTH session (5-min bars)
- Yahoo data is delayed; Chaikin is an estimate — **not** tick order-flow; **not** investment advice

## Auto-refresh (GitHub Actions)

Pages is meant to update **hourly on weekdays covering 09:00–16:00 ET** (EST/EDT) via GitHub Actions
(`cron: '0 13-21 * * 1-5'` UTC).

The runnable workflow source lives at [`pipeline/hourly-refresh.yml`](./pipeline/hourly-refresh.yml).
Copy it to `.github/workflows/hourly-refresh.yml` on `main` (GitHub UI → Add file, or a token with the `workflow` scope) to enable the schedule. The job runs `pipeline/publish_static.py` (yfinance fetch → rebuild root `index.html` + `sector_flow.json` / `.csv`) and commits only when data changed. Outside usable RTH / holidays / empty bars skip the commit (exit 78).

Optional short: [`sector_flow_short.mp4`](./sector_flow_short.mp4) (not rebuilt in CI).

## Local refresh

The in-page **Refresh** button needs the local pipeline server (`fetch_flow.py` + `server.py` on port **8766**). GitHub Pages serves static files only.
