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

`candles` is 96 bars of 30-minute OHLC (roughly the last 48 hours), oldest
first, `time` as UTC unix seconds, straight from Twelve Data's
`time_series` endpoint, ready to feed a Lightweight Charts candlestick
series with no reshaping.

## API usage

Each run makes 2 calls (`/quote` + `/time_series`), every 5 minutes,
so ~576 calls/day against Twelve Data's free 800/day limit. Leaves
headroom, don't add more calls per run without checking the budget.

## Note

GitHub disables scheduled workflows automatically after 60 days with no
other repo activity. If the price looks stale, check **Actions** tab
first, it likely just needs a manual re-run to wake back up.
