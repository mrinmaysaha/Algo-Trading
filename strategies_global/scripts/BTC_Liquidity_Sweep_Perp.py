#!/usr/bin/env python3
"""
================================================================================
OPENALGO PRODUCTION STRATEGY: BTC LIQUIDITY SWEEP (SFP) PERPETUAL FUTURES
================================================================================
Target Symbol   : BTCUSDFUT (Delta Exchange: BTCUSD Perpetual)
Exchange        : CRYPTO
Product Type    : NRML (Perpetual Futures)
Timeframe       : 15-Minute Execution
Strategy Type   : Institutional Liquidity Sweep / Swing Failure Pattern (SFP)
Holding Duration: Intraday (15 mins to 4 hours max, never multi-day)
Risk-to-Reward  : 1:3 RR (Take Profit = 3x Risk)
Leverage        : 10x - 15x
Instance Route  : Port 5001 (openalgo-global container)
================================================================================
"""

import os
import sys
import time
import json
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, Tuple

import requests
import numpy as np
import pandas as pd

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

IST_TZ = timezone(timedelta(hours=5, minutes=30))

def get_current_ist_datetime() -> datetime:
    return datetime.now(IST_TZ)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BTC_Liquidity_Sweep")

# ------------------------------------------------------------------------------
# 1. PARAMETERS & CONFIGURATION
# ------------------------------------------------------------------------------
STRATEGY_NAME = "BTC_Liquidity_Sweep"
SYMBOL = "BTCUSDFUT"
DELTA_SYMBOL = "BTCUSD"
EXCHANGE = "CRYPTO"
PRODUCT = "NRML"
TIMEFRAME = "15m"

SWING_LOOKBACK_BARS = int(os.getenv("BTC_SWING_LOOKBACK", "36"))     # 36 bars = 9 hours rolling swing (Filters chop)
MIN_REJECTION_WICK_RATIO = float(os.getenv("BTC_WICK_RATIO", "0.35")) # 35% wick rejection minimum
MAX_LEVERAGE = float(os.getenv("BTC_MAX_LEVERAGE", "12.0"))           # Strict 12x leverage cap to eliminate high brokerage
MIN_RISK_DIST_PCT = float(os.getenv("BTC_MIN_RISK_DIST", "0.0025"))   # 0.25% minimum stop distance to reject micro-wicks
MAX_HOLD_BARS = int(os.getenv("BTC_MAX_HOLD_BARS", "28"))             # 28 bars = 7 hours max intraday hold
CAPITAL_BASE_INR = float(os.getenv("CAPITAL_BASE_INR", os.getenv("BTC_FUT_CAPITAL_INR", "10000.0")))
USD_INR_RATE = float(os.getenv("USD_INR_RATE", "88.0"))
RISK_PER_TRADE_PCT = float(os.getenv("BTC_RISK_PER_TRADE", "0.06"))   # Risk 6% of capital per trade with 12x cap

def get_state_file_path() -> Path:
    base_dir = Path(__file__).resolve().parent.parent / "data"
    base_dir.mkdir(parents=True, exist_ok=True)
    today_str = get_current_ist_datetime().strftime("%Y%m%d")
    return base_dir / f"btc_liquidity_sweep_{today_str}.json"

def resolve_crypto_host_and_key() -> Tuple[str, str]:
    default_host = "http://127.0.0.1:5000" if os.path.exists("/.dockerenv") else "http://127.0.0.1:5001"
    host = os.getenv("OPENALGO_HOST_CRYPTO") or os.getenv("OPENALGO_HOST", default_host)
    api_key = (
        os.getenv("OPENALGO_API_KEY_CRYPTO")
        or os.getenv("OPENALGO_API_KEY")
        or "56609a7a820eaadf566b44babb61609fa55c25100996723f31a0b048f0c86721"
    )
    return host.rstrip("/"), api_key


class BTCLiquiditySweep:
    def __init__(self, host: str, api_key: str, capital_inr: float = CAPITAL_BASE_INR, dry_run: bool = False):
        self.host = host
        self.api_key = api_key
        self.capital_inr = capital_inr
        self.dry_run = dry_run

        self.in_position = False
        self.position_side = None
        self.entry_price = 0.0
        self.stop_loss = 0.0
        self.position_qty = 0
        self.rem_qty = 0
        self.entry_bar_time = None
        self.bars_held = 0

        # Multi-Target Milestone Ratchet State
        self.target1_p = 0.0
        self.target2_p = 0.0
        self.target3_p = 0.0
        self.tp1_hit = False
        self.tp2_hit = False

        self._load_state()

    def _load_state(self):
        state_file = get_state_file_path()
        if not state_file.exists():
            return
        try:
            with open(state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            self.in_position = data.get("in_position", False)
            self.position_side = data.get("position_side")
            self.entry_price = float(data.get("entry_price", 0.0))
            self.stop_loss = float(data.get("stop_loss", 0.0))
            self.position_qty = int(data.get("position_qty", 0))
            self.rem_qty = int(data.get("rem_qty", self.position_qty))
            self.target1_p = float(data.get("target1_p", 0.0))
            self.target2_p = float(data.get("target2_p", 0.0))
            self.target3_p = float(data.get("target3_p", 0.0))
            self.tp1_hit = data.get("tp1_hit", False)
            self.tp2_hit = data.get("tp2_hit", False)
            self.entry_bar_time = data.get("entry_bar_time")
            self.bars_held = int(data.get("bars_held", 0))
            if self.in_position:
                logger.info(f"[STATE RESTORED] Active {self.position_side} {self.rem_qty}/{self.position_qty} lots @ ${self.entry_price:.2f} | SL=${self.stop_loss:.2f} | TP1=${self.target1_p:.2f} | TP2=${self.target2_p:.2f} | TP3=${self.target3_p:.2f}")
        except Exception as e:
            logger.error(f"[STATE LOAD ERROR] {e}")

    def _save_state(self):
        state_file = get_state_file_path()
        try:
            data = {
                "date": get_current_ist_datetime().strftime("%Y-%m-%d"),
                "updated_at": get_current_ist_datetime().isoformat(),
                "in_position": self.in_position,
                "position_side": self.position_side,
                "entry_price": self.entry_price,
                "stop_loss": self.stop_loss,
                "position_qty": self.position_qty,
                "rem_qty": self.rem_qty,
                "target1_p": self.target1_p,
                "target2_p": self.target2_p,
                "target3_p": self.target3_p,
                "tp1_hit": self.tp1_hit,
                "tp2_hit": self.tp2_hit,
                "entry_bar_time": self.entry_bar_time,
                "bars_held": self.bars_held,
            }
            tmp = state_file.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
            os.replace(tmp, state_file)
        except Exception as e:
            logger.error(f"[STATE SAVE ERROR] {e}")

    def get_market_candles(self, limit: int = 100) -> Optional[pd.DataFrame]:
        try:
            url = f"{self.host}/api/v1/history"
            payload = {
                "apikey": self.api_key,
                "symbol": SYMBOL,
                "exchange": EXCHANGE,
                "interval": "15m",
                "start_date": (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d"),
                "end_date": datetime.now(timezone.utc).strftime("%Y-%m-%d")
            }
            res = requests.post(url, json=payload, timeout=6)
            if res.status_code == 200:
                d = res.json().get("data", [])
                if d and len(d) >= SWING_LOOKBACK_BARS + 5:
                    df = pd.DataFrame(d)
                    df.columns = [str(c).lower() for c in df.columns]
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        if col in df.columns:
                            df[col] = df[col].astype(float)
                    return df.tail(limit).reset_index(drop=True)
        except Exception:
            pass

        try:
            now = int(time.time())
            start = now - (5 * 86400)
            r = requests.get("https://api.india.delta.exchange/v2/history/candles",
                             params={"symbol": DELTA_SYMBOL, "resolution": "15m", "start": start, "end": now},
                             timeout=8)
            if r.status_code == 200:
                c = r.json().get("result", [])
                if c:
                    df = pd.DataFrame(c).drop_duplicates('time').sort_values('time').reset_index(drop=True)
                    for col in ['open', 'high', 'low', 'close', 'volume']:
                        if col in df.columns:
                            df[col] = df[col].astype(float)
                    return df.tail(limit).reset_index(drop=True)
        except Exception as e:
            logger.warning(f"Candle fetch fallback notice: {e}")
        return None

    def place_order(self, action: str, quantity: int, price: Optional[float] = None) -> Dict[str, Any]:
        if self.dry_run:
            p_str = f" @ ${price:.2f}" if price else " @ MARKET"
            logger.info(f"[DRY-RUN] Simulating {action} {quantity} lots of {SYMBOL}{p_str}")
            return {"status": "success", "orderid": "DRY_RUN_FUT"}
        try:
            url = f"{self.host}/api/v1/order"
            payload = {
                "apikey": self.api_key,
                "strategy": STRATEGY_NAME,
                "symbol": SYMBOL,
                "action": action,
                "exchange": EXCHANGE,
                "pricetype": "LIMIT" if price and price > 0 else "MARKET",
                "product": PRODUCT,
                "quantity": quantity,
            }
            if price and price > 0:
                payload["price"] = round(price, 2 if "ETH" in SYMBOL else 1)
            res = requests.post(url, json=payload, timeout=6)
            if res.status_code == 200:
                return res.json()
            return {"status": "error", "message": res.text}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def check_position_in_broker(self) -> Tuple[int, float]:
        try:
            url = f"{self.host}/api/v1/positionbook"
            res = requests.post(url, json={"apikey": self.api_key}, timeout=4)
            if res.status_code == 200:
                data = res.json().get("data", [])
                total_qty = 0.0
                total_pnl = 0.0
                found = False
                for p in data:
                    if p.get("symbol") in [SYMBOL, DELTA_SYMBOL]:
                        raw_qty = p.get("quantity") if p.get("quantity") is not None else p.get("netqty", 0)
                        qty = float(raw_qty or 0.0)
                        pnl = float(p.get("pnl", 0.0))
                        total_qty += qty
                        total_pnl += pnl
                        found = True
                if found:
                    return int(total_qty), total_pnl
        except Exception:
            pass
        return 0, 0.0

    def evaluate_signals(self, df: pd.DataFrame):
        highs = df['high'].values
        lows = df['low'].values
        closes = df['close'].values
        opens = df['open'].values
        n = len(df)

        curr_bar = n - 1
        curr_o = opens[curr_bar]
        curr_h = highs[curr_bar]
        curr_l = lows[curr_bar]
        curr_c = closes[curr_bar]
        candle_range = max(curr_h - curr_l, 1e-5)

        ema200_series = pd.Series(closes).ewm(span=200, adjust=False).mean()
        curr_ema = float(ema200_series.iloc[curr_bar])

        lookback_slice_high = highs[curr_bar - SWING_LOOKBACK_BARS: curr_bar]
        lookback_slice_low = lows[curr_bar - SWING_LOOKBACK_BARS: curr_bar]
        swing_high = float(np.max(lookback_slice_high))
        swing_low = float(np.min(lookback_slice_low))

        # ======================================================================
        # ACTIVE POSITION MONITORING: 3-TARGET SCALING OUT & RATCHET STOPS
        # ======================================================================
        if self.in_position:
            self.bars_held += 1
            broker_qty, pnl = self.check_position_in_broker()
            risk_dist = abs(self.entry_price - self.stop_loss)

            if broker_qty == 0 and not self.dry_run and self.bars_held > 1:
                logger.info(f"🔄 [BROKER RECONCILIATION] Broker position is flat (0 contracts). Resetting internal position state.")
                self.in_position = False
                self.rem_qty = 0
                self.bars_held = 0
                self._save_state()
                return

            logger.info(f"[MONITORING] {self.position_side} {self.rem_qty}/{self.position_qty} lots | Spot=${curr_c:.2f} | MTM PnL=${pnl:.2f} | Hold={self.bars_held}/{MAX_HOLD_BARS} bars | SL=${self.stop_loss:.2f} | TP1=${self.target1_p:.2f} | TP2=${self.target2_p:.2f}")

            if self.position_side == "LONG":
                # 1. Milestone TP1 (+1.0R): Close 33% lots & Lock Stop Loss to Entry + fees
                if not self.tp1_hit and curr_h >= self.target1_p:
                    lots_to_close = max(1, min(int(self.position_qty * 0.33), self.rem_qty))
                    logger.info(f"🎯 [TP1 HIT (+1.0R)] Closing {lots_to_close} lots @ ${self.target1_p:.2f} | Locking Stop Loss to Breakeven!")
                    res = self.place_order("SELL", lots_to_close)
                    if res.get("status") == "success":
                        self.rem_qty -= lots_to_close
                        self.tp1_hit = True
                        self.stop_loss = self.entry_price * 1.0005
                        self._save_state()
                    else:
                        logger.error(f"❌ [TP1 ORDER FAILED] {res}. Will retry on next check.")

                # 2. Milestone TP2 (+2.0R): Close 33% lots & Ratchet Stop Loss to +1.0R locked profit
                if self.tp1_hit and not self.tp2_hit and curr_h >= self.target2_p:
                    lots_to_close = max(1, min(int(self.position_qty * 0.33), self.rem_qty))
                    logger.info(f"🎯 [TP2 HIT (+2.0R)] Closing {lots_to_close} lots @ ${self.target2_p:.2f} | Ratcheting Stop Loss to +1.0R (${self.entry_price + (1.0 * risk_dist):.2f})!")
                    res = self.place_order("SELL", lots_to_close)
                    if res.get("status") == "success":
                        self.rem_qty -= lots_to_close
                        self.tp2_hit = True
                        self.stop_loss = self.entry_price + (1.0 * risk_dist)
                        self._save_state()
                    else:
                        logger.error(f"❌ [TP2 ORDER FAILED] {res}. Will retry on next check.")

                # 3. Final TP3 (+3.5R): Close remaining runner lots
                if curr_h >= self.target3_p and self.rem_qty > 0:
                    logger.info(f"🚀 [TP3 RUNNER HIT (+3.5R)] Closing remaining {self.rem_qty} lots @ ${self.target3_p:.2f}!")
                    res = self.place_order("SELL", self.rem_qty)
                    if res.get("status") == "success":
                        self.in_position = False
                        self.rem_qty = 0
                        self.bars_held = 0
                        self._save_state()
                        return
                    else:
                        logger.error(f"❌ [TP3 ORDER FAILED] {res}. Will retry on next bar.")

                # 4. Stop Loss / Ratchet Stop hit
                elif curr_l <= self.stop_loss and self.rem_qty > 0:
                    logger.info(f"🛡️ [STOP HIT] Price (${curr_l:.2f}) breached SL (${self.stop_loss:.2f}). Exiting {self.rem_qty} lots.")
                    res = self.place_order("SELL", self.rem_qty)
                    if res.get("status") == "success":
                        self.in_position = False
                        self.rem_qty = 0
                        self.bars_held = 0
                        self._save_state()
                        return
                    else:
                        logger.error(f"❌ [STOP LOSS ORDER FAILED] {res}. Will retry on next bar.")

                # 5. Maximum Time Stop (28 bars = 7 hours)
                elif self.bars_held >= MAX_HOLD_BARS and self.rem_qty > 0:
                    logger.info(f"⏰ [TIME EXIT] Held {self.bars_held} bars. Market closing remaining {self.rem_qty} lots.")
                    res = self.place_order("SELL", self.rem_qty)
                    if res.get("status") == "success":
                        self.in_position = False
                        self.rem_qty = 0
                        self.bars_held = 0
                        self._save_state()
                        return
                    else:
                        logger.error(f"❌ [TIME EXIT ORDER FAILED] {res}. Will retry on next bar.")

            elif self.position_side == "SHORT":
                # 1. Milestone TP1 (+1.0R): Close 33% lots & Lock Stop Loss to Entry + fees
                if not self.tp1_hit and curr_l <= self.target1_p:
                    lots_to_close = max(1, min(int(self.position_qty * 0.33), self.rem_qty))
                    logger.info(f"🎯 [TP1 HIT (+1.0R)] Closing {lots_to_close} lots @ ${self.target1_p:.2f} | Locking Stop Loss to Breakeven!")
                    res = self.place_order("BUY", lots_to_close)
                    if res.get("status") == "success":
                        self.rem_qty -= lots_to_close
                        self.tp1_hit = True
                        self.stop_loss = self.entry_price * 0.9995
                        self._save_state()
                    else:
                        logger.error(f"❌ [TP1 ORDER FAILED] {res}. Will retry on next check.")

                # 2. Milestone TP2 (+2.0R): Close 33% lots & Ratchet Stop Loss to +1.0R locked profit
                if self.tp1_hit and not self.tp2_hit and curr_l <= self.target2_p:
                    lots_to_close = max(1, min(int(self.position_qty * 0.33), self.rem_qty))
                    logger.info(f"🎯 [TP2 HIT (+2.0R)] Closing {lots_to_close} lots @ ${self.target2_p:.2f} | Ratcheting Stop Loss to +1.0R (${self.entry_price - (1.0 * risk_dist):.2f})!")
                    res = self.place_order("BUY", lots_to_close)
                    if res.get("status") == "success":
                        self.rem_qty -= lots_to_close
                        self.tp2_hit = True
                        self.stop_loss = self.entry_price - (1.0 * risk_dist)
                        self._save_state()
                    else:
                        logger.error(f"❌ [TP2 ORDER FAILED] {res}. Will retry on next check.")

                # 3. Final TP3 (+3.5R): Close remaining runner lots
                if curr_l <= self.target3_p and self.rem_qty > 0:
                    logger.info(f"🚀 [TP3 RUNNER HIT (+3.5R)] Closing remaining {self.rem_qty} lots @ ${self.target3_p:.2f}!")
                    res = self.place_order("BUY", self.rem_qty)
                    if res.get("status") == "success":
                        self.in_position = False
                        self.rem_qty = 0
                        self.bars_held = 0
                        self._save_state()
                        return
                    else:
                        logger.error(f"❌ [TP3 ORDER FAILED] {res}. Will retry on next bar.")

                # 4. Stop Loss / Ratchet Stop hit
                elif curr_h >= self.stop_loss and self.rem_qty > 0:
                    logger.info(f"🛡️ [STOP HIT] Price (${curr_h:.2f}) breached SL (${self.stop_loss:.2f}). Exiting {self.rem_qty} lots.")
                    res = self.place_order("BUY", self.rem_qty)
                    if res.get("status") == "success":
                        self.in_position = False
                        self.rem_qty = 0
                        self.bars_held = 0
                        self._save_state()
                        return
                    else:
                        logger.error(f"❌ [STOP LOSS ORDER FAILED] {res}. Will retry on next bar.")

                # 5. Maximum Time Stop (28 bars = 7 hours)
                elif self.bars_held >= MAX_HOLD_BARS and self.rem_qty > 0:
                    logger.info(f"⏰ [TIME EXIT] Held {self.bars_held} bars. Market closing remaining {self.rem_qty} lots.")
                    res = self.place_order("BUY", self.rem_qty)
                    if res.get("status") == "success":
                        self.in_position = False
                        self.rem_qty = 0
                        self.bars_held = 0
                        self._save_state()
                        return
                    else:
                        logger.error(f"❌ [TIME EXIT ORDER FAILED] {res}. Will retry on next bar.")

            return

        # ======================================================================
        # SCANNING FOR NEW LIQUIDITY SWEEP SETUPS WHEN FLAT
        # ======================================================================
        regime = "BULLISH" if curr_c > curr_ema else "BEARISH"
        logger.info(f"[HEARTBEAT] Spot=${curr_c:.2f} | 200 EMA=${curr_ema:.2f} [{regime}] | 9h Swing High=${swing_high:.2f} | Swing Low=${swing_low:.2f}")

        capital_usd = self.capital_inr / USD_INR_RATE
        try:
            f_resp = requests.post(f"{self.host}/api/v1/funds", json={"apikey": self.api_key}, timeout=3)
            if f_resp.status_code == 200:
                live_cash_inr = float(f_resp.json().get("data", {}).get("availablecash", 0.0))
                if live_cash_inr >= 5000000:
                    capital_usd = self.capital_inr / USD_INR_RATE
                    logger.info(f"[SANDBOX FUNDS] Sandbox mode detected (1 Cr). Using test capital: Rs {self.capital_inr:,.2f} (${capital_usd:.2f} USD)")
                elif live_cash_inr > 0:
                    capital_usd = live_cash_inr / USD_INR_RATE
                    logger.info(f"[LIVE FUNDS] Live Cash: Rs {live_cash_inr:,.2f} (${capital_usd:.2f} USD)")
        except Exception:
            pass

        vols = df['volume'].values
        curr_v = vols[curr_bar]
        v_avg = float(pd.Series(vols).rolling(20).mean().iloc[curr_bar])

        # 1. Bullish Liquidity Sweep (Sweeping Key Swing Low + Volume + 12x Leverage Cap)
        lower_wick_ratio = (curr_c - curr_l) / candle_range
        sweep_depth_pct = (swing_low - curr_l) / swing_low if swing_low > 0 else 0
        if (curr_l < swing_low and curr_c > swing_low and 
            lower_wick_ratio >= MIN_REJECTION_WICK_RATIO and curr_c >= curr_o and 
            curr_c > curr_ema and sweep_depth_pct >= 0.0006 and curr_v >= (1.2 * v_avg)):

            entry_price = swing_low * 1.0005 # Limit order at swing touch for Maker fill
            stop_loss = curr_l * 0.9990
            risk_dist = entry_price - stop_loss
            risk_pct = risk_dist / entry_price
            if MIN_RISK_DIST_PCT < risk_pct < 0.018:
                risk_usd = capital_usd * RISK_PER_TRADE_PCT
                loss_per_contract = risk_dist * 0.001
                raw_lots = int(risk_usd / loss_per_contract)
                # STRICT 12x LEVERAGE CAP:
                max_lots = int((capital_usd * MAX_LEVERAGE) / (entry_price * 0.001))
                lots = max(2, min(raw_lots, max_lots))

                target1_p = entry_price + (1.0 * risk_dist)
                target2_p = entry_price + (2.0 * risk_dist)
                target3_p = entry_price + (3.5 * risk_dist)

                logger.info("=" * 80)
                logger.info(f"🚀 INSTITUTIONAL BULLISH SFP DETECTED (LIMIT MAKER ORDER)!")
                logger.info(f"  • Swept Swing Low : ${swing_low:.2f} (Candle Low: ${curr_l:.2f} | Depth: {sweep_depth_pct*100:.2f}%)")
                logger.info(f"  • Volume Confirm  : {curr_v:.1f} vs Avg {v_avg:.1f} ({curr_v/max(v_avg,1):.2f}x)")
                logger.info(f"  • Macro Trend     : Bullish (Close ${curr_c:.2f} > 200 EMA ${curr_ema:.2f})")
                logger.info(f"  • Entry (Limit)   : ${entry_price:.2f}")
                logger.info(f"  • Stop Loss       : ${stop_loss:.2f} (-{risk_pct*100:.2f}%)")
                logger.info(f"  • Targets         : TP1=${target1_p:.2f} (1R) | TP2=${target2_p:.2f} (2R) | TP3=${target3_p:.2f} (3.5R)")
                logger.info(f"  • Position Sizing : {lots} lots (Capped at {MAX_LEVERAGE}x | Risk: ${risk_usd:.2f})")
                logger.info("=" * 80)

                res = self.place_order("BUY", lots, price=entry_price)
                if res.get("status") == "success":
                    self.in_position = True
                    self.position_side = "LONG"
                    self.entry_price = entry_price
                    self.stop_loss = stop_loss
                    self.position_qty = lots
                    self.rem_qty = lots
                    self.target1_p = target1_p
                    self.target2_p = target2_p
                    self.target3_p = target3_p
                    self.tp1_hit = False
                    self.tp2_hit = False
                    self.bars_held = 0
                    self._save_state()

        # 2. Bearish Liquidity Sweep (Sweeping Key Swing High + Volume + 12x Leverage Cap)
        upper_wick_ratio = (curr_h - curr_c) / candle_range
        sweep_depth_high_pct = (curr_h - swing_high) / swing_high if swing_high > 0 else 0
        if (curr_h > swing_high and curr_c < swing_high and 
            upper_wick_ratio >= MIN_REJECTION_WICK_RATIO and curr_c <= curr_o and 
            curr_c < curr_ema and sweep_depth_high_pct >= 0.0006 and curr_v >= (1.2 * v_avg)):

            entry_price = swing_high * 0.9995 # Limit order at swing touch for Maker fill
            stop_loss = curr_h * 1.0010
            risk_dist = stop_loss - entry_price
            risk_pct = risk_dist / entry_price
            if MIN_RISK_DIST_PCT < risk_pct < 0.018:
                risk_usd = capital_usd * RISK_PER_TRADE_PCT
                loss_per_contract = risk_dist * 0.001
                raw_lots = int(risk_usd / loss_per_contract)
                # STRICT 12x LEVERAGE CAP:
                max_lots = int((capital_usd * MAX_LEVERAGE) / (entry_price * 0.001))
                lots = max(2, min(raw_lots, max_lots))

                target1_p = entry_price - (1.0 * risk_dist)
                target2_p = entry_price - (2.0 * risk_dist)
                target3_p = entry_price - (3.5 * risk_dist)

                logger.info("=" * 80)
                logger.info(f"🔻 INSTITUTIONAL BEARISH SFP DETECTED (LIMIT MAKER ORDER)!")
                logger.info(f"  • Swept Swing High: ${swing_high:.2f} (Candle High: ${curr_h:.2f} | Depth: {sweep_depth_high_pct*100:.2f}%)")
                logger.info(f"  • Volume Confirm  : {curr_v:.1f} vs Avg {v_avg:.1f} ({curr_v/max(v_avg,1):.2f}x)")
                logger.info(f"  • Macro Trend     : Bearish (Close ${curr_c:.2f} < 200 EMA ${curr_ema:.2f})")
                logger.info(f"  • Entry (Limit)   : ${entry_price:.2f}")
                logger.info(f"  • Stop Loss       : ${stop_loss:.2f} (+{risk_pct*100:.2f}%)")
                logger.info(f"  • Targets         : TP1=${target1_p:.2f} (1R) | TP2=${target2_p:.2f} (2R) | TP3=${target3_p:.2f} (3.5R)")
                logger.info(f"  • Position Sizing : {lots} lots (Capped at {MAX_LEVERAGE}x | Risk: ${risk_usd:.2f})")
                logger.info("=" * 80)

                res = self.place_order("SELL", lots, price=entry_price)
                if res.get("status") == "success":
                    self.in_position = True
                    self.position_side = "SHORT"
                    self.entry_price = entry_price
                    self.stop_loss = stop_loss
                    self.position_qty = lots
                    self.rem_qty = lots
                    self.target1_p = target1_p
                    self.target2_p = target2_p
                    self.target3_p = target3_p
                    self.tp1_hit = False
                    self.tp2_hit = False
                    self.bars_held = 0
                    self._save_state()

    def run_loop(self):
        logger.info(f"Starting {STRATEGY_NAME} Monitoring Loop on 15m Candles...")
        while True:
            try:
                df = self.get_market_candles(limit=80)
                if df is not None and len(df) >= SWING_LOOKBACK_BARS + 5:
                    self.evaluate_signals(df)
                else:
                    logger.warning("Insufficient candle data retrieved. Retrying next cycle...")
            except Exception as e:
                logger.error(f"Error in strategy evaluation: {e}")
            time.sleep(60)

    def run_test(self, place_test_order: bool = False):
        logger.info("🧪 RUNNING ONE-SHOT TEST VALIDATION...")
        df = self.get_market_candles(limit=80)
        if df is not None and len(df) >= SWING_LOOKBACK_BARS + 5:
            self.evaluate_signals(df)
            logger.info("✅ Candle analysis and sweep evaluation validated successfully.")
        else:
            logger.error("Failed to fetch sufficient candles for test.")

        if place_test_order:
            logger.info("🧪 Testing one-shot live order execution and immediate cancellation/square-off...")
            curr_c = float(df.iloc[-1]["close"]) if df is not None and len(df) > 0 else 60000.0
            test_entry = round(curr_c * 0.90, 1) # Limit buy 10% below market
            res = self.place_order("BUY", 2, price=test_entry)
            logger.info(f"  Test Limit Maker Order result: {res}")
            time.sleep(2)
            broker_qty, pnl = self.check_position_in_broker()
            if broker_qty != 0:
                sq_res = self.place_order("SELL" if broker_qty > 0 else "BUY", abs(broker_qty))
                logger.info(f"  Test position square-off result: {sq_res}")
            logger.info("✅ One-shot order execution test complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BTC Liquidity Sweep Perpetual Strategy")
    parser.add_argument("--capital", type=float, default=CAPITAL_BASE_INR, help="Base capital in INR (default: 10000.0)")
    parser.add_argument("--test", action="store_true", help="Run one-shot signal analysis check")
    parser.add_argument("--test-order", action="store_true", help="Execute one-shot test order and immediate square-off")
    parser.add_argument("--dry-run", action="store_true", help="Run in dry run simulation mode")
    args = parser.parse_args()

    host, api_key = resolve_crypto_host_and_key()
    strategy = BTCLiquiditySweep(host=host, api_key=api_key, capital_inr=args.capital, dry_run=args.dry_run)

    if args.test or args.test_order:
        strategy.run_test(place_test_order=args.test_order)
    else:
        strategy.run_loop()
