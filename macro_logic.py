"""Pure port of ToroFX Macro Compass v3.1 (Pine v6) decision logic.

No network, no I/O. Every function here mirrors an `f_*` from the Pine
source so the two can be diffed by eye when the indicator changes.

The Pine `input.*` calls become THRESHOLDS below: on a chart they are
per-user settings, on the site there is one canonical set, so they live
in one dict instead of a settings panel.

Run `python macro_logic.py` to execute the self-test at the bottom.
"""

# Pine `input.float` / `input.bool` defaults, verbatim.
THRESHOLDS = {
    "dxy_neutral_pct":        0.20,   # DXY Neutral Threshold (%)
    "y10_neutral_bps":        5.0,    # 10Y Neutral Threshold (bps)
    "y2_neutral_bps":         3.0,    # 2Y Neutral Threshold (bps)
    "y2_strong_bps":          8.0,    # 2Y Strong Threshold (bps)
    "y2_event_strong_bps":   15.0,    # 2Y Strong Threshold on an event day (bps)
    "real_proxy_noise_pct":   0.00,   # TIP/IEF Noise Threshold (%)
    "breakeven_neutral_bps":  0.00,   # Breakeven Neutral Threshold (bps)
    "r13_ambiguity_bps":      1.0,    # R13 Split Ambiguity (bps)
    "proxy_neutral":          1.00,   # Inflation Proxy Neutral Threshold
}

# Pine's inflation-proxy inputs. "Direct" means the proxy rising implies
# inflation rising (crude); flip to "Inverse" for a proxy that moves the
# other way, "Off" to drop it from the read entirely.
PROXY_LABEL        = "CL"
PROXY_RELATIONSHIP = "Direct"     # Direct | Inverse | Off
PROXY_CHANGE_MODE  = "Percent"    # Percent | Bps


def sign(v):
    """Pine f_sign. na propagates as 0 so callers can branch on it safely."""
    if v is None:
        return 0
    return 1 if v > 0 else (-1 if v < 0 else 0)


# ---------------------------------------------------------------- pressures

def dxy_pressure(change_pct, t=THRESHOLDS):
    """Pine f_dxyPressure. Dollar up is a gold headwind, hence the flip."""
    if change_pct is None:
        return "N/A"
    if abs(change_pct) < t["dxy_neutral_pct"]:
        return "Neutral"
    return "Bearish" if change_pct > 0 else "Bullish"


def y2_pressure(change_bps, event_day=False, t=THRESHOLDS):
    """Pine f_2YPressure.

    The event-day switch is the whole point of the 2Y row: on a CPI or FOMC
    print an 8bp move is ordinary, so the "Strong" bar lifts to 15bp and the
    row stops screaming on noise.
    """
    if change_bps is None:
        return "N/A"
    mag = abs(change_bps)
    strong = t["y2_event_strong_bps"] if event_day else t["y2_strong_bps"]
    if mag < t["y2_neutral_bps"]:
        return "Neutral"
    if mag >= strong:
        return "Strong Bearish" if change_bps > 0 else "Strong Bullish"
    return "Bearish" if change_bps > 0 else "Bullish"


def rate_complex_pressure(y2_bps, y10_bps, t=THRESHOLDS):
    """Pine f_rateComplexPressure.

    Deliberately not an average. The 2Y is checked first and wins outright
    whenever it clears its own neutral band, because the front end is what
    carries the Fed path; the 10Y only gets a vote when the 2Y is quiet.
    """
    if y2_bps is None and y10_bps is None:
        return "N/A"
    if y2_bps is not None and abs(y2_bps) >= t["y2_neutral_bps"]:
        return "Bearish" if y2_bps > 0 else "Bullish"
    if y10_bps is not None and abs(y10_bps) >= t["y10_neutral_bps"]:
        return "Bearish" if y10_bps > 0 else "Bullish"
    return "Neutral"


# ------------------------------------------------------------ 10Y taxonomy

NOMINAL_LEVELS = (4.30, 4.45, 4.50, 4.60)


def nominal_regime(y10):
    """Pine f_nominalRegime: which shelf the 10Y is sitting on."""
    if y10 is None:
        return "N/A"
    if y10 < 4.30:
        return "< 4.30"
    if y10 < 4.45:
        return "4.30-4.45"
    if y10 < 4.50:
        return "4.45-4.50"
    if y10 < 4.60:
        return "4.50-4.60"
    return "> 4.60"


def nearest_nominal_level(y10):
    """Pine f_nearestNominalLevel."""
    if y10 is None:
        return None
    return min(NOMINAL_LEVELS, key=lambda lv: abs(y10 - lv))


# ------------------------------------------------------------------- R13

def r13_attribution(real_delta_bps, be_delta_bps, t=THRESHOLDS):
    """Pine f_r13Attribution: was the nominal move real-yield or inflation led?

    CALLER CONTRACT: both deltas must be measured over the SAME window.
    FRED publishes DFII10 and T10YIE on different lags, so taking each
    series' own last value silently compares different days and this tag
    goes quietly wrong. fetch_macro.py aligns them before calling in.
    """
    if real_delta_bps is None or be_delta_bps is None:
        return "N/A"

    synthetic_nominal = real_delta_bps + be_delta_bps
    real_sign, be_sign = sign(real_delta_bps), sign(be_delta_bps)
    nominal_sign = sign(synthetic_nominal)

    real_mag, be_mag = abs(real_delta_bps), abs(be_delta_bps)

    opposing = real_sign != 0 and be_sign != 0 and real_sign != be_sign
    near_equal = abs(real_mag - be_mag) <= t["r13_ambiguity_bps"]
    real_aligned = real_sign != 0 and real_sign == nominal_sign
    be_aligned = be_sign != 0 and be_sign == nominal_sign

    if nominal_sign == 0:
        return "MIXED"
    if opposing and near_equal:
        return "MIXED"
    if real_aligned and real_mag >= be_mag:
        return "REAL-LED"
    if be_aligned and be_mag > real_mag:
        return "BE-LED"
    return "MIXED"


# -------------------------------------------------------- real-yield proxy

def real_proxy_direction(tip_ief_change_pct, t=THRESHOLDS):
    """TIP/IEF ratio -> live real-yield direction.

    TIPS outperforming nominals means the market is paying up for inflation
    protection, which shows up as real yields falling. So the ratio and the
    direction carry opposite signs. +1 means real yields up.
    """
    if tip_ief_change_pct is None:
        return 0
    if abs(tip_ief_change_pct) <= t["real_proxy_noise_pct"]:
        return 0
    return -1 if tip_ief_change_pct > 0 else 1


def real_proxy_text(tip_ief_change_pct, direction):
    if tip_ief_change_pct is None:
        return "N/A"
    if direction > 0:
        return "REAL UP"
    if direction < 0:
        return "REAL DOWN"
    return "FLAT"


def real_proxy_confirmation(proxy_direction, y10_delta_bps):
    """Does the live TIP/IEF read agree with the live nominal move?

    Agreement means the nominal move is being driven by the real leg, which
    is the leg gold actually trades against. Hence CONFIRMS is the alarming
    state, not the reassuring one.
    """
    nominal_dir = sign(y10_delta_bps)
    if proxy_direction == 0 or nominal_dir == 0:
        return "PROXY FLAT"
    return "REAL CONFIRMS" if proxy_direction == nominal_dir else "REAL DIVERGES"


# --------------------------------------------------------------- inflation

def proxy_inflation_direction(change, t=THRESHOLDS, relationship=PROXY_RELATIONSHIP):
    """Pine f_proxyInflationDirection."""
    if relationship == "Off" or change is None:
        return 0
    if abs(change) < t["proxy_neutral"]:
        return 0
    up = change > 0
    if relationship == "Direct":
        return 1 if up else -1
    return -1 if up else 1


def proxy_inflation_text(direction, relationship=PROXY_RELATIONSHIP):
    if relationship == "Off":
        return "Off"
    if direction > 0:
        return "Inflation Up"
    if direction < 0:
        return "Inflation Down"
    return "Neutral"


def inflation_state(be_delta_bps, proxy_direction,
                    t=THRESHOLDS, relationship=PROXY_RELATIONSHIP):
    """Pine f_inflationState: reconciles the EOD breakeven against the live proxy."""
    if be_delta_bps is None:
        if proxy_direction > 0:
            return "LIVE PROXY UP"
        if proxy_direction < 0:
            return "LIVE PROXY DOWN"
        return "N/A"

    be_dir = sign(be_delta_bps) if abs(be_delta_bps) > t["breakeven_neutral_bps"] else 0

    if relationship == "Off":
        if be_dir > 0:
            return "BE UP EOD"
        if be_dir < 0:
            return "BE DOWN EOD"
        return "BE NEUTRAL"

    if be_dir > 0 and proxy_direction > 0:
        return "INFL UP CONFIRMED"
    if be_dir < 0 and proxy_direction < 0:
        return "INFL DOWN CONFIRMED"
    if be_dir != 0 and proxy_direction != 0 and be_dir != proxy_direction:
        return "DIVERGENCE"
    if be_dir == 0 and proxy_direction > 0:
        return "LIVE PROXY UP"
    if be_dir == 0 and proxy_direction < 0:
        return "LIVE PROXY DOWN"
    if be_dir > 0:
        return "BE UP / PROXY FLAT"
    if be_dir < 0:
        return "BE DOWN / PROXY FLAT"
    return "NEUTRAL"


# -------------------------------------------------------------- bias score

# Pine weights only these two rows. R13, the inflation state, the standalone
# 2Y read and the TIP/IEF proxy are all display-only: they explain the score,
# they never move it. That matches the rate-channel framework, where the
# dollar and the front end are what gold actually trades against.
SCORE_WEIGHTS = {"dxy": 3, "rate_complex": 3}


def pressure_score(pressure, weight):
    """Pine f_score."""
    if pressure in ("Bullish", "Strong Bullish"):
        return weight
    if pressure in ("Bearish", "Strong Bearish"):
        return -weight
    return 0


def macro_bias(score):
    """Pine f_macroBias.

    NOTE the reachable range. Each term is one of {-3, 0, +3}, so the sum is
    only ever -6, -3, 0, +3 or +6. The +/-1 rungs mean "Slight Bullish Gold"
    and "Slight Bearish Gold" are unreachable, and the +/-5 rungs make
    "Strong" mean exactly +/-6. Five live states, not seven. Kept faithful to
    Pine on purpose; widening it is a product decision, not a port fix.
    """
    if score >= 5:
        return "Strong Bullish Gold"
    if score >= 3:
        return "Moderate Bullish Gold"
    if score >= 1:
        return "Slight Bullish Gold"
    if score <= -5:
        return "Strong Bearish Gold"
    if score <= -3:
        return "Moderate Bearish Gold"
    if score <= -1:
        return "Slight Bearish Gold"
    return "Mixed"


def tone_for(pressure):
    """Maps a pressure verdict onto the site's teal/red/amber system."""
    if pressure in ("Bullish", "Strong Bullish"):
        return "bull"
    if pressure in ("Bearish", "Strong Bearish"):
        return "bear"
    if pressure == "Neutral":
        return "flat"
    return "na"


# ------------------------------------------------------------------ compose

def compose(readings, event_day=False, t=THRESHOLDS):
    """Turn raw aligned readings into the full Macro Compass verdict set.

    `readings` keys (all optional, None where unavailable):
      dxy_change_pct, y10, y10_delta_bps, y2, y2_delta_bps,
      real, real_delta_bps, breakeven, be_delta_bps,
      tip_ief_change_pct, proxy_change
    """
    r = readings

    dxy_p = dxy_pressure(r.get("dxy_change_pct"), t)
    y2_p = y2_pressure(r.get("y2_delta_bps"), event_day, t)
    rate_p = rate_complex_pressure(r.get("y2_delta_bps"), r.get("y10_delta_bps"), t)

    rp_dir = real_proxy_direction(r.get("tip_ief_change_pct"), t)
    px_dir = proxy_inflation_direction(r.get("proxy_change"), t)

    r13 = r13_attribution(r.get("real_delta_bps"), r.get("be_delta_bps"), t)
    infl = inflation_state(r.get("be_delta_bps"), px_dir, t)

    score = (pressure_score(dxy_p, SCORE_WEIGHTS["dxy"])
             + pressure_score(rate_p, SCORE_WEIGHTS["rate_complex"]))

    y10 = r.get("y10")
    near = nearest_nominal_level(y10)
    dist_bps = abs(y10 - near) * 100 if (y10 is not None and near is not None) else None

    real_delta = r.get("real_delta_bps")
    be_delta = r.get("be_delta_bps")

    return {
        "dxyPressure": dxy_p,
        "y2Pressure": y2_p,
        "rateComplexPressure": rate_p,
        "nominalRegime": nominal_regime(y10),
        "nearestNominalLevel": near,
        "distanceToNominalBps": round(dist_bps, 1) if dist_bps is not None else None,
        "realRead": ("N/A" if real_delta is None else
                     "Gold Headwind" if real_delta > 0 else
                     "Gold Tailwind" if real_delta < 0 else "Flat"),
        "breakevenRead": ("N/A" if be_delta is None else
                          "Inflation Up" if be_delta > 0 else
                          "Inflation Down" if be_delta < 0 else "Flat"),
        "realProxyDirection": rp_dir,
        "realProxyText": real_proxy_text(r.get("tip_ief_change_pct"), rp_dir),
        "realProxyConfirmation": real_proxy_confirmation(rp_dir, r.get("y10_delta_bps")),
        "inflationProxyRead": proxy_inflation_text(px_dir),
        "inflationState": infl,
        "r13": r13,
        "score": score,
        "bias": macro_bias(score),
        "tone": "bull" if score > 0 else ("bear" if score < 0 else "flat"),
    }


# --------------------------------------------------------------- self-test

def _selftest():
    """Worked examples built only from values verified against live sources
    on 2026-07-26. Nothing here is invented.

    Case A, the FRED end-of-day window 2026-07-22 -> 2026-07-23, which is
    the latest pair of dates where DFII10 and T10YIE BOTH carry a value:
        DFII10  2.39 -> 2.43   = +4.0bp real
        T10YIE  2.28 -> 2.28   =  0.0bp breakeven
        DGS10   4.67 -> 4.71   = +4.0bp nominal  <- equals real + breakeven,
                                                    the alignment anchor
        DGS2    4.31 -> 4.37   = +6.0bp front end
    DXY is left None here on purpose: it has no value on that EOD window,
    and a fabricated one would make the score untraceable.

    Case B and C use the live Yahoo daily closes, second-to-last vs last.
    """
    cases = []

    def check(label, got, want):
        cases.append((label, got, want, got == want))

    # --- Case A: FRED EOD window ---
    readings = {
        "dxy_change_pct": None,
        "y10": 4.71, "y10_delta_bps": 4.0,
        "y2": 4.37,  "y2_delta_bps": 6.0,
        "real": 2.43, "real_delta_bps": 4.0,
        "breakeven": 2.26, "be_delta_bps": 0.0,
        "tip_ief_change_pct": None,
        "proxy_change": None,
    }
    out = compose(readings)

    check("dxy N/A when absent", out["dxyPressure"], "N/A")
    check("2Y Bearish (6bp: over 3, under 8)", out["y2Pressure"], "Bearish")
    check("rate complex Bearish (2Y wins)", out["rateComplexPressure"], "Bearish")
    check("R13 REAL-LED (real did all of it)", out["r13"], "REAL-LED")
    check("real yield = Gold Headwind", out["realRead"], "Gold Headwind")
    check("10Y regime shelf", out["nominalRegime"], "> 4.60")
    check("score = 0 + (-3)", out["score"], -3)
    check("bias", out["bias"], "Moderate Bearish Gold")

    # --- Case B: DXY off real daily closes, 101.468 -> 101.171 ---
    # Dollar down 0.29%, outside the 0.20% band, so gold gets the tailwind.
    # Read off meta.chartPreviousClose at range=5d this was -0.009% and
    # scored Neutral, which is the bug fetch_macro.fetch_yahoo now avoids.
    dxy_change = ((101.171 - 101.468) / 101.468) * 100
    check("dxy Bullish on a 0.29% drop", dxy_pressure(dxy_change), "Bullish")
    check("...and the same move read stale = Neutral",
          dxy_pressure(((101.171 - 101.18) / 101.18) * 100), "Neutral")

    # --- Case C: live 10Y alone, 4.703 -> 4.679 = -2.4bp ---
    # Inside the 5bp band and with no 2Y vote, the complex must stay quiet.
    y10_live = (4.679 - 4.703) * 100
    check("10Y -2.4bp alone = Neutral",
          rate_complex_pressure(None, y10_live), "Neutral")
    check("...and the stale +8.1bp version would have said Bearish",
          rate_complex_pressure(None, (4.679 - 4.598) * 100), "Bearish")

    # Event-day switch: the same 6bp print stops reading as pressure once the
    # neutral band is the only thing it clears... it does not. 6 > 3 either way.
    # What changes is the Strong bar, so check a 12bp move both ways.
    check("12bp normal day = Strong Bearish", y2_pressure(12.0, event_day=False), "Strong Bearish")
    check("12bp event day = Bearish only", y2_pressure(12.0, event_day=True), "Bearish")

    # R13 ambiguity. +3.0/-3.0 would cancel to a flat nominal and exit on the
    # zero branch, testing nothing, so this leans the sum slightly positive:
    # the legs still oppose and sit 0.5bp apart, inside the 1.0bp ambiguity
    # band, so neither may claim the move.
    check("R13 opposing + near-equal = MIXED", r13_attribution(3.0, -2.5), "MIXED")
    check("R13 flat nominal = MIXED", r13_attribution(3.0, -3.0), "MIXED")
    check("R13 BE-LED", r13_attribution(1.0, 5.0), "BE-LED")
    # Same split, but wide enough apart to name a leader.
    check("R13 opposing + wide = REAL-LED", r13_attribution(6.0, -2.0), "REAL-LED")

    # The unreachable rungs, asserted so a future weight change trips this.
    reachable = {pressure_score(a, 3) + pressure_score(b, 3)
                 for a in ("Bullish", "Neutral", "Bearish")
                 for b in ("Bullish", "Neutral", "Bearish")}
    check("reachable scores", reachable, {-6, -3, 0, 3, 6})
    check("no score yields 'Slight'",
          any(macro_bias(s).startswith("Slight") for s in reachable), False)

    width = max(len(c[0]) for c in cases)
    failed = 0
    for label, got, want, ok in cases:
        if not ok:
            failed += 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<{width}}  got={got!r}"
              + ("" if ok else f"  want={want!r}"))
    print(f"\n{len(cases) - failed}/{len(cases)} passed")
    return failed


if __name__ == "__main__":
    import sys
    print("Macro Compass logic self-test\n")
    sys.exit(1 if _selftest() else 0)
