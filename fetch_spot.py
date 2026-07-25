"""
Fetches the current XAU/USD (gold spot) quote, recent 30-minute candles
(1 week lookback), and the cross-asset drivers behind the rate-channel
regime read, from Twelve Data. Writes spot.json in the shape
toro_session_desk.html's poll loop expects:

  { spot, changePct, marketOpen, updated, source, contract, candles,
    drivers, driversUpdated }

candles is an array of {time, open, high, low, close}, oldest first, time
as UTC unix seconds, ready for Lightweight Charts' candlestick series.

drivers is an array of
{key, label, symbol, value, changePct, dir, proxy, showValue}.
`proxy` is non-empty when the slot is a stand-in instrument rather than the
thing named on the card, and `showValue` is false in that case: an ETF's
own price is not the yield or the barrel price, so only its direction and
percent change are safe to publish.

API budget (free tier: 800 credits/day, 8 requests/minute)
---------------------------------------------------------
Gold costs 2 credits per run at every 5 minutes, which is 576/day and
already most of the allowance. So drivers are only refetched on the
half hour (~48 times/day, 3 credits each = 144), and carried forward
from the previous spot.json on every other run. Total lands near 720.
Do not fetch drivers every run, it does not fit.

Probing for entitlements
------------------------
Run the workflow manually with probe_drivers=true to walk the full
candidate list for each slot and print why each one lost. That costs
~11 credits, so it is manual-only, never on the schedule. Once you know
what this key serves, move the winners to the front of each candidate
list and the steady-state run picks them up first.

Requires a TWELVEDATA_KEY environment variable (set as a repo secret).
"""
import json
import os
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone

API_KEY = os.environ.get("TWELVEDATA_KEY")
if not API_KEY:
    print("TWELVEDATA_KEY is not set", file=sys.stderr)
    sys.exit(1)

PROBE = os.environ.get("GF_PROBE_DRIVERS") == "1"

SYMBOL = "XAU/USD"
QUOTE_URL = f"https://api.twelvedata.com/quote?symbol={SYMBOL}&apikey={API_KEY}"
SERIES_URL = (
    f"https://api.twelvedata.com/time_series?symbol={SYMBOL}"
    f"&interval=30min&outputsize=336&timezone=UTC&apikey={API_KEY}"
)

# Free tier allows 8 requests/minute, so space the driver calls out.
THROTTLE_SECONDS = 9

# Each slot is one column of the driver card. Candidates run best-first and
# the first one that answers wins. `invert` flips the change sign so the slot
# always reads in terms of its own name (bond prices move opposite to yields).
#
# A 2026-07-25 probe of this key found the direct instruments all 404:
# US10Y, TNX, DXY, WTI/USD, USOIL, BRENT/USD. USDX answered but priced at
# 25.5, nowhere near a dollar index, so it resolves to some other
# instrument and is not trustworthy. That leaves ETF proxies only, which is
# why every slot below carries a `proxy` note. If the Twelve Data plan is
# ever upgraded, put the direct symbols back at the front of these lists:
# the first-wins order means values start showing up on their own.
#
# `proxy` being set is the signal that the absolute price is meaningless
# (USO's level is not the oil price, IEF's is not a yield). Only the change
# and direction are safe to publish for those.
DRIVER_SLOTS = [
    {
        "key": "rates",
        "candidates": [
            {"sym": "IEF", "label": "Rates", "invert": True, "proxy": "IEF inv"},
            {"sym": "TLT", "label": "Rates", "invert": True, "proxy": "TLT inv"},
        ],
    },
    {
        "key": "dollar",
        "candidates": [
            {"sym": "UUP", "label": "Dollar", "proxy": "UUP"},
            {"sym": "EUR/USD", "label": "Dollar", "invert": True, "proxy": "EURUSD inv"},
        ],
    },
    {
        "key": "oil",
        "candidates": [
            {"sym": "USO", "label": "Oil", "proxy": "USO"},
        ],
    },
]


def fetch_json(url):
    with urllib.request.urlopen(url, timeout=15) as r:
        return json.load(r)


def fetch_quote(symbol):
    sym = urllib.parse.quote(symbol, safe="")
    return fetch_json(f"https://api.twelvedata.com/quote?symbol={sym}&apikey={API_KEY}")


def read_previous():
    try:
        with open("spot.json") as f:
            return json.load(f)
    except Exception:
        return {}


def as_driver(slot, cand, q):
    chg = float(q.get("percent_change", 0) or 0)
    if cand.get("invert"):
        chg = -chg
    proxy = cand.get("proxy", "")
    return {
        "key": slot["key"],
        "label": cand["label"],
        "symbol": cand["sym"],
        "value": round(float(q["close"]), 2),
        "changePct": round(chg, 2),
        # flat band stops rounding noise from reading as a direction
        "dir": "up" if chg > 0.02 else ("down" if chg < -0.02 else "flat"),
        "proxy": proxy,
        # a proxy's own price says nothing about the thing named on the card,
        # so the page must not print it as if it did
        "showValue": not proxy,
    }


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

# ---- cross-asset drivers ----
prev = read_previous()
now = datetime.now(timezone.utc)
# the scheduled run lands every 5 minutes; take the one nearest each half hour
due = now.minute < 5 or 30 <= now.minute < 35
drivers = prev.get("drivers") or []
drivers_updated = prev.get("driversUpdated") or ""
diag = {}

if PROBE or due or not drivers:
    fresh = []
    for slot in DRIVER_SLOTS:
        # steady state stops at the first candidate that answers; a probe
        # keeps going so the log shows what every symbol did
        for cand in slot["candidates"]:
            sym = cand["sym"]
            try:
                q = fetch_quote(sym)
            except Exception as e:
                diag[sym] = f"error: {e}"
                time.sleep(THROTTLE_SECONDS)
                continue
            ok = isinstance(q, dict) and "close" in q
            if ok:
                diag[sym] = "ok"
                if not any(d["key"] == slot["key"] for d in fresh):
                    fresh.append(as_driver(slot, cand, q))
            else:
                code = q.get("code", "?") if isinstance(q, dict) else "?"
                msg = str(q.get("message", ""))[:140] if isinstance(q, dict) else str(q)[:140]
                diag[sym] = f"{code}: {msg}"
            time.sleep(THROTTLE_SECONDS)
            if ok and not PROBE:
                break
    # a slot that answered nothing this run keeps its last good value
    # rather than blinking out of the card
    if fresh:
        by_key = {d["key"]: d for d in drivers}
        for d in fresh:
            by_key[d["key"]] = d
        order = [s["key"] for s in DRIVER_SLOTS]
        drivers = [by_key[k] for k in order if k in by_key]
        drivers_updated = now.isoformat(timespec="seconds").replace("+00:00", "Z")

out = {
    "spot": round(float(quote["close"]), 2),
    "changePct": round(float(quote.get("percent_change", 0) or 0), 2),
    "marketOpen": bool(quote.get("is_market_open", True)),
    "updated": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
    "source": "XAU/USD",
    "contract": "Gold spot",
    "candles": candles,
    "drivers": drivers,
    "driversUpdated": drivers_updated,
}
if PROBE:
    out["driverDiag"] = diag

with open("spot.json", "w") as f:
    json.dump(out, f, indent=2)
    f.write("\n")

print(f"wrote spot.json: spot={out['spot']} candles={len(candles)} drivers={len(drivers)}")
for k, v in diag.items():
    print(f"  driver probe {k}: {v}")
