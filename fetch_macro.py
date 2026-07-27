"""Builds macro.json: the ToroFX Macro Compass read, computed server-side.

Sits alongside fetch_spot.py in the relay repo and runs on the same
schedule. Neither source used here needs an API key, so this adds nothing
to the Twelve Data budget that fetch_spot.py is already close to spending.

Sources, and why they are split
-------------------------------
Twelve Data's free tier 404s on every direct macro instrument (US10Y, TNX,
DXY, WTI), which is why fetch_spot.py's regime chip is stuck publishing
ETF direction with no levels. Macro Compass needs actual levels: "10Y at
4.71, 4bp from 4.70" is the row. So it goes elsewhere.

  Yahoo  v8/finance/chart, no key, intraday levels.
         ^TNX 10Y, DX-Y.NYB dollar index, CL=F WTI, TIP, IEF, 2YY=F.
         Unofficial. Treat an outage as expected, not exceptional:
         every row degrades to null rather than failing the run.

  FRED   fredgraph.csv, no key, official, end of day.
         DFII10 real yield, T10YIE breakeven, DGS10/DGS2 cash levels.
         The Pine indicator already labels these STALE, so the lag is
         part of the design rather than a regression.

The alignment trap
------------------
FRED publishes DFII10 and T10YIE on different lags. On 2026-07-26 the
real-yield series ended 07-23 while breakeven had 07-24 already. R13
attribution compares the two deltas bps-for-bps to decide REAL-LED vs
BE-LED, so taking each series' own last value compares a Thursday move
against a Friday one and the tag goes wrong without ever looking wrong.
TradingView never exposes this because request.security aligns both on
the same bar. `aligned_deltas` below is the fix: latest date where every
series involved has a value, and the step before it.

Sanity anchor: DGS10 - DFII10 == T10YIE holds exactly on a common date
(4.71 - 2.43 = 2.28 on 2026-07-23), so once aligned the decomposition is
self-consistent and a drift means the alignment broke.

Usage:
    python fetch_macro.py            # write macro.json
    python fetch_macro.py --probe    # print diagnostics, write nothing
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

import macro_logic as L

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
TIMEOUT = 20

# Yahoo slots. `proxy` set means the printed level would be misleading, so
# only direction and change may be published, same contract fetch_spot.py
# uses for its ETF stand-ins.
YAHOO = {
    "y10":    {"sym": "^TNX",     "proxy": ""},
    "dxy":    {"sym": "DX-Y.NYB", "proxy": ""},
    "wti":    {"sym": "CL=F",     "proxy": ""},
    "tip":    {"sym": "TIP",      "proxy": ""},
    "ief":    {"sym": "IEF",      "proxy": ""},
    # CBOE 2-year yield FUTURE, not the cash 2Y. It prices the expected yield
    # at expiry, so its level sits well off spot (4.05 against a 4.37 cash 2Y
    # on 2026-07-23). The pressure logic only ever consumes the delta, so the
    # future is fine for that and the level comes from FRED DGS2 instead.
    "y2":     {"sym": "2YY=F",    "proxy": "CBOE 2Y yield future"},
}

FRED_SERIES = ("DFII10", "T10YIE", "DGS10", "DGS2")


# ------------------------------------------------------------------ fetching

def _get(url):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
        return r.read().decode("utf-8", "replace")


def fetch_yahoo(symbol):
    """Latest price and PRIOR SESSION close. None on any failure.

    Do not reach for meta.chartPreviousClose here, however much it looks
    like the right field. It means "close before the requested range", not
    "yesterday", so it silently rescales with the range parameter. Measured
    on ^TNX, 2026-07-26:

        range=1d -> 4.703  (the real prior session)
        range=5d -> 4.598  (four sessions further back)

    Against a 4.679 last price that is the difference between -2.4bp and
    +8.1bp: not a magnitude error, a sign flip. It would have driven the
    rate complex Bearish on a session that closed with yields lower, and
    taken the headline bias with it.

    So the deltas come off the daily close series instead, which cannot be
    reinterpreted: last two non-null closes, exactly Pine's close vs
    close[1] on a daily request.
    """
    url = ("https://query1.finance.yahoo.com/v8/finance/chart/"
           + urllib.parse.quote(symbol) + "?range=1mo&interval=1d")
    try:
        res = (json.loads(_get(url)).get("chart") or {}).get("result")
        if not res:
            return None
        node = res[0]
        closes = [c for c in node["indicators"]["quote"][0]["close"] if c is not None]
        if len(closes) < 2:
            return None
        px = node.get("meta", {}).get("regularMarketPrice")
        price = float(px) if px is not None else float(closes[-1])
        return {"price": price, "prev": float(closes[-2])}
    except Exception:
        return None


def fetch_fred(series_id, days=90):
    """({date_str: float}, error_or_empty) for a FRED series.

    Returns the reason rather than swallowing it. An empty dict with no
    error means the series parsed but held nothing usable, which is a very
    different problem from being blocked, and the two are indistinguishable
    once the exception is gone.
    """
    start = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    url = (f"https://fred.stlouisfed.org/graph/fredgraph.csv"
           f"?id={urllib.parse.quote(series_id)}&cosd={start}")
    out = {}
    try:
        body = _get(url)
    except urllib.error.HTTPError as e:
        return {}, f"HTTP {e.code} {e.reason}"
    except Exception as e:
        return {}, f"{type(e).__name__}: {e}"

    lines = body.replace("\r", "").strip().splitlines()
    for line in lines[1:]:
        parts = line.split(",")
        if len(parts) != 2 or parts[1] in (".", ""):
            continue
        try:
            out[parts[0]] = float(parts[1])
        except ValueError:
            continue
    if not out:
        # show what actually came back, so an HTML error page or a login
        # wall is obvious instead of looking like an empty series
        return {}, f"parsed 0 rows from {len(lines)} lines: {body[:120]!r}"
    return out, ""


# ----------------------------------------------------------------- alignment

def aligned_deltas(series_map, keys):
    """Deltas in bps for `keys`, all measured over one shared window.

    Returns (levels, deltas_bps, as_of, prior) or (None, None, None, None)
    when the series share fewer than two dates. Read the module docstring
    before touching this; the whole R13 tag rests on it.
    """
    dated = [set(series_map.get(k, {})) for k in keys]
    if not dated or not all(dated):
        return None, None, None, None
    common = sorted(set.intersection(*dated))
    if len(common) < 2:
        return None, None, None, None
    as_of, prior = common[-1], common[-2]
    levels = {k: series_map[k][as_of] for k in keys}
    deltas = {k: (series_map[k][as_of] - series_map[k][prior]) * 100 for k in keys}
    return levels, deltas, as_of, prior


def pct_change(quote):
    if not quote:
        return None
    return ((quote["price"] - quote["prev"]) / quote["prev"]) * 100


def bps_change(quote):
    """For instruments already quoted as a yield in percent."""
    if not quote:
        return None
    return (quote["price"] - quote["prev"]) * 100


# ---------------------------------------------------------------------- main

def build(probe=False):
    diag = {}

    quotes = {}
    for key, slot in YAHOO.items():
        q = fetch_yahoo(slot["sym"])
        quotes[key] = q
        diag[slot["sym"]] = "ok" if q else "no data"

    fred = {}
    for sid in FRED_SERIES:
        s, err = fetch_fred(sid)
        fred[sid] = s
        diag[sid] = f"{len(s)} pts, last {max(s)}" if s else f"NO DATA - {err}"

    # Real yield and breakeven must share a window; see module docstring.
    _, r_deltas, infl_as_of, infl_prior = aligned_deltas(fred, ("DFII10", "T10YIE"))
    real_delta = r_deltas["DFII10"] if r_deltas else None
    be_delta = r_deltas["T10YIE"] if r_deltas else None

    real_level = fred["DFII10"].get(infl_as_of) if infl_as_of else None
    be_level = fred["T10YIE"].get(infl_as_of) if infl_as_of else None

    # Cross-check the decomposition against cash 10Y over the same window.
    drift = None
    if infl_as_of and infl_prior and infl_as_of in fred["DGS10"] and infl_prior in fred["DGS10"]:
        cash = (fred["DGS10"][infl_as_of] - fred["DGS10"][infl_prior]) * 100
        drift = round(cash - (real_delta + be_delta), 2)
        diag["decomposition_drift_bps"] = drift

    # TIP/IEF ratio: live real-yield direction.
    tip, ief = quotes.get("tip"), quotes.get("ief")
    tip_ief = None
    if tip and ief and ief["price"] and ief["prev"]:
        now_r = tip["price"] / ief["price"]
        prev_r = tip["prev"] / ief["prev"]
        if prev_r:
            tip_ief = ((now_r - prev_r) / prev_r) * 100

    proxy_change = (pct_change(quotes.get("wti"))
                    if L.PROXY_CHANGE_MODE == "Percent"
                    else bps_change(quotes.get("wti")))

    dgs2_last = max(fred["DGS2"]) if fred["DGS2"] else None

    readings = {
        "dxy_change_pct": pct_change(quotes.get("dxy")),
        "y10": quotes["y10"]["price"] if quotes.get("y10") else None,
        "y10_delta_bps": bps_change(quotes.get("y10")),
        # level from FRED cash (stale but true), delta from the future (live)
        "y2": fred["DGS2"].get(dgs2_last) if dgs2_last else None,
        "y2_delta_bps": bps_change(quotes.get("y2")),
        "real": real_level,
        "real_delta_bps": real_delta,
        "breakeven": be_level,
        "be_delta_bps": be_delta,
        "tip_ief_change_pct": tip_ief,
        "proxy_change": proxy_change,
    }

    verdict = L.compose(readings)
    now = datetime.now(timezone.utc)

    out = {
        "updated": now.isoformat(timespec="seconds").replace("+00:00", "Z"),
        "eventDay": False,
        "readings": {k: (round(v, 4) if isinstance(v, float) else v)
                     for k, v in readings.items()},
        "verdict": verdict,
        "provenance": {
            "y10":  {"source": "Yahoo ^TNX", "live": True, "showValue": True},
            "dxy":  {"source": "Yahoo DX-Y.NYB", "live": True, "showValue": True},
            "y2":   {"source": "FRED DGS2 level + 2YY=F delta", "live": True,
                     "showValue": True, "levelAsOf": dgs2_last,
                     "note": "level is EOD cash, change is the yield future"},
            "real": {"source": "FRED DFII10", "live": False, "showValue": True,
                     "asOf": infl_as_of},
            "breakeven": {"source": "FRED T10YIE", "live": False, "showValue": True,
                          "asOf": infl_as_of},
            "realProxy": {"source": "Yahoo TIP/IEF", "live": True, "showValue": False},
            "inflProxy": {"source": "Yahoo CL=F", "live": True, "showValue": True,
                          "label": L.PROXY_LABEL},
        },
        "window": {"asOf": infl_as_of, "prior": infl_prior,
                   "decompositionDriftBps": drift},
    }

    if probe:
        out["diag"] = diag
    return out, diag


if __name__ == "__main__":
    probe = "--probe" in sys.argv
    result, diag = build(probe=probe)

    if probe:
        print("Macro Compass probe\n")
        for k, v in diag.items():
            print(f"  {k:<28} {v}")
        print("\nverdict:")
        for k, v in result["verdict"].items():
            print(f"  {k:<24} {v}")
        print(f"\nwindow: {result['window']}")
        sys.exit(0)

    with open("macro.json", "w") as f:
        json.dump(result, f, indent=2)
        f.write("\n")

    v = result["verdict"]
    print(f"wrote macro.json: bias={v['bias']} score={v['score']} "
          f"r13={v['r13']} window={result['window']['asOf']}")
    if result["window"]["decompositionDriftBps"] not in (None, 0.0):
        # real + breakeven should reconstruct the cash 10Y move exactly;
        # anything else means the alignment is not doing its job
        print(f"WARNING: decomposition drift "
              f"{result['window']['decompositionDriftBps']}bp", file=sys.stderr)
