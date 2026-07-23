"""
Fetches the current XAU/USD (gold spot) quote from Twelve Data and writes
spot.json in the shape toro_session_desk.html's poll loop expects:

  { spot, changePct, marketOpen, updated, source, contract }

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
URL = f"https://api.twelvedata.com/quote?symbol={SYMBOL}&apikey={API_KEY}"

try:
    with urllib.request.urlopen(URL, timeout=15) as r:
        data = json.load(r)
except Exception as e:
    print(f"Fetch failed: {e}", file=sys.stderr)
    sys.exit(1)

if "close" not in data:
    print(f"Unexpected response: {data}", file=sys.stderr)
    sys.exit(1)

out = {
    "spot": round(float(data["close"]), 2),
    "changePct": round(float(data.get("percent_change", 0) or 0), 2),
    "marketOpen": bool(data.get("is_market_open", True)),
    "updated": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "source": "XAU/USD",
    "contract": "Gold spot",
}

with open("spot.json", "w") as f:
    json.dump(out, f, indent=2)
    f.write("\n")

print("wrote spot.json:", out)
