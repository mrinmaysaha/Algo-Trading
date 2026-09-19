#!/usr/bin/env python3
"""
================================================================================
OPENALGO GLOBAL CRYPTO BACKTEST & FEE AUDIT ENGINE (DELTA EXCHANGE INDIA)
================================================================================
Permanent canonical backtest verification module for Delta Exchange India.
Enforces official FIU-IND fee structures:
  - Options: 0.010% notional, strictly capped at 3.5% of option premium + 18% GST
  - Futures: 0.020% Limit Maker entry + 0.050% Market Taker exit + 18% GST
================================================================================
"""

import os
import sys
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from datetime import datetime, timezone, timedelta
from typing import Dict, Any, List, Tuple
import requests
import pandas as pd
import numpy as np

# Ensure project root is in path to import broker module
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../.."))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from broker.deltaexchange.delta_fees import (
    calculate_futures_fee,
    calculate_single_option_leg_fee,
    calculate_iron_condor_roundtrip_fee,
    OPTIONS_NOTIONAL_RATE,
    OPTIONS_PREMIUM_CAP,
    GST_RATE,
    FUTURES_MAKER_FEE,
    FUTURES_TAKER_FEE
)

IST_TZ = timezone(timedelta(hours=5, minutes=30))

# ------------------------------------------------------------------------------
# 1. CANDLE DATA FETCHER WITH ROBUST PAGINATION & CACHING
# ------------------------------------------------------------------------------
def fetch_candles(symbol: str, resolution: str = "1h", days: int = 180) -> pd.DataFrame:
    delta_sym = "BTCUSD" if "BTC" in symbol.upper() else "ETHUSD"
    now = int(time.time())
    start = now - (days * 86400)
    all_candles = []
    curr_start = start
    step = 2000 * 900 if resolution == "15m" else 2000 * 3600

    while curr_start < now:
        curr_end = min(curr_start + step, now)
        try:
            r = requests.get(
                "https://api.india.delta.exchange/v2/history/candles",
                params={"symbol": delta_sym, "resolution": resolution, "start": curr_start, "end": curr_end},
                timeout=10
            )
            if r.status_code == 200:
                c = r.json().get("result", [])
                if not c:
                    break
                all_candles.extend(c)
                curr_start = max(x["time"] for x in c) + (900 if resolution == "15m" else 3600)
            else:
                break
        except Exception:
            break

    df = pd.DataFrame(all_candles).drop_duplicates("time").sort_values("time").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume"]:
        if col in df.columns:
            df[col] = df[col].astype(float)
    return df

# ------------------------------------------------------------------------------
# 2. IRON CONDOR PARAMETRIC BACKTEST FUNCTION
# ------------------------------------------------------------------------------
def backtest_iron_condor(
    df_1h: pd.DataFrame,
    symbol: str = "BTC",
    start_hour: int = 7,
    end_hour: int = 17,
    otm_pct: float = 0.030,
    starting_capital_inr: float = 10000.0,
    weekly_compounding: bool = True
) -> Dict[str, Any]:
    """
    Backtests daily 0DTE Iron Condor across 180 days with exact Delta Exchange India fees.
    """
    df = df_1h.copy()
    df["dt"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.tz_convert(IST_TZ)
    df["date"] = df["dt"].dt.date
    df["hour"] = df["dt"].dt.hour

    unique_dates = sorted(df["date"].unique())
    capital = starting_capital_inr
    compounded_cap = capital

    is_btc = ("BTC" in symbol.upper())
    lot_multiplier = 0.001 if is_btc else 0.01
    wing_width = 800.0 if is_btc else 50.0

    trades = []

    for day_idx, d in enumerate(unique_dates):
        if weekly_compounding and day_idx % 7 == 0:
            compounded_cap = capital
        elif not weekly_compounding:
            compounded_cap = starting_capital_inr

        day_df = df[df["date"] == d]
        window = day_df[(day_df["hour"] >= start_hour) & (day_df["hour"] <= end_hour)]
        if len(window) < 3:
            continue

        spot_entry = window.iloc[0]["open"]
        high_max = window["high"].max()
        low_min = window["low"].min()

        # Dynamic lot sizing: 65% max margin locked (35% cash buffer)
        margin_per_contract_inr = (wing_width * lot_multiplier) * 88.0
        max_margin_inr = compounded_cap * 0.65
        lots = max(10, int(max_margin_inr / margin_per_contract_inr))

        short_ce = spot_entry * (1.0 + otm_pct)
        short_pe = spot_entry * (1.0 - otm_pct)

        # Baseline credit collected based on hours to 17:30 expiry
        duration_hrs = end_hour - start_hour
        base_credit_pct = 0.00065 * (duration_hrs / 8.0) ** 0.45
        # OTM discount factor (wider strikes have slightly less premium)
        otm_factor = 1.0 - ((otm_pct - 0.025) * 6.0)
        net_credit_pct = max(0.00045, base_credit_pct * otm_factor)

        net_credit_usd = lots * lot_multiplier * (spot_entry * net_credit_pct)
        short_ce_prem = spot_entry * net_credit_pct * 0.70
        short_pe_prem = spot_entry * net_credit_pct * 0.70
        wing_ce_prem = spot_entry * net_credit_pct * 0.20
        wing_pe_prem = spot_entry * net_credit_pct * 0.20

        # Exact official Delta fee calculation
        fee_calc = calculate_iron_condor_roundtrip_fee(
            spot_price=spot_entry,
            short_ce_strike=short_ce,
            short_ce_premium=short_ce_prem,
            short_pe_strike=short_pe,
            short_pe_premium=short_pe_prem,
            long_ce_strike=short_ce + wing_width,
            long_ce_premium=wing_ce_prem,
            long_pe_strike=short_pe - wing_width,
            long_pe_premium=wing_pe_prem,
            contracts=lots,
            symbol=symbol,
            decay_exit_pct=0.85
        )
        fee_inr = fee_calc["total_fee_with_gst_inr"]

        # Check if short strike was breached
        sl_hit = (high_max >= short_ce * 1.001) or (low_min <= short_pe * 0.999)

        if sl_hit:
            gross_loss_usd = net_credit_usd * 1.50
            gross_inr = -gross_loss_usd * 88.0
            is_win = False
        else:
            gross_win_usd = net_credit_usd * 0.85 # 85% decay captured
            gross_inr = gross_win_usd * 88.0
            is_win = True

        net_inr = gross_inr - fee_inr
        capital += net_inr

        trades.append({
            "date": d,
            "lots": lots,
            "win": is_win,
            "gross_inr": gross_inr,
            "fee_inr": fee_inr,
            "net_inr": net_inr,
            "capital": capital
        })

    tdf = pd.DataFrame(trades)
    wins = tdf["win"].sum()
    total_trades = len(tdf)
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0
    tot_gross = tdf["gross_inr"].sum()
    tot_fee = tdf["fee_inr"].sum()
    tot_net = tdf["net_inr"].sum()
    roi = (tot_net / starting_capital_inr) * 100

    return {
        "symbol": symbol,
        "strategy": "Iron Condor",
        "window": f"{start_hour:02d}:00 - {end_hour:02d}:15 IST ({end_hour-start_hour} hrs)",
        "otm_pct": f"{otm_pct*100:.2f}%",
        "trades": total_trades,
        "wins": wins,
        "losses": total_trades - wins,
        "win_rate": win_rate,
        "gross_inr": tot_gross,
        "fee_inr": tot_fee,
        "net_inr": tot_net,
        "roi_pct": roi,
        "ending_capital_inr": capital,
        "avg_fee_per_trade_inr": tot_fee / max(total_trades, 1)
    }

# ------------------------------------------------------------------------------
# 3. FUTURES LIQUIDITY SWEEP PARAMETRIC BACKTEST FUNCTION
# ------------------------------------------------------------------------------
def backtest_liquidity_sweep(
    df_15m: pd.DataFrame,
    symbol: str = "BTC",
    lookback: int = 36,
    vol_mult: float = 1.2,
    mode: str = "split_33_33_34", # "split_33_33_34", "split_50_50", "baseline", "step_trailing", "atr_trailing"
    tp1_r: float = 1.0,
    tp2_r: float = 2.0,
    tp3_r: float = 3.5,
    rr_ratio: float = 2.5,
    risk_pct_per_trade: float = 0.06,
    max_leverage: float = 12.0,
    min_risk_dist_pct: float = 0.0025,
    max_hold_bars: int = 28,
    starting_capital_inr: float = 10000.0,
    weekly_compounding: bool = True
) -> Dict[str, Any]:
    """
    Backtests 15m Institutional Liquidity Sweep (SFP) with Limit Maker entry,
    Market Taker exit, and advanced Multi-Target scaling / Trailing SL architectures.
    """
    highs = df_15m["high"].values
    lows = df_15m["low"].values
    closes = df_15m["close"].values
    opens = df_15m["open"].values
    vols = df_15m["volume"].values
    n = len(df_15m)

    vol_ma = pd.Series(vols).rolling(20).mean().values
    ema200 = pd.Series(closes).ewm(span=200, adjust=False).mean().values

    # 14-period ATR for chandelier trailing
    tr1 = highs - lows
    tr2 = np.abs(highs - np.roll(closes, 1))
    tr3 = np.abs(lows - np.roll(closes, 1))
    tr = np.maximum(tr1, np.maximum(tr2, tr3))
    atr14 = pd.Series(tr).rolling(14).mean().values

    is_btc = ("BTC" in symbol.upper())
    lot_multiplier = 0.001 if is_btc else 0.01

    capital = starting_capital_inr
    compounded_cap = capital
    trades = []

    in_pos = False
    side = None
    entry_p = 0.0
    sl_p = 0.0
    total_lots = 0
    rem_lots = 0
    bars_held = 0
    highest_p = 0.0
    lowest_p = 1e9

    tp1_hit = False
    tp2_hit = False
    trade_gross_usd = 0.0
    trade_fee_usd = 0.0

    for i in range(200, n):
        # Weekly compounding check (every 7 days = 672 bars of 15m)
        if weekly_compounding and i % 672 == 0:
            compounded_cap = capital

        curr_o = opens[i]
        curr_h = highs[i]
        curr_l = lows[i]
        curr_c = closes[i]
        curr_v = vols[i]
        v_avg = vol_ma[i]
        curr_atr = atr14[i] if not np.isnan(atr14[i]) else (curr_h - curr_l)
        c_range = max(curr_h - curr_l, 1e-5)

        if in_pos:
            bars_held += 1
            risk_dist = abs(entry_p - sl_p)
            highest_p = max(highest_p, curr_h)
            lowest_p = min(lowest_p, curr_l)

            # Trailing SL Adjustments
            if mode == "baseline":
                if side == "LONG" and curr_h >= entry_p + (1.2 * risk_dist) and sl_p < entry_p:
                    sl_p = entry_p * 1.0005
                elif side == "SHORT" and curr_l <= entry_p - (1.2 * risk_dist) and sl_p > entry_p:
                    sl_p = entry_p * 0.9995

            elif mode in ["split_50_50", "split_33_33_34"]:
                if tp1_hit:
                    if side == "LONG" and sl_p < entry_p:
                        sl_p = entry_p * 1.0005
                    elif side == "SHORT" and sl_p > entry_p:
                        sl_p = entry_p * 0.9995
                if tp2_hit:
                    if side == "LONG" and sl_p < entry_p + (1.0 * risk_dist):
                        sl_p = entry_p + (1.0 * risk_dist)
                    elif side == "SHORT" and sl_p > entry_p - (1.0 * risk_dist):
                        sl_p = entry_p - (1.0 * risk_dist)

            elif mode == "step_trailing":
                if side == "LONG":
                    if curr_h >= entry_p + (2.2 * risk_dist):
                        sl_p = max(sl_p, entry_p + (1.5 * risk_dist))
                    elif curr_h >= entry_p + (1.5 * risk_dist):
                        sl_p = max(sl_p, entry_p + (0.8 * risk_dist))
                    elif curr_h >= entry_p + (0.9 * risk_dist):
                        sl_p = max(sl_p, entry_p * 1.0005)
                elif side == "SHORT":
                    if curr_l <= entry_p - (2.2 * risk_dist):
                        sl_p = min(sl_p, entry_p - (1.5 * risk_dist))
                    elif curr_l <= entry_p - (1.5 * risk_dist):
                        sl_p = min(sl_p, entry_p - (0.8 * risk_dist))
                    elif curr_l <= entry_p - (0.9 * risk_dist):
                        sl_p = min(sl_p, entry_p * 0.9995)

            elif mode == "atr_trailing":
                if side == "LONG" and curr_h >= entry_p + (1.0 * risk_dist):
                    sl_p = max(sl_p, highest_p - (1.5 * curr_atr))
                elif side == "SHORT" and curr_l <= entry_p - (1.0 * risk_dist):
                    sl_p = min(sl_p, lowest_p + (1.5 * curr_atr))

            # Target & Exit Execution
            pos_closed = False

            if side == "LONG":
                # TP1 check
                if mode in ["split_50_50", "split_33_33_34"] and not tp1_hit:
                    target1_p = entry_p + (tp1_r * risk_dist)
                    if curr_h >= target1_p:
                        tp1_hit = True
                        lots_to_close = int(total_lots * 0.50) if mode == "split_50_50" else int(total_lots * 0.33)
                        lots_to_close = max(1, min(lots_to_close, rem_lots))
                        gain_usd = lots_to_close * lot_multiplier * (target1_p - entry_p)
                        fee_d = calculate_futures_fee(entry_p, target1_p, lots_to_close, symbol, is_entry_maker=True, is_exit_taker=True)
                        trade_gross_usd += gain_usd
                        trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                        rem_lots -= lots_to_close

                # TP2 check
                if mode == "split_33_33_34" and tp1_hit and not tp2_hit:
                    target2_p = entry_p + (tp2_r * risk_dist)
                    if curr_h >= target2_p:
                        tp2_hit = True
                        lots_to_close = int(total_lots * 0.33)
                        lots_to_close = max(1, min(lots_to_close, rem_lots))
                        gain_usd = lots_to_close * lot_multiplier * (target2_p - entry_p)
                        fee_d = calculate_futures_fee(entry_p, target2_p, lots_to_close, symbol, is_entry_maker=True, is_exit_taker=True)
                        trade_gross_usd += gain_usd
                        trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                        rem_lots -= lots_to_close

                # Final TP check
                final_tp = entry_p + (tp2_r * risk_dist if mode in ["baseline", "split_50_50", "step_trailing", "atr_trailing"] else entry_p + (tp3_r * risk_dist))
                if curr_h >= final_tp and rem_lots > 0:
                    gain_usd = rem_lots * lot_multiplier * (final_tp - entry_p)
                    fee_d = calculate_futures_fee(entry_p, final_tp, rem_lots, symbol, is_entry_maker=True, is_exit_taker=True)
                    trade_gross_usd += gain_usd
                    trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                    rem_lots = 0
                    pos_closed = True
                elif curr_l <= sl_p and rem_lots > 0:
                    loss_usd = rem_lots * lot_multiplier * (sl_p - entry_p)
                    fee_d = calculate_futures_fee(entry_p, sl_p, rem_lots, symbol, is_entry_maker=True, is_exit_taker=True)
                    trade_gross_usd += loss_usd
                    trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                    rem_lots = 0
                    pos_closed = True
                elif bars_held >= max_hold_bars and rem_lots > 0:
                    gain_usd = rem_lots * lot_multiplier * (curr_c - entry_p)
                    fee_d = calculate_futures_fee(entry_p, curr_c, rem_lots, symbol, is_entry_maker=True, is_exit_taker=True)
                    trade_gross_usd += gain_usd
                    trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                    rem_lots = 0
                    pos_closed = True

            elif side == "SHORT":
                # TP1 check
                if mode in ["split_50_50", "split_33_33_34"] and not tp1_hit:
                    target1_p = entry_p - (tp1_r * risk_dist)
                    if curr_l <= target1_p:
                        tp1_hit = True
                        lots_to_close = int(total_lots * 0.50) if mode == "split_50_50" else int(total_lots * 0.33)
                        lots_to_close = max(1, min(lots_to_close, rem_lots))
                        gain_usd = lots_to_close * lot_multiplier * (entry_p - target1_p)
                        fee_d = calculate_futures_fee(entry_p, target1_p, lots_to_close, symbol, is_entry_maker=True, is_exit_taker=True)
                        trade_gross_usd += gain_usd
                        trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                        rem_lots -= lots_to_close

                # TP2 check
                if mode == "split_33_33_34" and tp1_hit and not tp2_hit:
                    target2_p = entry_p - (tp2_r * risk_dist)
                    if curr_l <= target2_p:
                        tp2_hit = True
                        lots_to_close = int(total_lots * 0.33)
                        lots_to_close = max(1, min(lots_to_close, rem_lots))
                        gain_usd = lots_to_close * lot_multiplier * (entry_p - target2_p)
                        fee_d = calculate_futures_fee(entry_p, target2_p, lots_to_close, symbol, is_entry_maker=True, is_exit_taker=True)
                        trade_gross_usd += gain_usd
                        trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                        rem_lots -= lots_to_close

                # Final TP check
                final_tp = entry_p - (tp2_r * risk_dist if mode in ["baseline", "split_50_50", "step_trailing", "atr_trailing"] else entry_p - (tp3_r * risk_dist))
                if curr_l <= final_tp and rem_lots > 0:
                    gain_usd = rem_lots * lot_multiplier * (entry_p - final_tp)
                    fee_d = calculate_futures_fee(entry_p, final_tp, rem_lots, symbol, is_entry_maker=True, is_exit_taker=True)
                    trade_gross_usd += gain_usd
                    trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                    rem_lots = 0
                    pos_closed = True
                elif curr_h >= sl_p and rem_lots > 0:
                    loss_usd = rem_lots * lot_multiplier * (entry_p - sl_p)
                    fee_d = calculate_futures_fee(entry_p, sl_p, rem_lots, symbol, is_entry_maker=True, is_exit_taker=True)
                    trade_gross_usd += loss_usd
                    trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                    rem_lots = 0
                    pos_closed = True
                elif bars_held >= max_hold_bars and rem_lots > 0:
                    gain_usd = rem_lots * lot_multiplier * (entry_p - curr_c)
                    fee_d = calculate_futures_fee(entry_p, curr_c, rem_lots, symbol, is_entry_maker=True, is_exit_taker=True)
                    trade_gross_usd += gain_usd
                    trade_fee_usd += (fee_d["total_fee_with_gst_usd"])
                    rem_lots = 0
                    pos_closed = True

            if pos_closed or rem_lots == 0:
                net_usd = trade_gross_usd - trade_fee_usd
                net_inr = net_usd * 88.0
                capital += net_inr
                trades.append({
                    "win": net_inr > 0,
                    "gross_inr": trade_gross_usd * 88.0,
                    "fee_inr": trade_fee_usd * 88.0,
                    "net_inr": net_inr,
                    "capital": capital
                })
                in_pos = False
                continue

        swing_h = np.max(highs[i - lookback: i])
        swing_l = np.min(lows[i - lookback: i])
        curr_ema = ema200[i]

        # Bullish SFP
        lower_wick = (curr_c - curr_l) / c_range
        sweep_d = (swing_l - curr_l) / swing_l if swing_l > 0 else 0
        if (curr_l < swing_l and curr_c > swing_l and lower_wick >= 0.35 and 
            sweep_d >= 0.0006 and curr_v >= (vol_mult * v_avg) and curr_c > curr_ema):
            entry_p = swing_l * 1.0005
            sl_p = curr_l * 0.9990
            risk_dist = entry_p - sl_p
            if min_risk_dist_pct < (risk_dist / entry_p) < 0.018:
                risk_usd = (compounded_cap * risk_pct_per_trade) / 88.0
                raw_lots = int(risk_usd / (risk_dist * lot_multiplier))
                max_lots = int((compounded_cap * max_leverage) / (entry_p * lot_multiplier * 88.0))
                total_lots = max(2, min(raw_lots, max_lots))
                rem_lots = total_lots
                side = "LONG"
                in_pos = True
                bars_held = 0
                highest_p = curr_h
                lowest_p = curr_l
                tp1_hit = False
                tp2_hit = False
                trade_gross_usd = 0.0
                trade_fee_usd = 0.0
                continue

        # Bearish SFP
        upper_wick = (curr_h - curr_c) / c_range
        sweep_dh = (curr_h - swing_h) / swing_h if swing_h > 0 else 0
        if (curr_h > swing_h and curr_c < swing_h and upper_wick >= 0.35 and 
            sweep_dh >= 0.0006 and curr_v >= (vol_mult * v_avg) and curr_c < curr_ema):
            entry_p = swing_h * 0.9995
            sl_p = curr_h * 1.0010
            risk_dist = sl_p - entry_p
            if min_risk_dist_pct < (risk_dist / entry_p) < 0.018:
                risk_usd = (compounded_cap * risk_pct_per_trade) / 88.0
                raw_lots = int(risk_usd / (risk_dist * lot_multiplier))
                max_lots = int((compounded_cap * max_leverage) / (entry_p * lot_multiplier * 88.0))
                total_lots = max(2, min(raw_lots, max_lots))
                rem_lots = total_lots
                side = "SHORT"
                in_pos = True
                bars_held = 0
                highest_p = curr_h
                lowest_p = curr_l
                tp1_hit = False
                tp2_hit = False
                trade_gross_usd = 0.0
                trade_fee_usd = 0.0
                continue

    tdf = pd.DataFrame(trades)
    wins = tdf["win"].sum() if len(tdf) > 0 else 0
    total_trades = len(tdf)
    win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0
    tot_gross = tdf["gross_inr"].sum() if len(tdf) > 0 else 0.0
    tot_fee = tdf["fee_inr"].sum() if len(tdf) > 0 else 0.0
    tot_net = tdf["net_inr"].sum() if len(tdf) > 0 else 0.0
    roi = (tot_net / starting_capital_inr) * 100

    win_sum = tdf[tdf["net_inr"] > 0]["net_inr"].sum() if len(tdf) > 0 else 0.0
    loss_sum = abs(tdf[tdf["net_inr"] < 0]["net_inr"].sum()) if len(tdf) > 0 else 0.0
    pf = (win_sum / max(loss_sum, 1e-3)) if len(tdf) > 0 else 0.0

    return {
        "symbol": symbol,
        "strategy": f"Liquidity Sweep ({mode})",
        "window": f"{lookback}-bar / 15m intraday",
        "otm_pct": "N/A (Perp Futures)",
        "trades": total_trades,
        "wins": wins,
        "losses": total_trades - wins,
        "win_rate": win_rate,
        "gross_inr": tot_gross,
        "fee_inr": tot_fee,
        "net_inr": tot_net,
        "roi_pct": roi,
        "profit_factor": pf,
        "ending_capital_inr": capital,
        "avg_fee_per_trade_inr": tot_fee / max(total_trades, 1)
    }

# ------------------------------------------------------------------------------
# 4. MASTER RUNNER & COMPARATIVE AUDIT
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    print("Loading 180-day Delta candles...")
    btc_1h = fetch_candles("BTC", "1h", 180)
    eth_1h = fetch_candles("ETH", "1h", 180)
    btc_15m = fetch_candles("BTC", "15m", 180)
    eth_15m = fetch_candles("ETH", "15m", 180)

    print("\n" + "="*140)
    print("         DELTA EXCHANGE INDIA 180-DAY CANONICAL AUDIT: PRODUCTION OPTIMIZED ARCHITECTURE")
    print("="*140)

    # Weekly Compounding Simulation (Active Production Mode)
    print("\n>>> MODE 1: WEEKLY COMPOUNDING (Active Production Allocation: Rs 10,000 Capital Per Engine)")
    res_btc_ic = backtest_iron_condor(btc_1h, "BTC", start_hour=7, end_hour=17, otm_pct=0.030, weekly_compounding=True)
    res_eth_ic = backtest_iron_condor(eth_1h, "ETH", start_hour=9, end_hour=17, otm_pct=0.0325, weekly_compounding=True)
    res_btc_sw = backtest_liquidity_sweep(btc_15m, "BTC", lookback=36, vol_mult=1.2, mode="split_33_33_34", risk_pct_per_trade=0.06, max_leverage=12.0, min_risk_dist_pct=0.0025, max_hold_bars=28, weekly_compounding=True)
    res_eth_sw = backtest_liquidity_sweep(eth_15m, "ETH", lookback=36, vol_mult=1.2, mode="split_33_33_34", risk_pct_per_trade=0.06, max_leverage=12.0, min_risk_dist_pct=0.0025, max_hold_bars=28, weekly_compounding=True)

    all_res = [res_btc_ic, res_eth_ic, res_btc_sw, res_eth_sw]

    print(f"{'Strategy':<30} | {'Window':<22} | {'OTM/Setup':<10} | {'Trades':<6} | {'Win Rate':<8} | {'Gross PnL (INR)':<16} | {'Real Fees+GST':<14} | {'Net Take-Home':<16} | {'ROI':<8}")
    print("-" * 140)
    for r in all_res:
        name = f"{r['symbol']} {r['strategy']}"
        print(f"{name:<30} | {r['window']:<22} | {r['otm_pct']:<10} | {r['trades']:<6} | {r['win_rate']:<7.1f}% | Rs {r['gross_inr']:<13,.2f} | Rs {r['fee_inr']:<11,.2f} | Rs {r['net_inr']:<13,.2f} | +{r['roi_pct']:<6.1f}%")

    tot_trades = sum(r["trades"] for r in all_res)
    tot_wins = sum(r["wins"] for r in all_res)
    tot_gross = sum(r["gross_inr"] for r in all_res)
    tot_fees = sum(r["fee_inr"] for r in all_res)
    tot_net = sum(r["net_inr"] for r in all_res)
    tot_cap = 40000.0 + tot_net

    print("-" * 140)
    print(f"{'PORTFOLIO COMPOUNDED TOTAL':<30} | {'All 4 Engines Combined':<22} | {'Optimal':<10} | {tot_trades:<6} | {(tot_wins/tot_trades)*100:<7.1f}% | Rs {tot_gross:<13,.2f} | Rs {tot_fees:<11,.2f} | Rs {tot_net:<13,.2f} | +{(tot_net/40000)*100:<6.1f}%")
    print("-" * 140)
    print(f"Total Take-Home Net Profit    : Rs {tot_net:,.2f} on Rs 40,000 Portfolio")
    print(f"Total Exchange Commission + GST: Rs {tot_fees:,.2f} ({(tot_fees/tot_gross)*100:.2f}% of Gross Profit)")
    print(f"Ending Total Portfolio Balance: Rs {tot_cap:,.2f}")

    # Flat Fixed Capital Simulation (No Compounding - Rs 10,000 Fixed Base)
    print("\n>>> MODE 2: FIXED CAPITAL BASELINE (No Compounding: Exactly Rs 10,000 Fixed Per Engine)")
    res_btc_ic_flat = backtest_iron_condor(btc_1h, "BTC", start_hour=7, end_hour=17, otm_pct=0.030, weekly_compounding=False)
    res_eth_ic_flat = backtest_iron_condor(eth_1h, "ETH", start_hour=9, end_hour=17, otm_pct=0.0325, weekly_compounding=False)
    res_btc_sw_flat = backtest_liquidity_sweep(btc_15m, "BTC", lookback=36, vol_mult=1.2, mode="split_33_33_34", risk_pct_per_trade=0.06, max_leverage=12.0, min_risk_dist_pct=0.0025, max_hold_bars=28, weekly_compounding=False)
    res_eth_sw_flat = backtest_liquidity_sweep(eth_15m, "ETH", lookback=36, vol_mult=1.2, mode="split_33_33_34", risk_pct_per_trade=0.06, max_leverage=12.0, min_risk_dist_pct=0.0025, max_hold_bars=28, weekly_compounding=False)

    all_res_flat = [res_btc_ic_flat, res_eth_ic_flat, res_btc_sw_flat, res_eth_sw_flat]

    print(f"{'Strategy':<30} | {'Window':<22} | {'OTM/Setup':<10} | {'Trades':<6} | {'Win Rate':<8} | {'Gross PnL (INR)':<16} | {'Real Fees+GST':<14} | {'Net Take-Home':<16} | {'ROI':<8}")
    print("-" * 140)
    for r in all_res_flat:
        name = f"{r['symbol']} {r['strategy']}"
        print(f"{name:<30} | {r['window']:<22} | {r['otm_pct']:<10} | {r['trades']:<6} | {r['win_rate']:<7.1f}% | Rs {r['gross_inr']:<13,.2f} | Rs {r['fee_inr']:<11,.2f} | Rs {r['net_inr']:<13,.2f} | +{r['roi_pct']:<6.1f}%")

    tot_trades_flat = sum(r["trades"] for r in all_res_flat)
    tot_wins_flat = sum(r["wins"] for r in all_res_flat)
    tot_gross_flat = sum(r["gross_inr"] for r in all_res_flat)
    tot_fees_flat = sum(r["fee_inr"] for r in all_res_flat)
    tot_net_flat = sum(r["net_inr"] for r in all_res_flat)
    tot_cap_flat = 40000.0 + tot_net_flat

    print("-" * 140)
    print(f"{'PORTFOLIO FIXED TOTAL':<30} | {'All 4 Engines Combined':<22} | {'Optimal':<10} | {tot_trades_flat:<6} | {(tot_wins_flat/tot_trades_flat)*100:<7.1f}% | Rs {tot_gross_flat:<13,.2f} | Rs {tot_fees_flat:<11,.2f} | Rs {tot_net_flat:<13,.2f} | +{(tot_net_flat/40000)*100:<6.1f}%")
    print("=" * 140)
    print(f"Total Take-Home Net Profit (Fixed Base): Rs {tot_net_flat:,.2f} on Rs 40,000 Portfolio")
    print(f"Total Exchange Commission + GST         : Rs {tot_fees_flat:,.2f} ({(tot_fees_flat/tot_gross_flat)*100:.2f}% of Gross Profit)")
    print(f"Ending Portfolio Balance (No Reinvest) : Rs {tot_cap_flat:,.2f}")
    print("=" * 140)
