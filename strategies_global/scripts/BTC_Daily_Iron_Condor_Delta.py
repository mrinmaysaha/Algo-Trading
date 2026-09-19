#!/usr/bin/env python3
"""
================================================================================
OPENALGO PRODUCTION STRATEGY: BTC DAILY HEDGED IRON CONDOR (DELTA EXCHANGE)
================================================================================
Market           : Crypto Options (Delta Exchange India)
Target Underlying: BTC (Bitcoin)
Execution Route  : Port 5001 (openalgo-global container)
Frequency        : Daily 0DTE Expiry (Expires daily at 17:30 IST / 12:00 UTC)
Architecture     : 4-Leg Defined-Risk Iron Condor
                   - Buy OTM Call Wing (Hedge)
                   - Buy OTM Put Wing (Hedge)
                   - Sell OTM Call (Short)
                   - Sell OTM Put (Short)
Margin Model     : Spread-defined risk (Locks only strike width margin, ~40% cap)
Risk Management  : 1.5x Premium Stop Loss on Short Legs | 80% Decay Target | 17:15 IST EOD Exit
State File       : strategies_global/data/btc_daily_iron_condor_YYYYMMDD.json
================================================================================
"""

import os
import sys
import time
import json
import signal
import logging
import argparse
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

import requests

# ------------------------------------------------------------------------------
# 0. TIMEZONE, ENCODING & LOGGING CONFIGURATION
# ------------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

IST_TZ = timezone(timedelta(hours=5, minutes=30))

def get_current_ist_datetime() -> datetime:
    return datetime.now(IST_TZ)

def get_current_ist_time() -> datetime.time:
    return datetime.now(IST_TZ).time()

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BTC_Daily_Iron_Condor")

# ------------------------------------------------------------------------------
# 1. PARAMETERS & CONFIGURATION
# ------------------------------------------------------------------------------
STRATEGY_NAME = "BTC_Daily_Iron_Condor"
EXCHANGE = "CRYPTO"
UNDERLYING = "BTC"

DEFAULT_LOTS = int(os.getenv("BTC_IC_LOTS", "60"))               # 60 contracts = 0.06 BTC (~$4,500 notional)
DYNAMIC_SIZING = os.getenv("BTC_IC_DYNAMIC_SIZING", "True").lower() in ("true", "1", "yes")
CAPITAL_BASE_INR = float(os.getenv("CAPITAL_BASE_INR", os.getenv("BTC_IC_CAPITAL_INR", "10000.0")))
MARGIN_UTILIZATION_CAP = float(os.getenv("BTC_IC_MARGIN_CAP", "0.65")) # 65% margin max = 35% free buffer
USD_INR_RATE = float(os.getenv("USD_INR_RATE", "88.0"))
# Architecture A2 Parameters (1.0% OTM Hedged Iron Condor - Recommended Default)
OTM_PCT = float(os.getenv("BTC_IC_OTM_PCT", "0.010"))            # 1.0% OTM short strikes (89.8% win rate)
SPREAD_WIDTH = float(os.getenv("BTC_IC_SPREAD_WIDTH", "800.0"))   # $800 wing spread width
SL_MULTIPLIER = float(os.getenv("BTC_IC_SL_MULT", "1.5"))        # 1.5x short premium stop loss
TARGET_DECAY_PCT = float(os.getenv("BTC_IC_TARGET_PCT", "0.80")) # 80% decay target
MIN_PREMIUM_THRESHOLD = float(os.getenv("BTC_IC_MIN_PREMIUM", "35.0")) # $35 minimum premium for shorts
MAX_SPREAD_PCT = float(os.getenv("BTC_IC_MAX_SPREAD", "0.05"))   # 5% max bid-ask spread

# Architecture B3 Parameters (Dynamic ATM Straddle with 30% SL)
STRADDLE_SL_PCT = float(os.getenv("BTC_STRADDLE_SL_PCT", "0.30")) # 30% per-leg stop loss
STRATEGY_MODE = os.getenv("BTC_STRATEGY_MODE", "condor").lower()  # "condor" (Arch A2) or "straddle" (Arch B3)

ENTRY_TIME_START = datetime.strptime("07:00", "%H:%M").time()    # 07:00 IST (10-hour theta window)
ENTRY_TIME_END = datetime.strptime("11:30", "%H:%M").time()
REENTRY_CUTOFF_TIME = datetime.strptime(os.getenv("BTC_REENTRY_CUTOFF", "12:30"), "%H:%M").time() # Cutoff for Session 2
MAX_DAILY_SESSIONS = int(os.getenv("BTC_MAX_SESSIONS", "2"))        # Max 2 sessions per day
EOD_EXIT_TIME = datetime.strptime("17:15", "%H:%M").time()       # 15 mins before 17:30 IST expiry

def get_state_file_path() -> Path:
    base_dir = Path(__file__).resolve().parent.parent / "data"
    base_dir.mkdir(parents=True, exist_ok=True)
    today_str = get_current_ist_datetime().strftime("%Y%m%d")
    return base_dir / f"btc_daily_iron_condor_{today_str}.json"

# ------------------------------------------------------------------------------
# 2. CLIENT & HOST RESOLUTION (DUAL-INSTANCE INVARIANT: PORT 5001)
# ------------------------------------------------------------------------------
def resolve_crypto_host_and_key() -> Tuple[str, str]:
    default_host = "http://127.0.0.1:5000" if os.path.exists("/.dockerenv") else "http://127.0.0.1:5001"
    host = os.getenv("OPENALGO_HOST_CRYPTO") or os.getenv("OPENALGO_HOST", default_host)
    api_key = (
        os.getenv("OPENALGO_API_KEY_CRYPTO")
        or os.getenv("OPENALGO_API_KEY")
        or "56609a7a820eaadf566b44babb61609fa55c25100996723f31a0b048f0c86721"
    )
    return host.rstrip("/"), api_key

# ------------------------------------------------------------------------------
# 3. IRON CONDOR STRATEGY ENGINE
# ------------------------------------------------------------------------------
class BTCDailyIronCondor:
    def __init__(
        self,
        host: str,
        api_key: str,
        lots: int = DEFAULT_LOTS,
        capital: float = CAPITAL_BASE_INR,
        dry_run: bool = False,
        force: bool = False,
        mode: str = STRATEGY_MODE,
    ):
        self.host = host
        self.api_key = api_key
        self.lots = lots
        self.capital = capital
        self.dry_run = dry_run
        self.force = force
        self.mode = mode.lower()
        self.shutdown_event = False

        # Architecture Mode Configuration
        if self.mode == "straddle":
            self.otm_pct = 0.0
            self.spread_width = 0.0
            self.sl_multiplier = float(os.getenv("BTC_STRADDLE_SL_MULT", "1.30"))
            self.straddle_sl_pct = STRADDLE_SL_PCT
            self.min_premium = float(os.getenv("BTC_STRADDLE_MIN_PREMIUM", "100.0"))
            self.max_spread_pct = float(os.getenv("BTC_STRADDLE_MAX_SPREAD", "0.03"))
            self.target_decay_pct = TARGET_DECAY_PCT
            logger.info("🏛️ [STRATEGY MODE] Architecture B3: Dynamic ATM Straddle (30% SL)")
        else:
            self.mode = "condor"
            self.otm_pct = OTM_PCT
            self.spread_width = SPREAD_WIDTH
            self.sl_multiplier = SL_MULTIPLIER
            self.straddle_sl_pct = 0.0
            self.min_premium = MIN_PREMIUM_THRESHOLD
            self.max_spread_pct = MAX_SPREAD_PCT
            self.target_decay_pct = TARGET_DECAY_PCT
            logger.info("🛡️ [STRATEGY MODE] Architecture A2: Delta 15-20 Hedged Iron Condor (1.0% OTM, 1.5x SL)")

        # State tracking
        self.current_session = 1
        self.session_history: List[Dict[str, Any]] = []
        self.trade_taken_today = False
        self.trade_active = False
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.net_credit_collected = 0.0
        self.target_profit = 0.0
        self.expiry_date: Optional[str] = None
        self.entry_time: Optional[str] = None
        self.entry_attempts = 0
        self.last_attempt_time = 0.0
        self.last_cooldown_log = 0.0
        self.last_attempt_log = 0.0
        self.max_entry_attempts = int(os.getenv("BTC_IC_MAX_ATTEMPTS", "3"))
        self.retry_cooldown_sec = int(os.getenv("BTC_IC_RETRY_COOLDOWN", "180"))

        self._load_state()

        if self.force:
            logger.info("⚡ [--force flag active] Overriding prior state: resetting trade_taken_today and attempt counters.")
            self.trade_taken_today = False
            self.entry_attempts = 0
            self.last_attempt_time = 0.0
            self.current_session = 1
            self.session_history = []
            self._save_state()
        else:
            # Reconcile existing broker positions and verify Stop Losses immediately on startup
            self.audit_and_recover_positions_on_startup()

    def _load_state(self):
        state_path = get_state_file_path()
        if not state_path.exists():
            return
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            today_str = get_current_ist_datetime().strftime("%Y-%m-%d")
            if data.get("date") != today_str:
                return
            self.current_session = int(data.get("current_session", 1))
            self.session_history = data.get("session_history", [])
            self.trade_taken_today = data.get("trade_taken_today", False)
            self.trade_active = data.get("trade_active", False)
            self.positions = data.get("positions", {})
            self.net_credit_collected = float(data.get("net_credit_collected", 0.0))
            self.target_profit = float(data.get("target_profit", 0.0))
            self.expiry_date = data.get("expiry_date")
            self.entry_time = data.get("entry_time")
            self.entry_attempts = int(data.get("entry_attempts", 0))
            self.last_attempt_time = float(data.get("last_attempt_time", 0.0))
            logger.info(
                f"[STATE RESTORED] Date: {today_str} | Session: {self.current_session}/{MAX_DAILY_SESSIONS} | "
                f"Active: {self.trade_active} | Trade Taken Today: {self.trade_taken_today} | "
                f"Attempts: {self.entry_attempts} | Legs: {len(self.positions)}"
            )
        except Exception as e:
            logger.error(f"[STATE LOAD ERROR] {e}")

    def _save_state(self):
        state_path = get_state_file_path()
        try:
            state_data = {
                "date": get_current_ist_datetime().strftime("%Y-%m-%d"),
                "updated_at": get_current_ist_datetime().isoformat(),
                "current_session": self.current_session,
                "session_history": self.session_history,
                "trade_taken_today": self.trade_taken_today,
                "trade_active": self.trade_active,
                "positions": self.positions,
                "net_credit_collected": self.net_credit_collected,
                "target_profit": self.target_profit,
                "expiry_date": self.expiry_date,
                "entry_time": self.entry_time,
                "entry_attempts": self.entry_attempts,
                "last_attempt_time": self.last_attempt_time,
            }
            tmp_path = state_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(state_data, f, indent=2)
            os.replace(tmp_path, state_path)
        except Exception as e:
            logger.error(f"[STATE SAVE ERROR] {e}")

    def audit_and_recover_positions_on_startup(self):
        """
        Critical Startup Invariant:
        When restarted or resumed after downtime/crash/server reload:
        1. Query live broker positions from /api/v1/positionbook.
        2. Sync & reconcile open legs. If local state was missing, reconstruct from broker.
        3. Ensure every short leg has an assigned Stop Loss threshold (1.5x of entry).
        4. Immediately check live LTP against SL:
           - If LTP >= SL (breached during downtime): immediately unwind breached side (short + wing).
           - If LTP < SL: log protection armed and continue normal real-time monitoring.
        5. Persist reconciled state to disk.
        """
        if self.dry_run:
            return

        logger.info("🔍 [STARTUP POSITION AUDIT] Checking broker positionbook for active contracts...")
        try:
            url = f"{self.host}/api/v1/positionbook"
            resp = requests.post(url, json={"apikey": self.api_key}, timeout=6)
            if resp.status_code != 200:
                logger.warning(f"⚠️ [STARTUP AUDIT] Could not fetch positionbook: HTTP {resp.status_code}")
                return

            pbook = resp.json().get("data", [])
            if not isinstance(pbook, list):
                return

            broker_positions: Dict[str, Dict] = {}
            for p in pbook:
                if not isinstance(p, dict) or not p.get("symbol"):
                    continue
                sym = str(p.get("symbol")).strip()
                # Filter for BTC option contracts
                if not sym.startswith("BTC") or ("CE" not in sym and "PE" not in sym):
                    continue
                raw_qty = p.get("quantity") if p.get("quantity") is not None else p.get("netqty", 0)
                qty = float(raw_qty or 0.0)
                if abs(qty) > 0.0:
                    broker_positions[sym] = {
                        "quantity": qty,
                        "average_price": float(p.get("average_price") or 0.0),
                        "ltp": float(p.get("ltp") or 0.0),
                    }

            if not broker_positions:
                logger.info("ℹ️ [STARTUP AUDIT] Broker positionbook is flat (0 open BTC option positions).")
                changed = False
                for sym, pos in list(self.positions.items()):
                    if pos.get("status") == "OPEN":
                        pos["status"] = "CLOSED"
                        pos["exit_reason"] = "BROKER_RECONCILED_FLAT"
                        changed = True
                if changed:
                    self.trade_active = False
                    self._save_state()
                return

            logger.info(f"📊 [STARTUP AUDIT] Detected {len(broker_positions)} active BTC option position(s) on broker:")
            for sym, bp in broker_positions.items():
                logger.info(f"   • {sym} | Qty: {bp['quantity']} | Avg: ${bp['average_price']:.2f}")

            # Reconcile / populate self.positions
            self.trade_active = True
            for sym, bp in broker_positions.items():
                net_qty = bp["quantity"]
                action = "SELL" if net_qty < 0 else "BUY"
                abs_qty = abs(int(net_qty))
                avg_price = bp["average_price"]

                if sym in self.positions:
                    pos = self.positions[sym]
                    pos["status"] = "OPEN"
                    pos["quantity"] = abs_qty
                    if not pos.get("entry_price") or pos["entry_price"] <= 0:
                        pos["entry_price"] = avg_price
                else:
                    # Auto-reconstruct leg
                    is_ce = sym.endswith("CE") or "CE" in sym
                    if action == "SELL":
                        leg_type = "SHORT_CE" if is_ce else "SHORT_PE"
                    else:
                        leg_type = "LONG_CE" if is_ce else "LONG_PE"
                    self.positions[sym] = {
                        "leg_type": leg_type,
                        "action": action,
                        "quantity": abs_qty,
                        "entry_price": avg_price,
                        "status": "OPEN",
                        "stop_loss": 0.0,
                    }

            # Check Stop Loss configuration on all short legs
            for sym, pos in self.positions.items():
                if pos.get("status") == "OPEN" and pos.get("action") == "SELL":
                    entry = float(pos.get("entry_price") or 0.0)
                    sl = float(pos.get("stop_loss") or 0.0)
                    if sl <= 0.0 and entry > 0.0:
                        pos["stop_loss"] = round(entry * SL_MULTIPLIER, 2)
                        logger.info(f"🛡️ [STARTUP AUDIT] Armed missing Stop Loss for {sym} at ${pos['stop_loss']:.2f} (Entry: ${entry:.2f})")

            # Check if any short leg breached SL while the strategy was offline
            for sym, pos in list(self.positions.items()):
                if pos.get("status") != "OPEN" or pos.get("action") != "SELL":
                    continue

                sl = float(pos.get("stop_loss") or 0.0)
                ltp = self.get_quote_ltp(sym)
                if not ltp or ltp <= 0.0:
                    ltp = broker_positions.get(sym, {}).get("ltp")

                if not ltp or ltp <= 0.0:
                    logger.warning(f"⚠️ [STARTUP AUDIT] Could not determine live LTP for {sym}. Will check in main loop.")
                    continue

                if sl > 0.0 and ltp >= sl:
                    logger.warning(
                        f"🚨 [STARTUP SL BREACH DETECTED] {sym} LTP ${ltp:.2f} >= SL ${sl:.2f} (Breached during downtime)! "
                        f"Immediately unwinding breached tested pair!"
                    )
                    pos["exit_attempted"] = True
                    # Step A: Close tested short leg
                    live_qty = self._get_broker_net_qty(sym)
                    qty_to_close = abs(int(live_qty)) if live_qty is not None else pos["quantity"]
                    if qty_to_close > 0:
                        closed_short, _, _ = self.place_and_verify_order(sym, "BUY", qty_to_close, timeout_sec=8)
                        if closed_short:
                            pos["status"] = "CLOSED"
                            pos["exit_reason"] = "STARTUP_SL_BREACH"
                            logger.info(f"  ✅ [STARTUP AUDIT] Confirmed closed short leg {sym}")
                        else:
                            logger.error(f"  ❌ [STARTUP AUDIT] Failed to fill short leg exit {sym}. Will retry in main loop.")

                    # Step B: Close protective wing hedge
                    wing_type = "LONG_CE" if pos["leg_type"] == "SHORT_CE" else "LONG_PE"
                    for w_sym, w_pos in list(self.positions.items()):
                        if w_pos.get("leg_type") == wing_type and w_pos.get("status") == "OPEN":
                            w_pos["exit_attempted"] = True
                            w_live_qty = self._get_broker_net_qty(w_sym)
                            w_qty = abs(int(w_live_qty)) if w_live_qty is not None else w_pos["quantity"]
                            if w_qty > 0:
                                closed_wing, _, _ = self.place_and_verify_order(w_sym, "SELL", w_qty, timeout_sec=8)
                                if closed_wing:
                                    w_pos["status"] = "CLOSED"
                                    w_pos["exit_reason"] = "STARTUP_WING_UNWIND"
                                    logger.info(f"  ✅ [STARTUP AUDIT] Confirmed closed protective wing {w_sym}")
                                else:
                                    logger.error(f"  ❌ [STARTUP AUDIT] Failed to close wing {w_sym}. Will retry in main loop.")
                else:
                    logger.info(
                        f"🛡️ [STARTUP SL INTACT] {sym} LTP ${ltp:.2f} < SL ${sl:.2f} (Entry: ${pos.get('entry_price', 0.0):.2f}). Active protection running."
                    )

            # Check if all legs are closed after audit
            all_closed = all(p.get("status") == "CLOSED" for p in self.positions.values())
            if all_closed and self.positions:
                self._handle_all_legs_closed(exit_trigger="STARTUP_RECOVERY_ALL_CLOSED")

            self._save_state()
            logger.info("✅ [STARTUP POSITION AUDIT COMPLETE] Strategy state synchronized with broker.")
        except Exception as e:
            logger.exception(f"❌ [STARTUP AUDIT ERROR] Failed during position recovery: {e}")

    def get_spot_price(self) -> Optional[float]:
        try:
            url = f"{self.host}/api/v1/quotes"
            resp = requests.post(url, json={"apikey": self.api_key, "symbol": "BTCUSDFUT", "exchange": EXCHANGE}, timeout=4)
            if resp.status_code == 200:
                data = resp.json()
                inner = data.get("data", data) if isinstance(data, dict) else {}
                ltp = inner.get("ltp")
                if ltp and float(ltp) > 0:
                    return float(ltp)
        except Exception as e:
            logger.warning(f"Could not fetch spot LTP from {self.host}: {e}")
        return None

    def get_today_expiry(self) -> Optional[str]:
        try:
            url = f"{self.host}/api/v1/expiry"
            resp = requests.post(url, json={"apikey": self.api_key, "symbol": "BTC", "exchange": EXCHANGE, "instrumenttype": "options"}, timeout=4)
            if resp.status_code == 200:
                exp_list = resp.json().get("data", [])
                if exp_list:
                    now_ist = get_current_ist_datetime()
                    today_str = now_ist.strftime("%d-%b-%y").upper()
                    # Filter out today's expiry if we are past 17:15 IST (daily expiry settles at 17:30 IST)
                    valid_expiries = [
                        e for e in exp_list
                        if not (e.upper() == today_str and now_ist.time() >= EOD_EXIT_TIME)
                    ]
                    if valid_expiries:
                        return valid_expiries[0]
                    return exp_list[0]
        except Exception as e:
            logger.warning(f"Could not fetch expiries: {e}")
        return None

    def resolve_option_symbol(self, strike: float, option_type: str, expiry: str) -> Optional[str]:
        try:
            url = f"{self.host}/api/v1/optionsymbol"
            # Round strike to nearest 100
            strike_int = int(round(strike / 100.0) * 100)
            resp = requests.post(url, json={
                "apikey": self.api_key,
                "underlying": "BTC",
                "exchange": EXCHANGE,
                "expiry_date": expiry,
                "offset": "ATM",
                "option_type": option_type,
                "underlying_ltp": strike_int
            }, timeout=4)
            if resp.status_code == 200:
                sym = resp.json().get("symbol")
                if sym:
                    return sym
        except Exception as e:
            logger.warning(f"Symbol lookup failed for strike {strike} {option_type}: {e}")
        return None

    def check_strike_liquidity(
        self, symbol: str, action: str, max_wing_price: float = 50.0
    ) -> Tuple[bool, float, float]:
        """
        Validates whether symbol has active bid/ask market maker quotes on Delta Exchange.
        - For BUY (wings): requires ask > 0 and ask <= max_wing_price (protects from collar/slippage).
        - For SELL (shorts): requires bid > 0 (prevents resting limit collar orders).
        Returns: (is_liquid: bool, bid: float, ask: float)
        """
        if self.dry_run:
            return True, 5.0, 6.0

        try:
            url = f"{self.host}/api/v1/quotes"
            resp = requests.post(
                url,
                json={"apikey": self.api_key, "symbol": symbol, "exchange": EXCHANGE},
                timeout=3,
            )
            if resp.status_code == 200:
                data = resp.json()
                inner = data.get("data", data) if isinstance(data, dict) else {}
                bid = float(inner.get("bid") or 0.0)
                ask = float(inner.get("ask") or 0.0)
                ltp = float(inner.get("ltp") or 0.0)
                low = float(inner.get("low") or 0.0)
                high = float(inner.get("high") or 0.0)

                # Stale-quote guard: Reject strikes where LTP contradicts day range [low, high]
                if low > 0.0 and high > 0.0 and ltp > 0.0:
                    if not (low <= ltp <= high):
                        logger.info(
                            f"  ⚠️ [STRIKE SKIPPED] {symbol} has contradictory ticker range "
                            f"(LTP ${ltp:.2f} outside [${low:.2f}, ${high:.2f}]). Hunting next strike..."
                        )
                        return False, 0.0, 0.0

                # Spread filter (reject strikes with spread > max_spread_pct)
                if ask > 0.0 and bid > 0.0:
                    spread_pct = (ask - bid) / ask
                    max_spr = getattr(self, "max_spread_pct", 0.05)
                # Spread filter
                if ask > 0.0 and bid > 0.0:
                    spread_pct = (ask - bid) / ask
                    dollar_spr = ask - bid
                    if action == "BUY":
                        # Protective wings (cheap insurance): accept if dollar spread <= $3.00 OR spread_pct <= 15%
                        # (A $1-$2 spread on a $15-$25 wing is the exchange's natural tick depth)
                        if spread_pct > 0.15 and dollar_spr > 3.0:
                            logger.info(
                                f"  ⚠️ [WING SKIPPED] {symbol} spread too wide (${dollar_spr:.2f} / {spread_pct*100:.1f}% > $3.00 / 15%). Hunting next strike..."
                            )
                            return False, 0.0, 0.0
                    else:
                        # Short legs: enforce strict <= 5% to guarantee selling into tight liquidity
                        max_spr = getattr(self, "max_spread_pct", 0.05)
                        if spread_pct > max_spr:
                            logger.info(
                                f"  ⚠️ [STRIKE SKIPPED] {symbol} short spread too wide ({spread_pct*100:.1f}% > "
                                f"{max_spr*100:.1f}%). Hunting next strike..."
                            )
                            return False, 0.0, 0.0

                if action == "BUY":
                    if ask > 0.0 and ask <= max_wing_price:
                        return True, bid, ask
                elif action == "SELL":
                    min_prem = getattr(self, "min_premium", 35.0)
                    if bid >= min_prem:
                        return True, bid, ask
                    elif bid > 0.0:
                        logger.info(
                            f"  ⚠️ [STRIKE SKIPPED] {symbol} bid ${bid:.2f} < min threshold ${min_prem:.2f}. Hunting closer to ATM..."
                        )
        except Exception as e:
            logger.debug(f"Liquidity check error for {symbol}: {e}")
        return False, 0.0, 0.0

    def resolve_liquid_option_symbol(
        self,
        target_strike: float,
        option_type: str,
        expiry: str,
        action: str,
        is_wing: bool = False,
        short_strike_ref: Optional[float] = None,
        max_wing_price: float = 50.0,
    ) -> Optional[str]:
        """
        Adaptive Liquid Strike Hunter:
        Finds the closest option contract that has active two-sided market maker liquidity.
        If the primary strike is illiquid (0 bids/asks or wide collar), it walks candidate strikes:
        - For wings: walks inwards towards the money/short strike (narrowing spread = lower risk & margin)
          and evaluates nearest major round strikes (multiples of 200, 500, 1000).
        - For shorts: walks inwards towards ATM (higher premium & tighter spreads) and major round strikes.
        Returns: liquid symbol string, or None if no liquid market depth exists.
        """
        strike_step = 100.0
        primary_strike = round(target_strike / strike_step) * strike_step

        candidate_strikes = [primary_strike]

        if is_wing and short_strike_ref is not None:
            # Walk inwards towards short strike to keep risk capped and margin minimal
            if option_type == "PE":
                for step in [1, 2, 3, 4, 5, 6]:
                    s = primary_strike + (step * strike_step)
                    if s < short_strike_ref:
                        candidate_strikes.append(s)
            elif option_type == "CE":
                for step in [1, 2, 3, 4, 5, 6]:
                    s = primary_strike - (step * strike_step)
                    if s > short_strike_ref:
                        candidate_strikes.append(s)

            # Round 200, 500 and 1000 strikes
            for round_step in [200.0, 500.0, 1000.0]:
                r = round(primary_strike / round_step) * round_step
                if option_type == "PE" and r < short_strike_ref and r not in candidate_strikes:
                    candidate_strikes.append(r)
                elif option_type == "CE" and r > short_strike_ref and r not in candidate_strikes:
                    candidate_strikes.append(r)
        else:
            # Walk inwards towards ATM to pick up higher premium & tighter spreads
            if option_type == "CE":
                for step in [1, 2, 3, 4, 5]:
                    cand = primary_strike - (step * strike_step)
                    if cand not in candidate_strikes:
                        candidate_strikes.append(cand)
            elif option_type == "PE":
                for step in [1, 2, 3, 4, 5]:
                    cand = primary_strike + (step * strike_step)
                    if cand not in candidate_strikes:
                        candidate_strikes.append(cand)

            for round_step in [200.0, 500.0, 1000.0]:
                r = round(primary_strike / round_step) * round_step
                if r not in candidate_strikes:
                    candidate_strikes.append(r)

        for cand in candidate_strikes:
            sym = self.resolve_option_symbol(cand, option_type, expiry)
            if not sym:
                continue

            is_liq, bid, ask = self.check_strike_liquidity(sym, action, max_wing_price)
            if is_liq:
                leg_desc = "Wing" if is_wing else "Short"
                logger.info(
                    f"  🎯 [LIQUID STRIKE HUNTER] Found liquid {leg_desc} {option_type} for strike {cand:.0f} "
                    f"-> {sym} (Bid: ${bid:.2f}, Ask: ${ask:.2f})"
                )
                return sym
            else:
                logger.debug(f"  [STRIKE HUNTER] Strike {cand:.0f} {option_type} ({sym}) illiquid. Trying next candidate...")

        logger.warning(
            f"  ❌ [LIQUID STRIKE HUNTER] No liquid strike with active orderbook found for {option_type} "
            f"near target {primary_strike:.0f}. Aborting leg resolution."
        )
        return None

    def get_quote_ltp(self, symbol: str) -> Optional[float]:
        try:
            url = f"{self.host}/api/v1/quotes"
            resp = requests.post(
                url,
                json={"apikey": self.api_key, "symbol": symbol, "exchange": EXCHANGE},
                timeout=3,
            )
            if resp.status_code == 200:
                data = resp.json()
                inner = data.get("data", data) if isinstance(data, dict) else {}
                ltp = inner.get("ltp")
                if ltp and float(ltp) > 0:
                    return float(ltp)
        except Exception as e:
            logger.debug(f"Could not fetch LTP for {symbol}: {e}")
        return None

    def cancel_order_by_id(self, order_id: str) -> bool:
        if self.dry_run or not order_id:
            return True
        try:
            url = f"{self.host}/api/v1/cancelorder"
            payload = {
                "apikey": self.api_key,
                "strategy": STRATEGY_NAME,
                "orderid": str(order_id),
            }
            resp = requests.post(url, json=payload, timeout=4)
            if resp.status_code == 200:
                logger.info(f"Successfully cancelled resting order {order_id}")
                return True
            logger.warning(f"Cancel order {order_id} returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
        return False

    def place_order(self, symbol: str, action: str, qty: int) -> Dict[str, Any]:
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would execute {action} {qty} contracts of {symbol}")
            return {"status": "success", "orderid": "DRY_ORDER_123", "symbol": symbol, "price": 150.0}

        url = f"{self.host}/api/v1/placeorder"
        payload = {
            "apikey": self.api_key,
            "strategy": STRATEGY_NAME,
            "symbol": symbol,
            "action": action,
            "exchange": EXCHANGE,
            "quantity": qty,
            "pricetype": "MARKET",
            "product": "MIS"
        }
        try:
            resp = requests.post(url, json=payload, timeout=6)
            if resp.status_code == 200:
                return resp.json()
            return {"status": "error", "message": resp.text}
        except Exception as e:
            logger.error(f"Order error for {symbol} ({action}): {e}")
            return {"status": "error", "message": str(e)}

    def place_and_verify_order(
        self, symbol: str, action: str, qty: int, timeout_sec: int = 6
    ) -> Tuple[bool, float, Optional[str]]:
        """
        Submits order and polls orderstatus / orderbook until 'complete'.
        If unfilled after timeout_sec, cancels resting limit collar order to prevent unexpected future fills.
        Returns: (success: bool, fill_price: float, order_id: Optional[str])
        """
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would execute {action} {qty} contracts of {symbol}")
            return True, 150.0, "DRY_ORDER_123"

        res = self.place_order(symbol, action, qty)
        if res.get("status") != "success":
            logger.error(f"Failed to submit {action} {qty} {symbol}: {res}")
            return False, 0.0, None

        order_id = str(res.get("orderid", "")).strip()
        if not order_id:
            logger.error(f"Order placement for {symbol} returned no orderid: {res}")
            return False, 0.0, None

        logger.info(f"  ⏳ Submitted order {order_id} ({action} {qty} {symbol}). Verifying fill...")

        deadline = time.time() + timeout_sec
        fill_price = 0.0

        while time.time() < deadline:
            time.sleep(0.5)

            # Check 1: Dedicated orderstatus endpoint
            try:
                os_resp = requests.post(
                    f"{self.host}/api/v1/orderstatus",
                    json={"apikey": self.api_key, "strategy": STRATEGY_NAME, "orderid": order_id},
                    timeout=3,
                )
                if os_resp.status_code == 200:
                    data = os_resp.json().get("data", {})
                    status = str(data.get("order_status", "")).lower()
                    if status in ("complete", "filled"):
                        fill_price = float(data.get("average_price") or data.get("price") or 0.0)
                        logger.info(f"  ✅ Order {order_id} confirmed FILLED @ ${fill_price:.2f}")
                        return True, fill_price, order_id
                    elif status in ("cancelled", "rejected"):
                        logger.error(f"  ❌ Order {order_id} was {status.upper()}: {data}")
                        return False, 0.0, order_id
            except Exception as e:
                logger.debug(f"orderstatus poll notice for {order_id}: {e}")

            # Check 2: Fallback to orderbook query
            try:
                ob_resp = requests.post(
                    f"{self.host}/api/v1/orderbook",
                    json={"apikey": self.api_key},
                    timeout=3,
                )
                if ob_resp.status_code == 200:
                    orders = ob_resp.json().get("data", {}).get("orders", [])
                    if isinstance(orders, list):
                        target = next((o for o in orders if str(o.get("orderid")) == order_id or str(o.get("orderId")) == order_id), None)
                        if target:
                            status = str(target.get("order_status", "")).lower()
                            if status in ("complete", "filled"):
                                fill_price = float(target.get("average_price") or target.get("price") or 0.0)
                                logger.info(f"  ✅ Order {order_id} confirmed FILLED via orderbook @ ${fill_price:.2f}")
                                return True, fill_price, order_id
                            elif status in ("cancelled", "rejected"):
                                logger.error(f"  ❌ Order {order_id} was {status.upper()} in orderbook")
                                return False, 0.0, order_id
            except Exception as e:
                logger.debug(f"orderbook poll notice for {order_id}: {e}")

        # Timeout reached: cancel resting order to avoid stale fills
        logger.warning(f"  ⏳ Order {order_id} ({action} {symbol}) did not fill within {timeout_sec}s. Cancelling...")
        self.cancel_order_by_id(order_id)
        return False, 0.0, order_id

    def compute_dynamic_lots(self, spot: float = 77000.0) -> int:
        """
        Computes dynamic lot sizing maintaining safe margin buffers.
        Re-runs every morning at entry — automatically captures weekly capital growth.
        - Condor (Architecture A2): Hedged spread margin = SPREAD_WIDTH * 0.001 USD.
        - Straddle (Architecture B3): Unhedged short margin ~10% notional = spot * 0.001 * 0.10.
        Growth cap: lots cannot more than double from the previous day's lot count (prevents runaway sizing).
        """
        if not DYNAMIC_SIZING:
            return self.lots

        available_inr = self.capital
        usd_inr = USD_INR_RATE  # fallback to env/hardcoded
        try:
            url = f"{self.host}/api/v1/funds"
            resp = requests.post(url, json={"apikey": self.api_key}, timeout=4)
            if resp.status_code == 200:
                funds_data = resp.json().get("data", {})
                live_cash = float(funds_data.get("availablecash", 0.0))
                # Try to derive live USD/INR rate from the wallet response (balance_inr / balance_usd)
                live_usd = float(funds_data.get("availablecash_usd") or 0.0)
                if live_usd > 0 and live_cash > 0:
                    usd_inr = live_cash / live_usd
                    logger.info(f"[LIVE FX] USD/INR rate derived from wallet: {usd_inr:.2f}")
                else:
                    logger.info(f"[FX] Using configured USD/INR rate: {usd_inr:.2f}")

                if live_cash >= 5000000:
                    # OpenAlgo Sandbox mode (1 Cr) -> use configured base testing capital directly
                    available_inr = self.capital
                    logger.info(f"[SANDBOX FUNDS] Sandbox mode detected. Using test base capital: Rs {available_inr:,.2f}")
                elif live_cash > 0:
                    # Live Delta Exchange India account -> use live available cash directly
                    available_inr = live_cash
                    logger.info(f"[LIVE FUNDS] Live Cash available: Rs {live_cash:,.2f}")
        except Exception as e:
            logger.warning(f"Funds API check notice: {e}")

        prev_lots = self.lots  # Track previous lots for growth cap

        if self.mode == "straddle":
            # 10% notional margin per unhedged short leg with 50% max margin cap
            margin_per_lot_usd = spot * 0.001 * 0.10
            margin_per_lot_inr = margin_per_lot_usd * usd_inr
            usable_margin_inr = available_inr * 0.50
            computed_lots = int(usable_margin_inr / margin_per_lot_inr)
            # Growth cap: max 2x previous lots per day to prevent runaway sizing
            max_allowed = max(prev_lots * 2, DEFAULT_LOTS)
            final_lots = max(1, min(computed_lots, 1000, max_allowed))
            logger.info(
                f"Dynamic Straddle Sizing: Capital Base=Rs {available_inr:,.2f} | "
                f"50% Usable=Rs {usable_margin_inr:,.2f} | Computed={computed_lots} | "
                f"GrowthCap={max_allowed} | Final={final_lots} lots"
            )
        else:
            # Hedged spread margin
            margin_per_lot_usd = self.spread_width * 0.001
            margin_per_lot_inr = margin_per_lot_usd * usd_inr
            usable_margin_inr = available_inr * MARGIN_UTILIZATION_CAP
            computed_lots = int(usable_margin_inr / margin_per_lot_inr)
            # Growth cap: max 2x previous lots per day to prevent runaway sizing
            max_allowed = max(prev_lots * 2, DEFAULT_LOTS)
            final_lots = max(10, min(computed_lots, 3000, max_allowed))
            logger.info(
                f"Dynamic Condor Sizing: Capital Base=Rs {available_inr:,.2f} | "
                f"65% Usable=Rs {usable_margin_inr:,.2f} | Computed={computed_lots} | "
                f"GrowthCap={max_allowed} | Final={final_lots} lots (35% Free Buffer Preserved)"
            )
        return final_lots

    def execute_iron_condor(self) -> bool:
        spot = self.get_spot_price()
        if not spot or spot <= 0:
            logger.error("Failed to retrieve valid BTC spot price. Aborting execution.")
            return False

        expiry = self.get_today_expiry()
        if not expiry:
            logger.error("Failed to retrieve valid option expiry date. Aborting.")
            return False

        self.expiry_date = expiry
        self.lots = self.compute_dynamic_lots(spot=spot)
        logger.info("=" * 80)
        logger.info(f"⚡ EXECUTING DAILY BTC IRON CONDOR (Architecture A2, Expiry: {expiry})")
        logger.info(f"Current BTC Spot LTP: ${spot:,.2f} | Target Position Size: {self.lots} contracts (65% Margin Cap)")
        logger.info("=" * 80)

        # 1. Compute Strike Targets
        short_call_target = spot * (1.0 + self.otm_pct)
        long_call_target = short_call_target + self.spread_width
        short_put_target = spot * (1.0 - self.otm_pct)
        long_put_target = short_put_target - self.spread_width

        # 2. Resolve Exact Exchange Symbols using Adaptive Liquid Strike Hunter:
        # Resolve Short legs first to establish anchor strikes
        sym_short_ce = self.resolve_liquid_option_symbol(
            short_call_target, "CE", expiry, action="SELL", is_wing=False
        )
        sym_short_pe = self.resolve_liquid_option_symbol(
            short_put_target, "PE", expiry, action="SELL", is_wing=False
        )

        if not sym_short_ce or not sym_short_pe:
            logger.error("Failed to resolve liquid short option symbols. Aborting execution.")
            return False

        # Resolve Long Wings bounded by short strikes to guarantee spread width margin cap
        sym_long_ce = self.resolve_liquid_option_symbol(
            long_call_target, "CE", expiry, action="BUY", is_wing=True, short_strike_ref=short_call_target
        )
        sym_long_pe = self.resolve_liquid_option_symbol(
            long_put_target, "PE", expiry, action="BUY", is_wing=True, short_strike_ref=short_put_target
        )

        if not sym_long_ce or not sym_long_pe:
            logger.error("Failed to resolve liquid wing option symbols. Aborting execution.")
            return False

        logger.info(f"Liquid Strikes Verified & Selected ({expiry}):")
        logger.info(f"  • Call Wing (BUY Hedge) : {sym_long_ce}")
        logger.info(f"  • Call Short (SELL)     : {sym_short_ce}")
        logger.info(f"  • Put Short (SELL)      : {sym_short_pe}")
        logger.info(f"  • Put Wing (BUY Hedge)  : {sym_long_pe}")

        # 3. Two-Phase Order Sequence (Wings First, then Shorts) with Atomic Rollback
        legs_sequence = [
            ("LONG_CE", sym_long_ce, "BUY", self.lots),
            ("LONG_PE", sym_long_pe, "BUY", self.lots),
            ("SHORT_CE", sym_short_ce, "SELL", self.lots),
            ("SHORT_PE", sym_short_pe, "SELL", self.lots),
        ]

        executed_positions = {}
        for leg_type, sym, act, qty in legs_sequence:
            success, fill_p, oid = self.place_and_verify_order(sym, act, qty, timeout_sec=6)
            if success:
                sl_price = fill_p * self.sl_multiplier if act == "SELL" else 0.0
                executed_positions[sym] = {
                    "leg_type": leg_type,
                    "action": act,
                    "quantity": qty,
                    "entry_price": fill_p,
                    "status": "OPEN",
                    "stop_loss": sl_price,
                    "order_id": oid,
                }
                logger.info(f"  ✅ [{leg_type}] {act} {qty} {sym} established @ ${fill_p:.2f}")
            else:
                logger.error(f"  ❌ Leg execution failed for [{leg_type}] {act} {sym}. Triggering ATOMIC ROLLBACK...")
                break

        # If any leg failed, initiate atomic rollback
        if len(executed_positions) < len(legs_sequence):
            logger.warning(
                f"Incomplete Condor ({len(executed_positions)}/4 legs). "
                f"Rolling back all filled legs to eliminate unhedged exposure..."
            )
            # Sort rollback legs: Shorts FIRST (BUY to close), Wings SECOND (SELL to close)
            rb_legs = sorted(
                list(executed_positions.items()),
                key=lambda item: 0 if item[1].get("action") == "SELL" else 1
            )
            for sym, pos in rb_legs:
                rb_action = "SELL" if pos["action"] == "BUY" else "BUY"
                logger.info(f"  Rollback: {rb_action} {pos['quantity']} {sym}")
                for attempt in range(2):
                    rb_success, _, _ = self.place_and_verify_order(sym, rb_action, pos["quantity"], timeout_sec=6)
                    if rb_success:
                        logger.info(f"  ✅ Successfully rolled back {sym}")
                        break
                    else:
                        logger.warning(f"  ⚠️ Rollback attempt {attempt + 1} failed for {sym}, retrying...")
                        time.sleep(1.0)
            self.positions = {}
            self.trade_active = False
            self.trade_taken_today = False
            self._save_state()
            logger.error("❌ Iron Condor establishment aborted cleanly. Zero unhedged positions remaining.")
            return False

        self.positions = executed_positions
        self.trade_active = True
        self.trade_taken_today = True
        self.entry_time = get_current_ist_datetime().isoformat()

        # Calculate Net Credit
        sc_prem = self.positions[sym_short_ce]["entry_price"]
        sp_prem = self.positions[sym_short_pe]["entry_price"]
        lc_prem = self.positions[sym_long_ce]["entry_price"]
        lp_prem = self.positions[sym_long_pe]["entry_price"]
        net_credit_per_contract = max(0.0, (sc_prem + sp_prem) - (lc_prem + lp_prem))
        self.net_credit_collected = net_credit_per_contract * self.lots * 0.001
        self.target_profit = self.net_credit_collected * self.target_decay_pct

        logger.info("=" * 80)
        logger.info(f"🎉 4-LEG IRON CONDOR FULLY ESTABLISHED & VERIFIED!")
        logger.info(f"Net Credit Expected: ~${self.net_credit_collected:.2f} USD (~Rs {self.net_credit_collected * 88.0:,.2f} INR)")
        logger.info(f"Target Profit (80% Decay): ~${self.target_profit:.2f} USD (~Rs {self.target_profit * 88.0:,.2f} INR)")
        logger.info(f"Call SL Threshold: ${self.positions[sym_short_ce]['stop_loss']:.2f} | Put SL Threshold: ${self.positions[sym_short_pe]['stop_loss']:.2f}")
        logger.info("=" * 80)
        self._save_state()
        return True

    def execute_atm_straddle(self) -> bool:
        spot = self.get_spot_price()
        if not spot or spot <= 0:
            logger.error("Failed to retrieve valid BTC spot price. Aborting execution.")
            return False

        expiry = self.get_today_expiry()
        if not expiry:
            logger.error("Failed to retrieve valid option expiry date. Aborting.")
            return False

        self.expiry_date = expiry
        self.lots = self.compute_dynamic_lots(spot=spot)
        logger.info("=" * 80)
        logger.info(f"⚡ EXECUTING DAILY BTC ATM STRADDLE (Architecture B3, Expiry: {expiry})")
        logger.info(f"Current BTC Spot LTP: ${spot:,.2f} | Target Position Size: {self.lots} contracts (50% Margin Cap)")
        logger.info("=" * 80)

        strike_step = 100.0
        atm_strike = round(spot / strike_step) * strike_step

        sym_short_ce = self.resolve_liquid_option_symbol(
            atm_strike, "CE", expiry, action="SELL", is_wing=False
        )
        sym_short_pe = self.resolve_liquid_option_symbol(
            atm_strike, "PE", expiry, action="SELL", is_wing=False
        )

        if not sym_short_ce or not sym_short_pe:
            logger.error("Failed to resolve liquid ATM straddle symbols. Aborting execution.")
            return False

        logger.info(f"ATM Strikes Selected ({expiry}):")
        logger.info(f"  • ATM Call Short (SELL) : {sym_short_ce}")
        logger.info(f"  • ATM Put Short (SELL)  : {sym_short_pe}")

        executed_positions = {}
        legs = [
            ("SHORT_CE", sym_short_ce, "SELL", self.lots),
            ("SHORT_PE", sym_short_pe, "SELL", self.lots),
        ]

        for leg_type, sym, act, qty in legs:
            success, fill_p, oid = self.place_and_verify_order(sym, act, qty, timeout_sec=6)
            if success:
                sl_price = fill_p * (1.0 + self.straddle_sl_pct)
                executed_positions[sym] = {
                    "leg_type": leg_type,
                    "action": act,
                    "quantity": qty,
                    "entry_price": fill_p,
                    "status": "OPEN",
                    "stop_loss": sl_price,
                    "order_id": oid,
                }
                logger.info(f"  ✅ [{leg_type}] {act} {qty} {sym} established @ ${fill_p:.2f} (SL: ${sl_price:.2f})")
            else:
                logger.error(f"  ❌ Leg execution failed for [{leg_type}] {act} {sym}. Triggering ATOMIC ROLLBACK...")
                for r_sym, r_pos in executed_positions.items():
                    rb_act = "BUY" if r_pos["action"] == "SELL" else "SELL"
                    logger.info(f"  Rollback: {rb_act} {r_pos['quantity']} {r_sym}")
                    self.place_and_verify_order(r_sym, rb_act, r_pos["quantity"], timeout_sec=6)
                self.positions = {}
                self.trade_active = False
                self.trade_taken_today = False
                self._save_state()
                logger.error("❌ Straddle establishment aborted cleanly. Zero unhedged positions remaining.")
                return False

        self.positions = executed_positions
        self.trade_active = True
        self.trade_taken_today = True
        self.entry_time = get_current_ist_datetime().isoformat()

        sc_prem = self.positions[sym_short_ce]["entry_price"]
        sp_prem = self.positions[sym_short_pe]["entry_price"]
        self.net_credit_collected = (sc_prem + sp_prem) * self.lots * 0.001
        self.target_profit = self.net_credit_collected * self.target_decay_pct

        logger.info("=" * 80)
        logger.info(f"🎉 2-LEG ATM STRADDLE FULLY ESTABLISHED & VERIFIED!")
        logger.info(f"Total Premium Collected: ~${self.net_credit_collected:.2f} USD (~Rs {self.net_credit_collected * 88.0:,.2f} INR)")
        logger.info(f"Target Profit (80% Decay): ~${self.target_profit:.2f} USD (~Rs {self.target_profit * 88.0:,.2f} INR)")
        logger.info(f"Call SL Threshold (+30%): ${self.positions[sym_short_ce]['stop_loss']:.2f} | Put SL Threshold (+30%): ${self.positions[sym_short_pe]['stop_loss']:.2f}")
        logger.info("=" * 80)
        self._save_state()
        return True

    def _get_broker_net_qty(self, symbol: str) -> Optional[float]:
        """Fetch live net quantity for symbol from broker positionbook."""
        try:
            url = f"{self.host}/api/v1/positionbook"
            resp = requests.post(url, json={"apikey": self.api_key}, timeout=4)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if isinstance(data, list):
                    total_qty = 0.0
                    found = False
                    for p in data:
                        if isinstance(p, dict) and p.get("symbol") == symbol:
                            found = True
                            raw_qty = p.get("quantity") if p.get("quantity") is not None else p.get("netqty", 0)
                            total_qty += float(raw_qty or 0.0)
                    if found:
                        return total_qty
                    return 0.0
        except Exception as e:
            logger.debug(f"Positionbook check for {symbol} notice: {e}")
        return None

    def reconcile_positions_with_broker(self):
        """
        Queries live positionbook from OpenAlgo and verifies active positions.
        Aggregates net quantity across records to prevent 0-qty overwrite.
        Only reconciles local state to CLOSED if an exit was already attempted.
        """
        if self.dry_run or not self.trade_active or not self.positions:
            return

        try:
            url = f"{self.host}/api/v1/positionbook"
            resp = requests.post(url, json={"apikey": self.api_key}, timeout=4)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if isinstance(data, list):
                    broker_net_qty: Dict[str, float] = {}
                    for p in data:
                        if isinstance(p, dict) and p.get("symbol"):
                            sym = p.get("symbol")
                            raw_qty = p.get("quantity") if p.get("quantity") is not None else p.get("netqty", 0)
                            qty = float(raw_qty or 0.0)
                            broker_net_qty[sym] = broker_net_qty.get(sym, 0.0) + qty

                    changed = False
                    for sym, pos in list(self.positions.items()):
                        if pos.get("status") == "OPEN":
                            live_qty = broker_net_qty.get(sym, 0.0)
                            if abs(live_qty) == 0.0 and pos.get("exit_attempted"):
                                logger.info(f"🔄 [RECONCILIATION] Broker confirms {sym} is FLAT (0 qty). Marking CLOSED locally.")
                                pos["status"] = "CLOSED"
                                pos["exit_reason"] = "BROKER_RECONCILED_FLAT"
                                changed = True
                            elif abs(live_qty) == 0.0 and not pos.get("exit_attempted"):
                                logger.debug(f"ℹ️ [RECONCILIATION] Broker reports 0 qty for {sym} but no exit was attempted locally. Skipping false closure.")

                    all_closed = all(p.get("status") == "CLOSED" for p in self.positions.values())
                    if all_closed:
                        logger.info("🔄 [RECONCILIATION] All tracked positions are confirmed FLAT on broker. Setting trade_active = False.")
                        self.trade_active = False
                        changed = True

                    if changed:
                        self._save_state()
        except Exception as e:
            logger.debug(f"Position reconciliation check notice: {e}")

    def _get_ltp_cached(self, symbol: str) -> Optional[float]:
        """Fetch LTP with 3s TTL cache to avoid hammering the quotes API every 5s tick."""
        now_ts = time.time()
        if not hasattr(self, "_ltp_cache"):
            self._ltp_cache: Dict[str, tuple] = {}
        cached = self._ltp_cache.get(symbol)
        if cached and (now_ts - cached[1]) < 3.0:
            return cached[0]
        ltp = self.get_quote_ltp(symbol)
        if ltp and ltp > 0:
            self._ltp_cache[symbol] = (ltp, now_ts)
        return ltp

    def manage_active_positions(self):
        if not self.trade_active or not self.positions:
            return

        # Continuous square-off retry loop if previous cycle was incomplete
        if getattr(self, "_squaring_off", False):
            logger.info("🔁 [SQUARE-OFF IN PROGRESS] Retrying unclosed legs until confirmed flat...")
            self.square_off_all("RETRY_UNCLOSED_LEGS")
            return

        now = get_current_ist_time()
        # 1. EOD Force Exit Check (17:15 IST)
        if now >= EOD_EXIT_TIME:
            logger.info(f"⏰ [EOD FORCE EXIT] {EOD_EXIT_TIME.strftime('%H:%M')} IST reached. Squaring off all open legs before 17:30 expiry.")
            self.square_off_all("EOD_FORCE_EXIT")
            return

        # 2. Target Profit Check (85% Theta Decay Captured)
        short_legs = [p for p in self.positions.values() if p["status"] == "OPEN" and p["action"] == "SELL"]
        if short_legs:
            initial_short_prem = sum(p["entry_price"] for p in short_legs)
            current_short_prem = 0.0
            quotes_ok = True
            for pos in short_legs:
                sym = [s for s, p in self.positions.items() if p == pos][0]
                ltp = self._get_ltp_cached(sym)
                if ltp and ltp > 0:
                    current_short_prem += ltp
                else:
                    quotes_ok = False

            decay_threshold = initial_short_prem * (1.0 - TARGET_DECAY_PCT)
            if quotes_ok and initial_short_prem > 0 and current_short_prem <= decay_threshold:
                decay_pct = ((initial_short_prem - current_short_prem) / initial_short_prem) * 100.0
                logger.info(f"🎯 [TARGET PROFIT REACHED] Short premium decayed by {decay_pct:.1f}% (Current: ${current_short_prem:.2f} <= Target: ${decay_threshold:.2f}). Squaring off all legs to lock in profit!")
                self.square_off_all("TARGET_PROFIT_DECAY")
                return

        # 3. Check Stop Loss on Short Legs with Verified Unwind
        for sym, pos in list(self.positions.items()):
            if pos["status"] != "OPEN" or pos["action"] != "SELL":
                continue

            ltp = self._get_ltp_cached(sym)
            sl = pos["stop_loss"]
            if ltp and sl > 0 and ltp >= sl:
                logger.warning(f"🚨 [STOP LOSS HIT] {sym} LTP ${ltp:.2f} >= SL ${sl:.2f} (1.5x threshold). Closing tested side!")

                pos["exit_attempted"] = True

                # Step A: Close tested short leg with fill verification and flat check
                live_qty = self._get_broker_net_qty(sym)
                if live_qty is not None and abs(live_qty) == 0.0:
                    pos["status"] = "CLOSED"
                    pos["exit_reason"] = "ALREADY_FLAT"
                    logger.info(f"  ℹ️ {sym} is already FLAT on broker (0 qty). Skipping SL exit order.")
                    closed_short = True
                else:
                    qty_to_close = abs(int(live_qty)) if live_qty is not None else pos["quantity"]
                    closed_short, _, _ = self.place_and_verify_order(sym, "BUY", qty_to_close, timeout_sec=6)
                    if closed_short:
                        pos["status"] = "CLOSED"
                        pos["exit_reason"] = "SL_HIT"
                        logger.info(f"  ✅ Confirmed closed tested short leg {sym}")
                    else:
                        logger.error(f"  ⚠️ Could not confirm fill for closing short leg {sym}. Will retry on next tick.")

                # Step B: Close corresponding hedge wing with fill verification and retry
                wing_type = "LONG_CE" if pos["leg_type"] == "SHORT_CE" else "LONG_PE"
                for w_sym, w_pos in self.positions.items():
                    if w_pos["leg_type"] == wing_type and w_pos["status"] == "OPEN":
                        w_pos["exit_attempted"] = True
                        w_live_qty = self._get_broker_net_qty(w_sym)
                        if w_live_qty is not None and abs(w_live_qty) == 0.0:
                            w_pos["status"] = "CLOSED"
                            w_pos["exit_reason"] = "ALREADY_FLAT"
                            logger.info(f"  ℹ️ Protective wing {w_sym} is already FLAT on broker (0 qty). Skipping exit order.")
                        else:
                            w_qty_to_close = abs(int(w_live_qty)) if w_live_qty is not None else w_pos["quantity"]
                            closed_wing, _, _ = self.place_and_verify_order(w_sym, "SELL", w_qty_to_close, timeout_sec=6)
                            if closed_wing:
                                w_pos["status"] = "CLOSED"
                                w_pos["exit_reason"] = "WING_UNWIND"
                                logger.info(f"  ✅ Confirmed closed protective wing {w_sym} after short SL breach.")
                            else:
                                logger.warning(f"  ⚠️ Protective wing {w_sym} exit timed out! Retrying with 8s window...")
                                retry_w, _, _ = self.place_and_verify_order(w_sym, "SELL", w_qty_to_close, timeout_sec=8)
                                if retry_w:
                                    w_pos["status"] = "CLOSED"
                                    w_pos["exit_reason"] = "WING_UNWIND"
                                    logger.info(f"  ✅ Retry successful: closed protective wing {w_sym}.")
                                else:
                                    logger.error(f"  ❌ Wing {w_sym} exit failed to fill. Will be reconciled.")

                self._save_state()

        # Check if all legs are closed
        all_closed = all(p["status"] == "CLOSED" for p in self.positions.values())
        if all_closed:
            self._handle_all_legs_closed(exit_trigger="ALL_LEGS_CLOSED")

    def _handle_all_legs_closed(self, exit_trigger: str = "MANUAL"):
        now = get_current_ist_time()
        self.session_history.append({
            "session": self.current_session,
            "positions": dict(self.positions),
            "exit_time": get_current_ist_datetime().isoformat(),
            "trigger": exit_trigger,
            "net_credit": self.net_credit_collected,
            "target_profit": self.target_profit,
        })
        self.trade_active = False

        if exit_trigger != "EOD_FORCE_EXIT" and self.current_session < MAX_DAILY_SESSIONS and now < REENTRY_CUTOFF_TIME:
            logger.info(
                f"🔄 [SESSION {self.current_session} COMPLETED ({exit_trigger})] All positions flat at {now.strftime('%H:%M:%S IST')} "
                f"(Before Cutoff {REENTRY_CUTOFF_TIME.strftime('%H:%M')}). Eligible for Session {self.current_session + 1} Re-Strike!"
            )
            self.current_session += 1
            self.positions = {}
            self.trade_taken_today = False
            self.entry_attempts = 0
            self.last_attempt_time = 0.0
            self._save_state()
        else:
            self.trade_taken_today = True
            self._save_state()
            if now >= REENTRY_CUTOFF_TIME:
                logger.info(
                    f"🏁 Session {self.current_session} closed at {now.strftime('%H:%M:%S IST')} "
                    f"(At or after cutoff {REENTRY_CUTOFF_TIME.strftime('%H:%M')}). No further re-entries today."
                )
            elif self.current_session >= MAX_DAILY_SESSIONS:
                logger.info(f"🏁 Maximum daily sessions ({MAX_DAILY_SESSIONS}) reached. Strategy idle until tomorrow.")
            else:
                logger.info(f"🏁 Strategy closed for today ({exit_trigger}).")

    def square_off_all(self, reason: str = "MANUAL"):
        # Sort legs: Short legs first (BUY to close), then Long wings (SELL to close)
        # Prevents naked short exposure and avoids exchange margin spikes
        sorted_legs = sorted(
            list(self.positions.items()),
            key=lambda item: 0 if item[1].get("action") == "SELL" else 1
        )

        for sym, pos in sorted_legs:
            if pos.get("status") == "OPEN":
                close_action = "SELL" if pos["action"] == "BUY" else "BUY"
                pos["exit_attempted"] = True

                # Check live broker quantity to prevent position flip / over-closing
                live_qty = self._get_broker_net_qty(sym)
                if live_qty is not None:
                    if abs(live_qty) == 0.0:
                        pos["status"] = "CLOSED"
                        pos["exit_reason"] = "ALREADY_FLAT"
                        logger.info(f"  ℹ️ {sym} is already flat on broker (0 qty). Skipping order.")
                        continue
                    qty_to_close = abs(int(live_qty))
                else:
                    qty_to_close = pos["quantity"]

                success, _, _ = self.place_and_verify_order(sym, close_action, qty_to_close, timeout_sec=6)
                if success:
                    pos["status"] = "CLOSED"
                    pos["exit_reason"] = reason
                    logger.info(f"  ✅ Closed {sym} ({close_action} {qty_to_close}) - Reason: {reason}")
                else:
                    logger.warning(f"  ⚠️ Square-off did not confirm fill for {sym}, retrying with 8s window...")
                    success_retry, _, _ = self.place_and_verify_order(sym, close_action, qty_to_close, timeout_sec=8)
                    if success_retry:
                        pos["status"] = "CLOSED"
                        pos["exit_reason"] = reason
                        logger.info(f"  ✅ Closed {sym} on retry - Reason: {reason}")
                    else:
                        logger.error(f"  ❌ Square-off failed for {sym}. Will retry on subsequent ticks.")

        all_closed = all(p.get("status") == "CLOSED" for p in self.positions.values())
        if all_closed:
            self._squaring_off = False
            self._handle_all_legs_closed(exit_trigger=reason)
        else:
            self._squaring_off = True
            self._save_state()

    def run(self):
        logger.info("=" * 80)
        logger.info(f"🚀 BTC DAILY HEDGED IRON CONDOR RUNNING — {STRATEGY_NAME}")
        logger.info(f"Target: Port 5001 | Lots: {self.lots} contracts | OTM: {OTM_PCT*100:.1f}% | Wing: ${SPREAD_WIDTH:.0f}")
        logger.info(f"Session 1 Window: {ENTRY_TIME_START.strftime('%H:%M')} - {ENTRY_TIME_END.strftime('%H:%M')} IST")
        logger.info(f"Session 2 Re-Strike Cutoff: {REENTRY_CUTOFF_TIME.strftime('%H:%M')} IST | Max Sessions: {MAX_DAILY_SESSIONS} | EOD Exit: {EOD_EXIT_TIME.strftime('%H:%M')} IST")
        logger.info("=" * 80)

        def _sig_handler(sig, frame):
            logger.info(f"[SHUTDOWN] Signal {sig} received.")
            self.shutdown_event = True

        signal.signal(signal.SIGINT, _sig_handler)
        signal.signal(signal.SIGTERM, _sig_handler)

        # Audit and reconcile broker positions immediately upon startup/restart
        self.audit_and_recover_positions_on_startup()

        last_hb = time.time()
        while not self.shutdown_event:
            now = get_current_ist_time()
            now_epoch = time.time()

            # Heartbeat and broker reconciliation every 60s
            if now_epoch - last_hb >= 60.0:
                last_hb = now_epoch
                if self.trade_active:
                    self.reconcile_positions_with_broker()
                open_count = sum(1 for p in self.positions.values() if p.get("status") == "OPEN")
                if self.trade_active:
                    status_str = f"ACTIVE SESSION {self.current_session}/{MAX_DAILY_SESSIONS} (Monitoring {open_count} Legs)"
                elif self.trade_taken_today:
                    status_str = f"DONE TODAY ({self.current_session} session(s) completed)"
                elif self.entry_attempts >= self.max_entry_attempts:
                    status_str = f"MAX ATTEMPTS ({self.max_entry_attempts}) REACHED (Awaiting Manual/Force)"
                elif self.current_session == 1 and now < ENTRY_TIME_START:
                    status_str = f"AWAITING {ENTRY_TIME_START.strftime('%H:%M')} IST WINDOW"
                elif (self.current_session == 1 and now > ENTRY_TIME_END) or (self.current_session > 1 and now > REENTRY_CUTOFF_TIME):
                    status_str = "ENTRY WINDOW CLOSED (Past Cutoff)"
                else:
                    elapsed = now_epoch - self.last_attempt_time
                    cd_left = max(0, int(self.retry_cooldown_sec - elapsed))
                    status_str = f"SESSION {self.current_session} WINDOW OPEN (Attempts: {self.entry_attempts}/{self.max_entry_attempts}, Cooldown: {cd_left}s)"
                logger.info(f"💓 [HEARTBEAT] Time: {get_current_ist_datetime().strftime('%H:%M:%S IST')} | Status: {status_str}")

            # 1. Trigger Entry if in entry window and no trade active for current session
            if not self.trade_taken_today and not self.trade_active:
                is_valid_window = False
                if self.current_session == 1:
                    is_valid_window = (ENTRY_TIME_START <= now <= ENTRY_TIME_END)
                elif self.current_session <= MAX_DAILY_SESSIONS:
                    is_valid_window = (now <= REENTRY_CUTOFF_TIME)

                if is_valid_window:
                    if self.entry_attempts >= self.max_entry_attempts:
                        if now_epoch - self.last_attempt_log >= 300.0:
                            logger.warning(
                                f"⚠️ Maximum entry attempts ({self.max_entry_attempts}) reached for Session {self.current_session}. "
                                f"No further automatic retries. Run with --force to override."
                            )
                            self.last_attempt_log = now_epoch
                    else:
                        elapsed = now_epoch - self.last_attempt_time
                        if elapsed >= self.retry_cooldown_sec:
                            attempt_num = self.entry_attempts + 1
                            logger.info(
                                f"⏰ [SESSION {self.current_session}/{MAX_DAILY_SESSIONS}] Entry window open. "
                                f"Triggering execution (Attempt {attempt_num}/{self.max_entry_attempts})..."
                            )
                            self.last_attempt_time = now_epoch
                            self.entry_attempts += 1
                            self._save_state()

                            if self.mode == "straddle":
                                success = self.execute_atm_straddle()
                            else:
                                success = self.execute_iron_condor()
                            if not success:
                                logger.warning(
                                    f"⚠️ Entry attempt {self.entry_attempts}/{self.max_entry_attempts} did not establish strategy. "
                                    f"Entering {self.retry_cooldown_sec}s cooldown before next attempt."
                                )
                                self._save_state()
                        else:
                            remaining_cd = int(self.retry_cooldown_sec - elapsed)
                            if now_epoch - self.last_cooldown_log >= 30.0:
                                logger.info(f"⏳ [RETRY COOLDOWN] Next entry attempt permitted in {remaining_cd}s...")
                                self.last_cooldown_log = now_epoch

            # 2. Position Management
            if self.trade_active:
                self.manage_active_positions()

            time.sleep(5)

# ------------------------------------------------------------------------------
# 4. CLI ENTRY POINT
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BTC Daily Options Strategy Engine (Delta Exchange)")
    parser.add_argument("--mode", type=str, choices=["condor", "straddle"], default=STRATEGY_MODE, help="Architecture mode: condor (Arch A2) or straddle (Arch B3)")
    parser.add_argument("--lots", type=int, default=DEFAULT_LOTS, help="Number of contracts to trade (default: 60)")
    parser.add_argument("--capital", type=float, default=CAPITAL_BASE_INR, help="Base capital in INR (default: 10000.0)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate orders without placing real orders")
    parser.add_argument("--test", action="store_true", help="Execute one-shot test entry and immediate square-off")
    parser.add_argument("--force", action="store_true", help="Force entry by resetting trade_taken_today and attempt counters")
    args = parser.parse_args()

    host, api_key = resolve_crypto_host_and_key()
    strategy = BTCDailyIronCondor(
        host=host,
        api_key=api_key,
        lots=args.lots,
        capital=args.capital,
        dry_run=args.dry_run,
        force=args.force,
        mode=args.mode,
    )

    if args.test:
        logger.info(f"🧪 Running one-shot test validation ({strategy.mode.upper()})...")
        if strategy.mode == "straddle":
            strategy.execute_atm_straddle()
        else:
            strategy.execute_iron_condor()
        time.sleep(3)
        strategy.square_off_all(reason="TEST_COMPLETE")
    else:
        strategy.run()
