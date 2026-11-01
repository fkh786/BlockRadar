"""
Options helper: expiry calendar (for demo), Black-Scholes pricing (demo data and
fallback Greeks), and translation of an order-block trade plan on the
underlying into option-premium terms.
"""
from __future__ import annotations

import calendar
import math
from datetime import date, datetime, timedelta

from .models import IST, now_ist

RISK_FREE = 0.065


# ------------------------------------------------------------------ expiries (demo)

def _last_weekday(year, month, weekday):
    last = calendar.monthrange(year, month)[1]
    d = date(year, month, last)
    while d.weekday() != weekday:
        d -= timedelta(days=1)
    return d


def demo_expiries(weekly: bool, today: date | None = None, n: int = 4) -> list[str]:
    """NSE moved F&O expiries to Tuesday in 2025: NIFTY weekly, everything else monthly."""
    today = today or now_ist().date()
    out = []
    if weekly:
        d = today
        while len(out) < n:
            if d.weekday() == 1 and d >= today:
                out.append(d.isoformat())
            d += timedelta(days=1)
        return out
    y, m = today.year, today.month
    while len(out) < min(n, 3):
        e = _last_weekday(y, m, 1)
        if e >= today:
            out.append(e.isoformat())
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def years_to_expiry(expiry: str, now: datetime | None = None) -> float:
    now = now or now_ist()
    e = date.fromisoformat(expiry)
    exp_dt = datetime(e.year, e.month, e.day, 15, 30, tzinfo=IST)
    secs = (exp_dt - now).total_seconds()
    return max(secs, 600) / (365 * 24 * 3600)


# ------------------------------------------------------------------ Black-Scholes

def _ncdf(x):
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def _npdf(x):
    return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)


def bs(S: float, K: float, T: float, iv: float, cp: str, r: float = RISK_FREE) -> dict:
    iv = max(iv, 0.01)
    sq = iv * math.sqrt(T)
    d1 = (math.log(S / K) + (r + 0.5 * iv * iv) * T) / sq
    d2 = d1 - sq
    if cp == "CE":
        price = S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
        delta = _ncdf(d1)
        theta = (-S * _npdf(d1) * iv / (2 * math.sqrt(T)) - r * K * math.exp(-r * T) * _ncdf(d2)) / 365
    else:
        price = K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)
        delta = _ncdf(d1) - 1
        theta = (-S * _npdf(d1) * iv / (2 * math.sqrt(T)) + r * K * math.exp(-r * T) * _ncdf(-d2)) / 365
    gamma = _npdf(d1) / (S * sq)
    vega = S * _npdf(d1) * math.sqrt(T) / 100
    return {"price": max(price, 0.05), "delta": delta, "gamma": gamma, "theta": theta, "vega": vega, "iv": iv * 100}


# ------------------------------------------------------------------ plan translation

def project(premium: float, delta: float, gamma: float, s0: float, s1: float) -> float:
    """Option price if the underlying moves from s0 to s1 (delta-gamma estimate, same day)."""
    ds = s1 - s0
    return max(0.05, premium + delta * ds + 0.5 * gamma * ds * ds)


def underlying_plan(r: dict) -> dict:
    """Entry / stop / target on the underlying, from the analysis result."""
    bull = r["bias"] != "bearish"
    z = r.get("zone")
    a = r.get("atr") or 0
    if z:
        entry = z["top"] if bull else z["bottom"]
        stop = (z["bottom"] - 0.25 * a) if bull else (z["top"] + 0.25 * a)
    else:
        entry = r["ltp"]
        stop = r["support"] if bull else r["resistance"]
    target = r.get("target_liquidity")
    if target is None or (bull and target <= entry) or (not bull and target >= entry):
        target = r["resistance"] if bull else r["support"]
    if (bull and target <= entry) or (not bull and target >= entry):
        target = entry + (entry - stop) * 2
    risk = abs(entry - stop) or 1e-9
    return {"direction": "bull" if bull else "bear", "entry": round(entry, 2),
            "stop": round(stop, 2), "target": round(target, 2), "from_zone": bool(z),
            "rr": round(abs(target - entry) / risk, 2)}


def build_helper(r: dict, chain: dict, lot_size: int) -> dict:
    """
    chain = {"expiry", "spot", "rows": [{"strike", "CE": {...}, "PE": {...}}]}
    with each side {"key", "ltp", "oi", "prev_oi", "volume", "iv", "delta", "gamma", "theta"}.
    """
    rows = sorted(chain.get("rows") or [], key=lambda x: x["strike"])
    spot = chain.get("spot") or r["ltp"]
    out = {"expiry": chain.get("expiry"), "spot": spot, "lot_size": lot_size,
           "bias": r["bias"], "candidates": [], "warnings": []}
    if not rows:
        out["warnings"].append("No option chain available for this expiry.")
        return out

    # chain-wide OI picture
    ce_oi = sum((x.get("CE") or {}).get("oi") or 0 for x in rows)
    pe_oi = sum((x.get("PE") or {}).get("oi") or 0 for x in rows)
    out["pcr"] = round(pe_oi / ce_oi, 2) if ce_oi else None
    near = [x for x in rows if abs(x["strike"] - spot) / spot < 0.08] or rows
    max_ce = max(near, key=lambda x: (x.get("CE") or {}).get("oi") or 0)
    max_pe = max(near, key=lambda x: (x.get("PE") or {}).get("oi") or 0)
    out["oi_resistance"] = max_ce["strike"]      # biggest call writing = ceiling
    out["oi_support"] = max_pe["strike"]         # biggest put writing = floor

    days_left = (date.fromisoformat(chain["expiry"]) - now_ist().date()).days if chain.get("expiry") else None
    out["days_left"] = days_left
    if days_left is not None and days_left <= 1:
        out["warnings"].append("Expiry is today/tomorrow: premium decays very fast. Consider the next expiry.")

    if r["bias"] == "choppy":
        out["warnings"].append("No directional setup (choppy). Buying options in a choppy market usually loses to time decay.")
        return out

    side = "CE" if r["bias"] == "bullish" else "PE"
    plan = underlying_plan(r)
    out["side"] = side
    out["plan"] = plan

    strikes = [x["strike"] for x in rows]
    atm_i = min(range(len(strikes)), key=lambda i: abs(strikes[i] - spot))
    step = 1 if side == "CE" else -1           # ITM is below spot for calls, above for puts
    picks = [("ITM", atm_i - step), ("ATM", atm_i), ("OTM", atm_i + step)]

    for tag, i in picks:
        if i < 0 or i >= len(rows):
            continue
        row = rows[i]
        o = row.get(side) or {}
        prem = o.get("ltp") or 0
        if prem <= 0:
            continue
        delta, gamma = o.get("delta"), o.get("gamma")
        if delta is None or gamma is None or (delta == 0 and gamma == 0):
            g = bs(spot, row["strike"], years_to_expiry(chain["expiry"]), (o.get("iv") or 20) / 100, side)
            delta, gamma = g["delta"], g["gamma"]
        e = project(prem, delta, gamma, spot, plan["entry"])
        s = project(prem, delta, gamma, spot, plan["stop"])
        t = project(prem, delta, gamma, spot, plan["target"])
        risk = max(e - s, 0.05)
        oi, poi = o.get("oi") or 0, o.get("prev_oi") or 0
        out["candidates"].append({
            "tag": tag,
            "strike": row["strike"],
            "key": o.get("key"),
            "label": f"{r['symbol']} {chain['expiry']} {row['strike']:g} {side}",
            "ltp": round(prem, 2),
            "oi": oi,
            "oi_chg_pct": round((oi - poi) / poi * 100, 1) if poi else None,
            "volume": o.get("volume"),
            "iv": round(o.get("iv") or 0, 1),
            "delta": round(delta, 2),
            "theta": round(o.get("theta") or 0, 2),
            "entry": round(e, 2),
            "stop": round(s, 2),
            "target": round(t, 2),
            "rr": round((t - e) / risk, 2),
            "cost_per_lot": round(e * lot_size, 0),
            "risk_per_lot": round(risk * lot_size, 0),
            "reward_per_lot": round((t - e) * lot_size, 0),
            "theta_per_lot": round((o.get("theta") or 0) * lot_size, 0),
        })
    if out["candidates"]:
        best = min(out["candidates"], key=lambda c: abs(abs(c["delta"]) - 0.55))
        best["recommended"] = True
        if best["rr"] < 1.5:
            out["warnings"].append(
                f"Reward is only {best['rr']}× the risk at these levels. Better to wait for a pullback "
                f"into the zone{'' if plan['from_zone'] else ' (no zone yet)'} than to buy now.")
    if not plan["from_zone"]:
        out["warnings"].append("No order-block zone: the plan uses the current price and nearest support/resistance.")
    return out


def implied_vol(price: float, S: float, K: float, T: float, cp: str) -> float | None:
    """Solve Black-Scholes for IV (as a fraction) by bisection; None if no solution."""
    if not price or price <= 0 or S <= 0 or K <= 0:
        return None
    intrinsic = max(0.0, S - K) if cp == "CE" else max(0.0, K - S)
    if price < intrinsic * 0.999:
        return None
    lo, hi = 0.01, 3.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if bs(S, K, T, mid, cp)["price"] > price:
            hi = mid
        else:
            lo = mid
    return (lo + hi) / 2


def fill_greeks(chain: dict) -> dict:
    """Add IV / delta / gamma / theta from Black-Scholes where the broker gives none."""
    S = chain.get("spot") or 0
    if not S or not chain.get("expiry"):
        return chain
    T = years_to_expiry(chain["expiry"])
    for row in chain.get("rows", []):
        for cp in ("CE", "PE"):
            o = row.get(cp)
            if not o or o.get("delta") not in (None, 0):
                continue
            iv = implied_vol(o.get("ltp") or 0, S, row["strike"], T, cp)
            if iv is None:
                continue
            g = bs(S, row["strike"], T, iv, cp)
            o.update(iv=round(iv * 100, 2), delta=round(g["delta"], 4), gamma=g["gamma"], theta=round(g["theta"], 3))
    return chain
