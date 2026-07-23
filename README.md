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
  "spot": 4136.20,
  "changePct": 1.46,
  "marketOpen": true,
  "updated": "2026-07-22T21:00:00Z",
  "source": "XAU/USD",
  "contract": "Gold spot"
}
```

## Note

GitHub disables scheduled workflows automatically after 60 days with no
other repo activity. If the price looks stale, check **Actions** tab
first, it likely just needs a manual re-run to wake back up.
