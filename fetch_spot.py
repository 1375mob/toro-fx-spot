"""
Fetches the current XAU/USD (gold spot) quote AND recent 5-minute candles
from Twelve Data, writes spot.json in the shape toro_session_desk.html's
poll loop expects:

  { spot, changePct, marketOpen, updated, source, contract, candles }

candles is an array of {time, open, high, low, close}, oldest first, time
as UTC unix seconds, ready for Lightweight Charts' candlestick series.

Runs on a GitHub Actions schedule (see .github/workflows/update-spot.yml).
Requires a TWELVEDATA_KEY environment variable (set as a repo secret).
"""
import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ.get("TWELVEDATA_KEY")
if not API_KEY:
    print("TWELVEDATA_KEY is not set", file=sys.stderr)
    sys.exit(1)

SYMBOL = "XAU/USD"
QUOTE_URL = f"https://api.twelvedata.com/quote?symbol={SYMBOL}&apikey={API_KEY}"
SERIES_URL = (
    f"https://api.twelvedata.com/time_series?symbol={SYMBOL}"
    f"&interval=5min&outputsize=96&timezone=UTC&apikey={API_KEY}"
)


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


try:
    quote = fetch_json(QUOTE_URL)
    series = fetch_json(SERIES_URL)
except Exception as e:
    print(f"Fetch failed: {e}", file=sys.stderr)
    sys.exit(1)

if "close" not in quote:
    print(f"Unexpected quote response: {quote}", file=sys.stderr)
    sys.exit(1)

if series.get("status") != "ok" or "values" not in series:
    print(f"Unexpected series response: {series}", file=sys.stderr)
    sys.exit(1)

candles = []
for v in reversed(series["values"]):  # Twelve Data returns newest first, we want oldest first
    dt = datetime.strptime(v["datetime"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
    candles.append({
        "time": int(dt.timestamp()),
        "open": round(float(v["open"]), 2),
        "high": round(float(v["high"]), 2),
        "low": round(float(v["low"]), 2),
        "close": round(float(v["close"]), 2),
    })

out = {
    "spot": round(float(quote["close"]), 2),
    "changePct": round(float(quote.get("percent_change", 0) or 0), 2),
    "marketOpen": bool(quote.get("is_market_open", True)),
    "updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "source": "XAU/USD",
    "contract": "Gold spot",
    "candles": candles,
}

with open("spot.json", "w") as f:
    json.dump(out, f, indent=2)
    f.write("\n")

print(f"wrote spot.json: spot={out['spot']} candles={len(candles)}")
