# toro-fx-spot

Public relay for the live gold spot price shown on the ToroFX Session Desk
(`toro_session_desk.html`) and its live chart. A GitHub Actions workflow
pulls XAU/USD from Twelve Data every 5 minutes and commits `spot.json`
here. The site polls this file's raw URL directly, no API key ever
touches the browser.

## Setup

1. Get a free API key at https://twelvedata.com (no card required).
2. In this repo: **Settings -> Secrets and variables -> Actions -> New
   repository secret**, name it `TWELVEDATA_KEY`, paste the key.
3. Run the workflow once manually (**Actions -> Update gold spot price ->
   Run workflow**) to confirm it works, then it runs on its own every 5
   minutes.

## spot.json shape

```json
{
  "spot": 4124.61,
  "changePct": -0.14,
  "marketOpen": true,
  "updated": "2026-07-23T02:53:45Z",
  "source": "XAU/USD",
  "contract": "Gold spot",
  "candles": [
    { "time": 1753239225, "open": 4124.61, "high": 4124.61, "low": 4124.61, "close": 4124.61 }
  ]
}
```

`candles` is 336 bars of 30-minute OHLC (roughly the last week), oldest
first, `time` as UTC unix seconds, straight from Twelve Data's
`time_series` endpoint, ready to feed a Lightweight Charts candlestick
series with no reshaping.

## API usage

Each run makes 2 calls (`/quote` + `/time_series`), every 5 minutes,
so ~576 calls/day against Twelve Data's free 800/day limit. Leaves
headroom, don't add more calls per run without checking the budget.

## macro.json — the Macro Compass read

`fetch_macro.py` builds `macro.json` every 15 minutes on its own workflow,
a port of the ToroFX Macro Compass v3.1 TradingView indicator. The
decision logic lives in `macro_logic.py` as a 1:1 port of the Pine `f_*`
functions; run `python3 macro_logic.py` for its self-test, which the
workflow runs as a gate before anything is allowed to publish.

Needs a second secret, `FRED_API_KEY`, free from
https://fredaccount.stlouisfed.org/apikeys. Use the **API 1** key: v2 is a
bulk release-level endpoint and authenticates by Bearer header, neither of
which suits reading four series. Without the key the compass still builds,
but real yield, breakeven and the R13 attribution go `N/A`.

Costs nothing against the Twelve Data budget above. Neither source is
Twelve Data, whose free tier 404s on every direct macro instrument
(US10Y, DXY, WTI) and is why `spot.json`'s drivers are ETF proxies with no
levels.

Two things that will bite anyone editing this:

- **Yahoo's `meta.chartPreviousClose` is relative to the requested range,
  not the prior session.** On the 10Y at `range=5d` it reads 4.598 against
  a true prior close of 4.703, turning a -2.4bp session into +8.1bp. A
  sign flip, enough to invert the headline bias. Deltas are taken from the
  daily close series instead. Do not "simplify" this back.

- **FRED publishes DFII10 and T10YIE on different lags.** Seen live:
  real yield last 2026-07-23 while breakeven already had 07-24. R13
  attribution compares the two deltas bps-for-bps, so each series' own
  last value would compare a Thursday move against a Friday one and decide
  REAL-LED vs BE-LED off mismatched windows. `aligned_deltas` pins both to
  their latest common date. `decompositionDriftBps` in the output is the
  check: real + breakeven must reconstruct the cash 10Y move, so anything
  but 0.0 means the alignment broke.

Also note `fred.stlouisfed.org` (the website, including `fredgraph.csv`)
is unreachable from Actions runners: it redirects and the target
blackholes datacenter IPs, giving identical timeouts rather than a
refusal. `api.stlouisfed.org` answers in ~0.2s. The website endpoints
remain only as a keyless fallback and will time out in CI.

## Note

GitHub disables scheduled workflows automatically after 60 days with no
other repo activity. If the price looks stale, check **Actions** tab
first, it likely just needs a manual re-run to wake back up.
