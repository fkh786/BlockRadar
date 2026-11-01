"""
Price-action analytics: swings, order blocks, liquidity pools, liquidity sweeps
("traps"), support/resistance and a 0-100 strength score.

Everything here is plain Python and works on a list of 5-minute candles
(oldest first). Order blocks are a price-action *model* - they are inferred
from candles, not read from any institutional order book.
"""
from __future__ import annotations

from collections import OrderedDict

from .models import Candle, StockData, aggregate

SWING_LEFT = 3
SWING_RIGHT = 3
MIN_DISPLACEMENT_ATR = 1.2     # how strong the move away from an OB must be
OB_SEARCH_BACK = 6             # bars to look back for the last opposite candle
EQUAL_LEVEL_ATR = 0.15         # tolerance for "equal highs / equal lows"
TRAP_LOOKBACK = 6              # bars in which a liquidity sweep counts as recent


# ---------------------------------------------------------------- helpers

def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def split_days(candles: list[Candle]) -> "OrderedDict":
    days: OrderedDict = OrderedDict()
    for c in candles:
        days.setdefault(c.day, []).append(c)
    return days


def atr(candles: list[Candle], n: int = 14) -> float:
    if len(candles) < 2:
        return max((candles[0].h - candles[0].l) if candles else 0.0, 1e-9)
    trs = []
    for i in range(1, len(candles)):
        c, p = candles[i], candles[i - 1]
        trs.append(max(c.h - c.l, abs(c.h - p.c), abs(c.l - p.c)))
    window = trs[-n:]
    return max(sum(window) / len(window), 1e-9)


def find_swings(candles: list[Candle], left=SWING_LEFT, right=SWING_RIGHT):
    """Fractal swing points. Returns lists of (index, price) for highs and lows."""
    highs, lows = [], []
    n = len(candles)
    for i in range(left, n - right):
        h, l = candles[i].h, candles[i].l
        if all(h >= candles[j].h for j in range(i - left, i + right + 1) if j != i) and \
           any(h > candles[j].h for j in range(i - left, i + right + 1) if j != i):
            highs.append((i, h))
        if all(l <= candles[j].l for j in range(i - left, i + right + 1) if j != i) and \
           any(l < candles[j].l for j in range(i - left, i + right + 1) if j != i):
            lows.append((i, l))
    return highs, lows


# ---------------------------------------------------------------- order blocks

def find_order_blocks(candles: list[Candle], atr_val: float, swing: int = SWING_RIGHT):
    """
    Bullish OB: the last bearish candle before a displacement move that breaks
    a prior swing high (break of structure). Bearish OB is the mirror image.
    Each OB carries its zone, the break that confirmed it, whether the move
    left a fair-value gap, how often price has come back to it, and whether it
    has been broken (closed through).
    """
    highs, lows = find_swings(candles, swing, swing)
    obs = []
    structure = 0          # +1 last break was up, -1 down
    last_break_idx = -1
    used_highs, used_lows = set(), set()

    hi_ptr = lo_ptr = 0
    active_highs, active_lows = [], []   # swings confirmed so far
    for i in range(len(candles)):
        # a swing at index k is only known once k + SWING_RIGHT bars have closed
        while hi_ptr < len(highs) and highs[hi_ptr][0] + swing <= i:
            active_highs.append(highs[hi_ptr]); hi_ptr += 1
        while lo_ptr < len(lows) and lows[lo_ptr][0] + swing <= i:
            active_lows.append(lows[lo_ptr]); lo_ptr += 1

        c = candles[i]
        # ---- bullish break of structure
        if active_highs:
            k, level = active_highs[-1]
            if k not in used_highs and c.c > level:
                used_highs.add(k)
                ob = _build_ob(candles, k, i, atr_val, bullish=True)
                if ob:
                    obs.append(ob)
                structure, last_break_idx = 1, i
        # ---- bearish break of structure
        if active_lows:
            k, level = active_lows[-1]
            if k not in used_lows and c.c < level:
                used_lows.add(k)
                ob = _build_ob(candles, k, i, atr_val, bullish=False)
                if ob:
                    obs.append(ob)
                structure, last_break_idx = -1, i

    # status of each OB after it formed
    for ob in obs:
        touches, broken = 0, False
        inside = False
        for j in range(ob["break_idx"] + 1, len(candles)):
            cj = candles[j]
            if ob["kind"] == "bull":
                if cj.c < ob["bottom"]:
                    broken = True; ob["broken_t"] = cj.t; break
                now_inside = cj.l <= ob["top"]
            else:
                if cj.c > ob["top"]:
                    broken = True; ob["broken_t"] = cj.t; break
                now_inside = cj.h >= ob["bottom"]
            if now_inside and not inside:
                touches += 1
            inside = now_inside
        ob["touches"] = touches
        ob["status"] = "broken" if broken else ("tested" if touches else "fresh")

    return obs, structure, last_break_idx, highs, lows


def _build_ob(candles, swing_idx, break_idx, atr_val, bullish):
    seg = range(swing_idx, break_idx + 1)
    if bullish:
        leg_start = min(seg, key=lambda j: candles[j].l)          # low of the leg
        is_opposite = lambda cc: cc.c < cc.o
    else:
        leg_start = max(seg, key=lambda j: candles[j].h)          # high of the leg
        is_opposite = lambda cc: cc.c > cc.o
    ob_idx = None
    for j in range(leg_start, max(leg_start - OB_SEARCH_BACK, 0) - 1, -1):
        if is_opposite(candles[j]):
            ob_idx = j
            break
    if ob_idx is None:
        ob_idx = leg_start
    oc = candles[ob_idx]
    top, bottom = oc.h, oc.l
    # very tall candles: use the body + the wick on the far side only
    if top - bottom > 1.5 * atr_val:
        if bullish:
            top = max(oc.o, oc.c)
        else:
            bottom = min(oc.o, oc.c)

    leg = candles[ob_idx:break_idx + 1]
    if bullish:
        displacement = (max(x.h for x in leg) - top) / atr_val
    else:
        displacement = (bottom - min(x.l for x in leg)) / atr_val
    if displacement < MIN_DISPLACEMENT_ATR:
        return None

    fvg = False
    for m in range(ob_idx + 1, break_idx + 1):
        if m + 1 >= len(candles):
            break
        if bullish and candles[m + 1].l > candles[m - 1].h:
            fvg = True; break
        if not bullish and candles[m + 1].h < candles[m - 1].l:
            fvg = True; break

    vols = [x.v for x in candles[max(0, break_idx - 20):break_idx]] or [1]
    avg_v = sum(vols) / len(vols) or 1
    leg_vol = sum(x.v for x in leg) / len(leg)
    vol_ratio = leg_vol / avg_v

    strength = clamp(displacement / 4, 0, 1) * 60 + (20 if fvg else 0) + clamp(vol_ratio - 1, 0, 1) * 20
    return {
        "kind": "bull" if bullish else "bear",
        "top": round(top, 2),
        "bottom": round(bottom, 2),
        "t": oc.t,
        "idx": ob_idx,
        "break_idx": break_idx,
        "break_t": candles[break_idx].t,
        "displacement_atr": round(displacement, 2),
        "fvg": fvg,
        "strength": round(strength),
    }


# ---------------------------------------------------------------- liquidity

def liquidity_levels(candles, highs, lows, atr_val, prev_day):
    """
    Resting liquidity = clusters of stop orders. We mark:
      - equal highs / equal lows (two or more swing points at ~ the same price)
      - previous day high / low
      - other unswept swing highs / lows
    A level is 'swept' once price has traded through it.
    """
    tol = EQUAL_LEVEL_ATR * atr_val
    levels = []

    def swept_after(idx, price, side):
        for j in range(idx + 1, len(candles)):
            if side == "BSL" and candles[j].h > price:
                return j
            if side == "SSL" and candles[j].l < price:
                return j
        return None

    def add_side(points, side, eq_label, single_label):
        pts = points[-25:]
        used = set()
        for a in range(len(pts)):
            if a in used:
                continue
            group = [pts[a]]
            for b in range(a + 1, len(pts)):
                if b not in used and abs(pts[b][1] - pts[a][1]) <= tol:
                    group.append(pts[b]); used.add(b)
            used.add(a)
            price = max(p[1] for p in group) if side == "BSL" else min(p[1] for p in group)
            last_idx = max(p[0] for p in group)
            sw = swept_after(last_idx, price, side)
            levels.append({
                "price": round(price, 2),
                "side": side,
                "label": eq_label if len(group) > 1 else single_label,
                "weight": len(group),
                "idx": last_idx,
                "t": candles[last_idx].t,
                "swept_idx": sw,
            })

    add_side(highs, "BSL", "Equal highs", "Swing high")
    add_side(lows, "SSL", "Equal lows", "Swing low")

    if prev_day:
        pdh, pdl, first_idx = prev_day
        for price, side, label in ((pdh, "BSL", "Prev day high"), (pdl, "SSL", "Prev day low")):
            sw = swept_after(first_idx - 1, price, side)
            levels.append({"price": round(price, 2), "side": side, "label": label, "weight": 2,
                           "idx": first_idx, "t": candles[first_idx].t if first_idx < len(candles) else 0,
                           "swept_idx": sw})
    return levels


def detect_trap(candles, levels, lookback=TRAP_LOOKBACK):
    """
    Liquidity trap = a recent candle ran the stops beyond a level and then
    closed back on the other side. Sweeping sell-side liquidity and reclaiming
    is a bullish trap (shorts / stop-losses trapped); the mirror is bearish.
    """
    n = len(candles)
    best = None
    for lv in levels:
        sw = lv["swept_idx"]
        if sw is None or sw < n - lookback:
            continue
        c = candles[sw]
        if lv["side"] == "SSL" and c.c > lv["price"]:
            later_ok = all(x.c > lv["price"] for x in candles[sw:])
            if later_ok:
                cand = {"dir": 1, "level": lv["price"], "label": lv["label"], "t": c.t,
                        "text": f"Sell-side liquidity swept at {lv['price']} and reclaimed"}
                if best is None or sw > best["_i"]:
                    best = {**cand, "_i": sw}
        if lv["side"] == "BSL" and c.c < lv["price"]:
            later_ok = all(x.c < lv["price"] for x in candles[sw:])
            if later_ok:
                cand = {"dir": -1, "level": lv["price"], "label": lv["label"], "t": c.t,
                        "text": f"Buy-side liquidity swept at {lv['price']} and rejected"}
                if best is None or sw > best["_i"]:
                    best = {**cand, "_i": sw}
    if best:
        best.pop("_i")
    return best


# ---------------------------------------------------------------- scoring

def efficiency_ratio(closes):
    if len(closes) < 3:
        return 0.0
    net = abs(closes[-1] - closes[0])
    path = sum(abs(closes[i] - closes[i - 1]) for i in range(1, len(closes)))
    return net / path if path else 0.0


def vwap(day_candles):
    pv = sum(((c.h + c.l + c.c) / 3) * c.v for c in day_candles)
    v = sum(c.v for c in day_candles)
    return pv / v if v else (day_candles[-1].c if day_candles else 0.0)


def oi_read(price_chg, oi, oi_base):
    if not oi or not oi_base:
        return None, 0.0
    oi_chg = (oi - oi_base) / oi_base * 100
    if abs(oi_chg) < 0.5:
        return {"label": "OI flat", "oi_chg": round(oi_chg, 2)}, 0.0
    if price_chg >= 0 and oi_chg > 0:
        return {"label": "Long buildup", "oi_chg": round(oi_chg, 2)}, 1.0
    if price_chg >= 0 and oi_chg < 0:
        return {"label": "Short covering", "oi_chg": round(oi_chg, 2)}, 0.5
    if price_chg < 0 and oi_chg > 0:
        return {"label": "Short buildup", "oi_chg": round(oi_chg, 2)}, -1.0
    return {"label": "Long unwinding", "oi_chg": round(oi_chg, 2)}, -0.5


HTF_SETTINGS = {"15m": (15, 3), "1h": (60, 2)}     # minutes per bar, swing size
WORK_SESSIONS = 6                                  # 5-min sessions used for structure


def htf_view(candles: list[Candle], ltp: float, minutes: int, swing: int):
    """Order blocks and structure on a higher timeframe built from 5-min candles."""
    bars = aggregate(candles, minutes)
    if len(bars) < 4 * swing + 6:
        return None
    a = atr(bars, 14)
    obs, structure, _, _, _ = find_order_blocks(bars, a, swing)
    active = [o for o in obs if o["status"] != "broken"]
    bull_below = [o for o in active if o["kind"] == "bull" and o["bottom"] <= ltp]
    bear_above = [o for o in active if o["kind"] == "bear" and o["top"] >= ltp]
    near_bull = max(bull_below, key=lambda o: o["top"]) if bull_below else None
    near_bear = min(bear_above, key=lambda o: o["bottom"]) if bear_above else None
    return {
        "structure": structure,
        "atr": round(a, 2),
        "in_bull_ob": bool(near_bull and near_bull["top"] >= ltp),
        "in_bear_ob": bool(near_bear and near_bear["bottom"] <= ltp),
        "near_bull": near_bull,
        "near_bear": near_bear,
        "obs": obs[-10:],
    }


def _alignment(bias: str, view: dict | None) -> int:
    """+1 higher timeframe agrees with the bias, -1 it disagrees, 0 neutral/unknown."""
    if not view or bias == "choppy":
        return 0
    want = 1 if bias == "bullish" else -1
    support_zone = view["in_bull_ob"] if want == 1 else view["in_bear_ob"]
    against_zone = view["in_bear_ob"] if want == 1 else view["in_bull_ob"]
    if view["structure"] == want or support_zone:
        return -1 if against_zone and view["structure"] != want else 1
    if view["structure"] == -want or against_zone:
        return -1
    return 0


def analyze(sd: StockData) -> dict | None:
    all_candles = sd.candles
    if len(all_candles) < 30:
        return None
    days = split_days(all_candles)
    day_keys = list(days.keys())
    today = days[day_keys[-1]]
    prior = [days[k] for k in day_keys[:-1]]
    keep = set(day_keys[-WORK_SESSIONS:])
    candles = [c for c in all_candles if c.day in keep]

    ltp = sd.ltp or candles[-1].c
    prev_close = sd.prev_close or (prior[-1][-1].c if prior else today[0].o)
    a = atr(candles, 14)
    has_volume = sum(c.v for c in today) > 0

    # typical daily range (%), from prior sessions
    ranges = [(max(c.h for c in d) - min(c.l for c in d)) / d[-1].c * 100 for d in prior[-10:] if d]
    adr_pct = (sum(ranges) / len(ranges)) if ranges else max((max(c.h for c in today) - min(c.l for c in today)) / ltp * 100, 0.5)
    adr_pct = max(adr_pct, 0.3)

    move_pct = (ltp - prev_close) / prev_close * 100 if prev_close else 0.0
    norm_move = move_pct / adr_pct

    obs, structure, last_break_idx, highs, lows = find_order_blocks(candles, a)

    prev_day = None
    if prior:
        pd = prior[-1]
        prev_day = (max(c.h for c in pd), min(c.l for c in pd), len(candles) - len(today))
    levels = liquidity_levels(candles, highs, lows, a, prev_day)
    trap = detect_trap(candles, levels)

    er = efficiency_ratio([today[0].o] + [c.c for c in today])
    vw = vwap(today)

    # relative volume vs prior sessions at the same point of the day
    n_bars = len(today)
    cum_today = sum(c.v for c in today)
    past = [sum(c.v for c in d[:n_bars]) for d in prior[-5:] if len(d) >= n_bars]
    rvol = cum_today / (sum(past) / len(past)) if past and sum(past) else 1.0

    oi_info, oi_comp = oi_read(move_pct, sd.oi, sd.oi_base)

    active = [o for o in obs if o["status"] != "broken"]
    bull_obs = sorted([o for o in active if o["kind"] == "bull" and o["bottom"] <= ltp],
                      key=lambda o: ltp - o["top"])
    bear_obs = sorted([o for o in active if o["kind"] == "bear" and o["top"] >= ltp],
                      key=lambda o: o["bottom"] - ltp)
    near_bull = bull_obs[0] if bull_obs else None
    near_bear = bear_obs[0] if bear_obs else None

    ob_comp = 0.0
    if near_bull and (ltp - near_bull["top"]) <= 2 * a:
        ob_comp += 1.0 if near_bull["status"] == "fresh" else 0.6
    if near_bear and (near_bear["bottom"] - ltp) <= 2 * a:
        ob_comp -= 1.0 if near_bear["status"] == "fresh" else 0.6

    comps = [
        (clamp(norm_move / 1.0, -1, 1), 30),            # size of move vs normal range
        (float(structure), 15),                          # last break of structure
        (ob_comp, 10),                                   # order block support / supply
        (float(trap["dir"]) if trap else 0.0, 10),       # recent liquidity trap
    ]
    if has_volume:                                       # indices have no volume, so no VWAP
        vwap_comp = 1.0 if ltp > vw * 1.001 else -1.0 if ltp < vw * 0.999 else 0.0
        comps.append((vwap_comp, 10))
    if oi_info is not None:
        comps.append((oi_comp, 15))                      # futures OI build-up type
    raw = sum(v * w for v, w in comps) / sum(w for _, w in comps)

    if raw > 0.15:
        bias = "bullish"
    elif raw < -0.15:
        bias = "bearish"
    else:
        bias = "choppy"
    if er < 0.18 and abs(norm_move) < 0.6:
        bias = "choppy"

    # ---- higher timeframes
    mtf = {}
    for label, (mins, swing) in HTF_SETTINGS.items():
        mtf[label] = htf_view(all_candles, ltp, mins, swing)
    align = {k: _alignment(bias, v) for k, v in mtf.items()}
    mtf_mult = clamp(1 + sum(0.08 if x > 0 else -0.14 if x < 0 else 0 for x in align.values()), 0.7, 1.16)

    quality = clamp((er - 0.12) / 0.38, 0, 1)            # clean trend vs zig-zag
    rvol_mult = clamp(0.75 + 0.25 * rvol, 0.75, 1.3) if has_volume else 1.0
    score = abs(raw) * 100 * (0.45 + 0.55 * quality) * rvol_mult * mtf_mult
    score = int(round(clamp(score, 0, 100)))
    if bias == "choppy":
        score = 0

    # ---- the "one clear zone" card
    live_levels = [lv for lv in levels if lv["swept_idx"] is None]
    gap = 0.3 * a                                          # ignore levels sitting right on price
    res_cands = [p for _, p in highs if p > ltp + gap]
    sup_cands = [p for _, p in lows if p < ltp - gap]
    resistance = min(res_cands) if res_cands else max(c.h for c in today)
    support = max(sup_cands) if sup_cands else min(c.l for c in today)

    # trade zone: the most recent unbroken OB in the direction of the bias,
    # as long as it is still within reach (about one normal day's range)
    def pick(kind):
        cands = [o for o in active if o["kind"] == kind and
                 (o["bottom"] <= ltp if kind == "bull" else o["top"] >= ltp)]
        cands = [o for o in cands if abs(ltp - (o["top"] if kind == "bull" else o["bottom"])) / ltp * 100 <= adr_pct]
        return max(cands, key=lambda o: o["break_idx"]) if cands else None

    zone = pick("bull") if bias == "bullish" else pick("bear") if bias == "bearish" else None
    htf_confluence = []
    if zone:
        edge = zone["top"] if zone["kind"] == "bull" else zone["bottom"]
        zone = {**zone, "distance_pct": round(abs(ltp - edge) / ltp * 100, 2)}
        for label, view in mtf.items():
            if not view:
                continue
            for o in view["obs"]:
                if o["kind"] == zone["kind"] and o["status"] != "broken" and \
                        o["bottom"] <= zone["top"] and o["top"] >= zone["bottom"]:
                    htf_confluence.append(label)
                    break
        if htf_confluence and score:
            score = min(100, score + 4 * len(htf_confluence))
    if bias == "bearish":
        stop_liq = min((lv["price"] for lv in live_levels if lv["side"] == "BSL" and lv["price"] > ltp), default=None)
        target_liq = max((lv["price"] for lv in live_levels if lv["side"] == "SSL" and lv["price"] < ltp), default=None)
    else:
        stop_liq = max((lv["price"] for lv in live_levels if lv["side"] == "SSL" and lv["price"] < ltp), default=None)
        target_liq = min((lv["price"] for lv in live_levels if lv["side"] == "BSL" and lv["price"] > ltp), default=None)

    in_zone = bool(zone and zone["bottom"] <= ltp <= zone["top"])

    return {
        "symbol": sd.symbol,
        "name": sd.name,
        "sector": sd.sector,
        "kind": sd.kind,
        "ltp": round(ltp, 2),
        "prev_close": round(prev_close, 2),
        "change_pct": round(move_pct, 2),
        "norm_move": round(norm_move, 2),
        "adr_pct": round(adr_pct, 2),
        "bias": bias,
        "score": score,
        "raw": round(raw, 3),
        "efficiency": round(er, 2),
        "rvol": round(rvol, 2),
        "has_volume": has_volume,
        "vwap": round(vw, 2),
        "atr": round(a, 2),
        "structure": structure,
        "oi": oi_info,
        "trap": trap,
        "zone": zone,
        "in_zone": in_zone,
        "htf_confluence": htf_confluence,
        "mtf": {k: ({"structure": v["structure"], "in_bull_ob": v["in_bull_ob"], "in_bear_ob": v["in_bear_ob"],
                     "align": align[k]} if v else None) for k, v in mtf.items()},
        "mtf_obs": {k: (v["obs"] if v else []) for k, v in mtf.items()},
        "support": round(support, 2),
        "resistance": round(resistance, 2),
        "stop_liquidity": stop_liq,
        "target_liquidity": target_liq,
        "order_blocks": obs[-12:],
        "liquidity": levels,
        "lot_size": sd.lot_size,
        "updated": sd.updated,
    }
