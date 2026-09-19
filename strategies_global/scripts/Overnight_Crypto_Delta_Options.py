#!/usr/bin/env python3
"""
================================================================================
OPENALGO PRODUCTION STRATEGY: OVERNIGHT CRYPTO DELTA OPTIONS (BTC & ETH)
================================================================================
Market           : Crypto Options (Delta Exchange India)
Target Underlying: BTC (Bitcoin) & ETH (Ethereum) - Supports BTC, ETH, or BOTH
Execution Route  : Port 5001 (openalgo-global container)
Frequency        : Daily 1DTE Overnight Expiry (Entered at 22:00 IST, exits 06:30 AM IST)
Architecture     : 4-Leg Defined-Risk Iron Condor with "Wing-First" Execution
                   1. BUY OTM Call Wing (Hedge)
                   2. BUY OTM Put Wing (Hedge)
                   3. SELL OTM Call (Short)
                   4. SELL OTM Put (Short)
Safety Invariants:
  - Strict "Hedge-First" sequence (never sell shorts unless wings are filled)
  - Pre-flight Layer-2 orderbook depth & spread inspection
  - Adaptive inward strike walking for wing liquidity
  - Pegged collared limit orders with IOC / timeout
Risk Management  :
  - 2.0x Premium Stop Loss on Short Legs
  - 50% Net Premium Decay Take Profit
  - 06:30 AM IST Hard Cutoff Exit (Locks overnight profit before Europe/Asia open)
State File       : strategies_global/data/overnight_crypto_delta_YYYYMMDD.json
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
logger = logging.getLogger("Overnight_Crypto_Delta")

# ------------------------------------------------------------------------------
# 1. PARAMETERS & CONFIGURATION
# ------------------------------------------------------------------------------
STRATEGY_NAME = "Overnight_Crypto_Delta_Options"
EXCHANGE = "CRYPTO"

# Asset Configuration Presets
ASSET_CONFIGS = {
    "BTC": {
        "underlying": "BTC",
        "futures_symbol": "BTCUSDFUT",
        "strike_step": 100.0,
        "otm_pct": float(os.getenv("BTC_OVERNIGHT_OTM_PCT", "0.020")),      # 2.0% OTM shorts
        "wing_width_strikes": int(os.getenv("BTC_OVERNIGHT_WING_STRIKES", "8")), # 8 strikes = $800 width (Target: 92 lots)
        "contract_mult": 0.001,                                              # 1 contract = 0.001 BTC
        "default_lots": int(os.getenv("BTC_OVERNIGHT_LOTS", "92")),         # 92 contracts on Rs 10k capital base
        "max_wing_price": float(os.getenv("BTC_MAX_WING_PRICE", "30.0")),   # $30 max wing budget
        "max_spread_pct": 0.15,                                              # 15% max spread on shorts
        "max_short_spread_dollar": 3.00,                                     # $3.00 max dollar spread on shorts
        "max_wing_spread_pct": 0.25,                                         # 25% max spread on wings
        "max_wing_spread_dollar": 4.50,                                      # $4.50 max dollar spread on wings
        "min_depth_contracts": 100,                                          # 100 contracts min depth
    },
    "ETH": {
        "underlying": "ETH",
        "futures_symbol": "ETHUSDFUT",
        "strike_step": 10.0,
        "otm_pct": float(os.getenv("ETH_OVERNIGHT_OTM_PCT", "0.012")),      # 1.2% OTM shorts
        "wing_width_strikes": int(os.getenv("ETH_OVERNIGHT_WING_STRIKES", "3")), # 3 strikes = $30 width (Target: 242 lots)
        "contract_mult": 0.01,                                               # 1 contract = 0.01 ETH
        "default_lots": int(os.getenv("ETH_OVERNIGHT_LOTS", "242")),        # 242 contracts on Rs 10k capital base
        "max_wing_price": float(os.getenv("ETH_MAX_WING_PRICE", "4.0")),    # $4 max wing budget
        "max_spread_pct": 0.15,                                              # 15% max spread on shorts
        "max_short_spread_dollar": 0.60,                                     # $0.60 max dollar spread on shorts
        "max_wing_spread_pct": 0.25,                                         # 25% max spread on wings
        "max_wing_spread_dollar": 0.80,                                      # $0.80 max dollar spread on wings
        "min_depth_contracts": 100,                                          # 100 contracts min depth
    }
}

CAPITAL_BASE_INR = float(os.getenv("CAPITAL_BASE_INR", os.getenv("OVERNIGHT_CAPITAL_INR", "10000.0"))) # Rs 10,000 per asset
MARGIN_UTILIZATION_CAP = float(os.getenv("MARGIN_UTILIZATION_CAP", "0.65"))                             # 65% margin max = 35% free buffer
USD_INR_RATE = float(os.getenv("USD_INR_RATE", "88.0"))                                                 # Fallback FX rate
DYNAMIC_SIZING = os.getenv("DYNAMIC_SIZING", "True").lower() in ("true", "1", "yes")                  # Compounding / wallet scaling

SL_MULTIPLIER = float(os.getenv("OVERNIGHT_SL_MULT", "2.0"))        # 2.0x short premium stop loss
TARGET_DECAY_PCT = float(os.getenv("OVERNIGHT_TP_PCT", "0.50"))     # 50% net decay target
ENTRY_TIME_START = datetime.strptime("22:00", "%H:%M").time()      # 22:00 IST (10:00 PM IST)
ENTRY_TIME_END = datetime.strptime("04:30", "%H:%M").time()        # Entry allowed until 04:30 AM IST
MORNING_CUTOFF_TIME = datetime.strptime("06:30", "%H:%M").time()   # 06:30 AM IST Hard Exit Cutoff

def get_state_file_path() -> Path:
    base_dir = Path(__file__).resolve().parent.parent / "data"
    base_dir.mkdir(parents=True, exist_ok=True)
    today_str = get_current_ist_datetime().strftime("%Y%m%d")
    return base_dir / f"overnight_crypto_delta_{today_str}.json"

# ------------------------------------------------------------------------------
# 2. CLIENT & HOST RESOLUTION (PORT 5001 - DUAL INSTANCE INVARIANT)
# ------------------------------------------------------------------------------
def resolve_crypto_host_and_key() -> Tuple[str, str]:
    default_host = "http://127.0.0.1:5000" if os.path.exists("/.dockerenv") else "http://127.0.0.1:5001"
    host = os.getenv("OPENALGO_HOST_CRYPTO") or os.getenv("OPENALGO_HOST", default_host)
    api_key = (
        os.getenv("OPENALGO_API_KEY_CRYPTO")
        or os.getenv("OPENALGO_API_KEY")
        or os.getenv("CRYPTO_API_KEY")
        or "56609a7a820eaadf566b44babb61609fa55c25100996723f31a0b048f0c86721"
    )
    return host.rstrip("/"), api_key

# ------------------------------------------------------------------------------
# 3. OVERNIGHT CRYPTO OPTIONS ENGINE
# ------------------------------------------------------------------------------
class OvernightCryptoOptionsEngine:
    def __init__(
        self,
        symbols_to_trade: List[str],
        dry_run: bool = False,
        force_entry: bool = False,
        host: Optional[str] = None,
        api_key: Optional[str] = None,
    ):
        self.symbols_to_trade = symbols_to_trade
        self.dry_run = dry_run
        self.force_entry = force_entry or (os.getenv("FORCE_ENTRY", "false").lower() in ("true", "1", "yes"))
        env_host, env_key = resolve_crypto_host_and_key()
        self.host = host or env_host
        self.api_key = api_key or env_key
        
        self.running = True
        self.state_data = {
            "strategy": STRATEGY_NAME,
            "assets": {}
        }
        
        for sym in self.symbols_to_trade:
            self.state_data["assets"][sym] = {
                "active": False,
                "entry_done": False,
                "positions": {},
                "initial_net_credit": 0.0,
                "target_profit_value": 0.0,
                "expiry_date": None,
                "entry_time": None,
                "exit_time": None,
                "exit_reason": None,
                "pnl": 0.0
            }
            
        self._load_state()
        if not self.dry_run:
            self.audit_and_recover_positions_on_startup()

    def _load_state(self):
        state_path = get_state_file_path()
        if not state_path.exists():
            return
        try:
            with open(state_path, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if "assets" in loaded:
                for sym, s_data in loaded["assets"].items():
                    if sym in self.state_data["assets"]:
                        self.state_data["assets"][sym] = s_data
            logger.info(f"[STATE RESTORED] Restored state from {state_path.name}")
        except Exception as e:
            logger.error(f"[STATE LOAD ERROR] Could not parse state: {e}")

    def _save_state(self):
        if self.dry_run:
            return
        state_path = get_state_file_path()
        try:
            self.state_data["updated_at"] = get_current_ist_datetime().isoformat()
            tmp_path = state_path.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(self.state_data, f, indent=2)
            os.replace(tmp_path, state_path)
        except Exception as e:
            logger.error(f"[STATE SAVE ERROR] {e}")

    def audit_and_recover_positions_on_startup(self):
        """
        Critical Startup Invariant:
        When restarted or resumed after downtime/crash/server reload:
        1. Query live broker positions from /api/v1/positionbook.
        2. Sync & reconcile open legs for BTC and ETH.
        3. Ensure short legs have assigned Stop Loss thresholds (2.0x of entry).
        4. Immediately check live prices against SL:
           - If Ask/LTP >= SL: immediately unwind the condor positions!
           - If Ask/LTP < SL: arm active real-time monitoring.
        5. Persist reconciled state to disk.
        """
        if self.dry_run:
            return

        logger.info("🔍 [STARTUP POSITION AUDIT] Checking broker positionbook for active overnight contracts...")
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
                raw_qty = p.get("quantity") if p.get("quantity") is not None else p.get("netqty", 0)
                qty = float(raw_qty or 0.0)
                if abs(qty) > 0.0:
                    broker_positions[sym] = {
                        "quantity": qty,
                        "average_price": float(p.get("average_price") or 0.0),
                        "ltp": float(p.get("ltp") or 0.0),
                    }

            for asset in self.symbols_to_trade:
                a_state = self.state_data["assets"].get(asset, {})
                positions = a_state.get("positions", {})

                # Check if any leg of this asset exists on the broker
                has_broker_legs = False
                for leg_key, leg_data in positions.items():
                    sym = leg_data.get("symbol")
                    if sym and sym in broker_positions:
                        has_broker_legs = True
                        leg_data["active"] = True

                if has_broker_legs:
                    a_state["active"] = True
                    logger.info(f"📊 [{asset}] [STARTUP AUDIT] Active overnight positions confirmed on broker.")

                    # Ensure short leg SLs are present
                    for short_key in ["CE_SHORT", "PE_SHORT"]:
                        s_leg = positions.get(short_key, {})
                        if s_leg.get("active"):
                            entry = float(s_leg.get("fill_price") or 0.0)
                            sl = float(s_leg.get("sl_price") or 0.0)
                            if sl <= 0.0 and entry > 0.0:
                                s_leg["sl_price"] = round(entry * SL_MULTIPLIER, 2)
                                logger.info(f"🛡️ [{asset}] [STARTUP AUDIT] Armed missing SL for {short_key} ({s_leg.get('symbol')}) at ${s_leg['sl_price']:.2f}")

                    # Check for SL breach
                    for short_key in ["CE_SHORT", "PE_SHORT"]:
                        s_leg = positions.get(short_key, {})
                        if s_leg.get("active") and s_leg.get("symbol"):
                            sym = s_leg["symbol"]
                            sl = float(s_leg.get("sl_price") or 0.0)
                            bid, ask, _, _ = self.get_l2_depth(sym)
                            check_price = ask if (ask and ask > 0) else None
                            if not check_price or check_price <= 0:
                                bp = broker_positions.get(sym, {})
                                check_price = bp.get("ltp")

                            if check_price and sl > 0.0 and check_price >= sl:
                                logger.warning(
                                    f"🚨 [{asset}] [STARTUP SL BREACH DETECTED] {sym} Ask/LTP ${check_price:.2f} >= SL ${sl:.2f}! "
                                    f"Immediately squaring off condor!"
                                )
                                self._square_off_entire_condor(asset, f"STARTUP_{short_key}_SL_BREACH")
                                break
                            elif check_price:
                                logger.info(
                                    f"🛡️ [{asset}] [STARTUP SL INTACT] {sym} Ask/LTP ${check_price:.2f} < SL ${sl:.2f}. Protection armed."
                                )

            self._save_state()
            logger.info("✅ [STARTUP AUDIT COMPLETE] Overnight state synchronized with broker.")
        except Exception as e:
            logger.exception(f"❌ [STARTUP AUDIT ERROR] {e}")

    # --------------------------------------------------------------------------
    # Market Data & Option Chain Helpers
    # --------------------------------------------------------------------------
    def get_spot_price(self, asset: str) -> Optional[float]:
        cfg = ASSET_CONFIGS.get(asset)
        if not cfg:
            return None
        fut_sym = cfg["futures_symbol"]
        try:
            url = f"{self.host}/api/v1/quotes"
            resp = requests.post(
                url,
                json={"apikey": self.api_key, "symbol": fut_sym, "exchange": EXCHANGE},
                timeout=4
            )
            if resp.status_code == 200:
                data = resp.json()
                inner = data.get("data", data) if isinstance(data, dict) else {}
                ltp = inner.get("ltp")
                if ltp and float(ltp) > 0:
                    return float(ltp)
        except Exception as e:
            logger.warning(f"[{asset}] Failed to fetch spot LTP from {self.host}: {e}")
        return None

    def get_1dte_expiry(self, asset: str) -> Optional[str]:
        """
        Fetches option expiries and selects the nearest active 1DTE expiry contract.
        Settlement occurs daily at 17:30 IST on Delta Exchange.
        - Before 17:30 IST (including overnight 22:00-06:30): today's date IS the active contract expiring at 17:30 IST!
        - At or after 17:30 IST: today's contract has settled, so filter out today and target tomorrow's contract.
        """
        try:
            url = f"{self.host}/api/v1/expiry"
            resp = requests.post(
                url,
                json={"apikey": self.api_key, "symbol": asset, "exchange": EXCHANGE, "instrumenttype": "options"},
                timeout=5
            )
            if resp.status_code == 200:
                exp_list = resp.json().get("data", [])
                if exp_list:
                    now_ist = get_current_ist_datetime()
                    today_str = now_ist.strftime("%d-%b-%y").upper()
                    # Filter out today's settled contract only if past 17:30 IST
                    cutoff_settle = datetime.strptime("17:30", "%H:%M").time()
                    if now_ist.time() >= cutoff_settle:
                        valid_expiries = [e for e in exp_list if e.upper() != today_str]
                    else:
                        valid_expiries = exp_list
                    if valid_expiries:
                        return valid_expiries[0]
                    return exp_list[0]
        except Exception as e:
            logger.warning(f"[{asset}] Expiry fetch failed: {e}")
        return None

    def resolve_option_symbol(self, asset: str, strike: float, option_type: str, expiry: str) -> Optional[str]:
        """
        Resolves canonical Delta Exchange option symbol for OpenAlgo.
        Delta option canonical format: {asset}{DDMMMYY}{strike_int}{option_type}
        Example: BTC19SEP2681000CE
        """
        cfg = ASSET_CONFIGS[asset]
        step = cfg["strike_step"]
        rounded_strike = int(round(strike / step) * step)
        
        # Format clean expiry without hyphens
        exp_clean = expiry.replace("-", "").upper()
        canonical_sym = f"{asset}{exp_clean}{rounded_strike}{option_type}"

        # 1. Verify via OpenAlgo optionsymbol resolver
        try:
            url = f"{self.host}/api/v1/optionsymbol"
            resp = requests.post(
                url,
                json={
                    "apikey": self.api_key,
                    "underlying": asset,
                    "exchange": EXCHANGE,
                    "expiry_date": expiry,
                    "offset": "ATM",
                    "option_type": option_type,
                    "underlying_ltp": rounded_strike
                },
                timeout=4
            )
            if resp.status_code == 200:
                sym = resp.json().get("symbol")
                if sym:
                    return sym
        except Exception:
            pass

        # 2. Fallback to deterministic canonical format
        return canonical_sym

    def get_l2_depth(self, symbol: str) -> Tuple[Optional[float], Optional[float], int, int]:
        """
        Returns: (best_bid, best_ask, bid_size, ask_size)
        """
        if self.dry_run:
            return 20.0, 21.0, 5000, 5000
        try:
            url = f"{self.host}/api/v1/depth"
            resp = requests.post(
                url,
                json={"apikey": self.api_key, "symbol": symbol, "exchange": EXCHANGE},
                timeout=4
            )
            if resp.status_code == 200:
                data = resp.json().get("data", {})
                bids = data.get("bids", [])
                asks = data.get("asks", [])
                best_bid = float(bids[0]["price"]) if (bids and "price" in bids[0]) else None
                bid_size = int(bids[0].get("quantity") or bids[0].get("size") or 0) if bids else 0
                best_ask = float(asks[0]["price"]) if (asks and "price" in asks[0]) else None
                ask_size = int(asks[0].get("quantity") or asks[0].get("size") or 0) if asks else 0
                return best_bid, best_ask, bid_size, ask_size
        except Exception as e:
            logger.debug(f"Depth fetch error for {symbol}: {e}")

        # Fallback to quotes endpoint
        try:
            url = f"{self.host}/api/v1/quotes"
            resp = requests.post(
                url,
                json={"apikey": self.api_key, "symbol": symbol, "exchange": EXCHANGE},
                timeout=4
            )
            if resp.status_code == 200:
                inner = resp.json().get("data", {})
                bid = float(inner.get("bid") or 0.0) or None
                ask = float(inner.get("ask") or 0.0) or None
                return bid, ask, 1000, 1000
        except Exception:
            pass

        return None, None, 0, 0

    # --------------------------------------------------------------------------
    # Adaptive Strike & Wing Hunter
    # --------------------------------------------------------------------------
    def hunt_liquid_strike(
        self,
        asset: str,
        base_strike: float,
        option_type: str,
        action: str,
        expiry: str,
        is_wing: bool = False,
        walk_direction: int = 0
    ) -> Tuple[Optional[str], float, float, float]:
        """
        Inspects orderbook depth & spread.
        If illiquid, walks inward (walk_direction: -1 for Call wing, +1 for Put wing) up to 4 steps.
        Returns: (symbol, strike, best_bid, best_ask)
        """
        cfg = ASSET_CONFIGS[asset]
        step = cfg["strike_step"]
        curr_strike = base_strike
        
        max_steps = 4 if is_wing else 2
        for step_idx in range(max_steps):
            sym = self.resolve_option_symbol(asset, curr_strike, option_type, expiry)
            bid, ask, b_sz, a_sz = None, None, 0, 0
            if sym:
                if self.dry_run:
                    if is_wing:
                        return sym, curr_strike, 5.0, 6.0
                    else:
                        return sym, curr_strike, 45.0, 47.0

                bid, ask, b_sz, a_sz = self.get_l2_depth(sym)

                if is_wing and action == "BUY":
                    # Wing verification: Must have active Ask, sufficient depth, and fair spread
                    if ask is not None and ask > 0:
                        dollar_spr = (ask - bid) if bid else 999.0
                        spr_pct = (dollar_spr / ask) if bid else 1.0
                        
                        spread_ok = (spr_pct <= cfg["max_wing_spread_pct"]) or (dollar_spr <= cfg["max_wing_spread_dollar"])
                        depth_ok = a_sz >= cfg["min_depth_contracts"] or a_sz >= cfg["default_lots"]
                        price_ok = ask <= cfg["max_wing_price"]
                        
                        if spread_ok and depth_ok and price_ok:
                            if step_idx > 0:
                                logger.info(f"  [{asset}] Adaptive wing stepped inward to {sym} (${ask:.2f})")
                            return sym, curr_strike, bid or 0.0, ask
                        else:
                            logger.warning(
                                f"  [{asset}] Wing candidate {sym} illiquid/wide (Ask: ${ask:.2f}, Spr: ${dollar_spr:.2f}/{spr_pct*100:.1f}%, Size: {a_sz}). Walking inward..."
                            )
                    else:
                        logger.warning(f"  [{asset}] Wing candidate {sym} has no active ask. Walking inward...")
                elif not is_wing and action == "SELL":
                    # Short leg verification: Must have active Bid, tight spread <= 15% or small dollar spread
                    if bid is not None and ask is not None and bid > 0 and ask > 0:
                        dollar_spr = ask - bid
                        spr_pct = dollar_spr / ask
                        spread_ok = (spr_pct <= cfg["max_spread_pct"]) or (dollar_spr <= cfg.get("max_short_spread_dollar", 3.00))
                        depth_ok = b_sz >= cfg["min_depth_contracts"] or b_sz >= cfg["default_lots"]
                        if spread_ok and depth_ok:
                            return sym, curr_strike, bid, ask
                        else:
                            logger.warning(
                                f"  [{asset}] Short candidate {sym} spread wide ({spr_pct*100:.1f}% > {cfg['max_spread_pct']*100:.1f}%, Spr: ${dollar_spr:.2f}, Size: {b_sz}). Walking..."
                            )
                    else:
                        logger.warning(f"  [{asset}] Short candidate {sym} has no active bid/ask. Walking...")

            # Walk strike
            if walk_direction != 0:
                curr_strike += (step * walk_direction)
            else:
                break
                
        return None, curr_strike, 0.0, 0.0

    # --------------------------------------------------------------------------
    # Pegged Collared Limit Execution & Order Lifecycle
    # --------------------------------------------------------------------------
    def place_pegged_limit_order(
        self,
        symbol: str,
        action: str,
        quantity: int,
        bid: float,
        ask: float,
        is_wing: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        """
        Executes order using aggressive limit collar:
        - BUY (Wings): Limit at Ask price (crosses spread for guaranteed immediate fill)
        - SELL (Shorts): Limit at Bid price (crosses spread for guaranteed immediate fill)
        Returns: (success, order_id)
        """
        if self.dry_run:
            logger.info(f"  [DRY-RUN ORDER] {action} {quantity}x {symbol} @ ${(bid+ask)/2:.2f}")
            return True, f"dry_run_{symbol}_{int(time.time())}"

        if action == "BUY":
            pegged_price = round(ask, 2)
        else:
            pegged_price = round(bid, 2)

        payload = {
            "apikey": self.api_key,
            "strategy": STRATEGY_NAME,
            "symbol": symbol,
            "action": action,
            "exchange": EXCHANGE,
            "pricetype": "LIMIT",
            "price": pegged_price,
            "quantity": quantity,
            "product": "NRML",
        }

        try:
            url = f"{self.host}/api/v1/placeorder"
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                res = resp.json()
                status = res.get("status", "").lower()
                order_id = str(res.get("orderid") or "")
                logger.info(f"  [ORDER PLACED] {action} {quantity}x {symbol} @ ${pegged_price:.2f} | OrderId: {order_id} | Status: {status}")
                return True, order_id
            else:
                logger.error(f"  [ORDER FAILED] HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"  [ORDER EXCEPTION] {e}")
        return False, None

    def cancel_order(self, order_id: str):
        """Cancels an unfilled open order by ID."""
        if not order_id or self.dry_run:
            return
        try:
            url = f"{self.host}/api/v1/cancelorder"
            requests.post(url, json={"apikey": self.api_key, "strategy": STRATEGY_NAME, "orderid": order_id}, timeout=3)
            logger.info(f"  [ORDER CANCELLED] {order_id}")
        except Exception as e:
            logger.debug(f"Cancel order notice for {order_id}: {e}")

    def wait_for_fill(self, order_id: Optional[str], timeout_sec: float = 6.0) -> Tuple[bool, float]:
        """
        Polls order status until order is confirmed FILLED (or timeout).
        If timeout expires without fill, cancels the order.
        Returns: (is_filled, fill_price)
        """
        if self.dry_run or not order_id:
            return True, 0.0

        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            time.sleep(0.5)
            # Check 1: orderstatus endpoint
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
                        return True, fill_price
                    elif status in ("cancelled", "rejected"):
                        logger.error(f"  ❌ Order {order_id} was {status.upper()}")
                        return False, 0.0
            except Exception:
                pass

            # Check 2: orderbook fallback
            try:
                ob_resp = requests.post(
                    f"{self.host}/api/v1/orderbook",
                    json={"apikey": self.api_key},
                    timeout=3,
                )
                if ob_resp.status_code == 200:
                    orders = ob_resp.json().get("data", {}).get("orders", [])
                    if isinstance(orders, list):
                        target = next((o for o in orders if str(o.get("orderid")) == str(order_id)), None)
                        if target:
                            status = str(target.get("order_status", "")).lower()
                            if status in ("complete", "filled"):
                                fill_price = float(target.get("average_price") or target.get("price") or 0.0)
                                logger.info(f"  ✅ Order {order_id} confirmed FILLED via orderbook @ ${fill_price:.2f}")
                                return True, fill_price
            except Exception:
                pass

        logger.warning(f"  ⏳ Order {order_id} fill timeout ({timeout_sec}s). Cancelling...")
        self.cancel_order(order_id)
        return False, 0.0

    def _get_broker_net_qty(self, symbol: str) -> Optional[float]:
        """Fetch live net quantity for symbol from broker positionbook."""
        if self.dry_run:
            return None
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
        Reconciles open condor legs with live broker positionbook.
        Marks leg inactive only if exit was attempted and broker confirms flat.
        """
        if self.dry_run:
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
                            broker_net_qty[sym] = broker_net_qty.get(sym, 0.0) + float(raw_qty or 0.0)

                    changed = False
                    for asset in self.symbols_to_trade:
                        a_state = self.state_data["assets"].get(asset, {})
                        if not a_state.get("active"):
                            continue
                        positions = a_state.get("positions", {})
                        for leg_key, leg_data in positions.items():
                            if leg_data.get("active"):
                                sym = leg_data.get("symbol")
                                live_qty = broker_net_qty.get(sym, 0.0)
                                if abs(live_qty) == 0.0 and leg_data.get("exit_attempted"):
                                    logger.info(f"🔄 [{asset}] [RECONCILIATION] Broker confirms {sym} is FLAT (0 qty). Marking inactive.")
                                    leg_data["active"] = False
                                    changed = True

                        any_active = any(leg.get("active") for leg in positions.values())
                        if not any_active:
                            logger.info(f"🔄 [{asset}] [RECONCILIATION] All legs confirmed FLAT on broker. Condor closed.")
                            a_state["active"] = False
                            a_state["exit_time"] = get_current_ist_datetime().isoformat()
                            a_state["exit_reason"] = a_state.get("_square_off_reason") or "BROKER_RECONCILED_FLAT"
                            a_state["_squaring_off"] = False
                            changed = True

                    if changed:
                        self._save_state()
        except Exception as e:
            logger.debug(f"Position reconciliation notice: {e}")

    def square_off_position(self, symbol: str, action: str, quantity: int, reason: str):
        """
        Squares off an active position using aggressive limit / market.
        Verifies live broker quantity to prevent position flips / over-closing.
        """
        if self.dry_run:
            logger.info(f"  [DRY-RUN EXIT] {action} {quantity}x {symbol} ({reason})")
            return True

        # Check live broker quantity first
        live_qty = self._get_broker_net_qty(symbol)
        if live_qty is not None:
            if abs(live_qty) == 0.0:
                logger.info(f"  ℹ️ {symbol} is already FLAT on broker (0 qty). Skipping square-off order.")
                return True
            quantity = min(quantity, abs(int(live_qty)))

        payload = {
            "apikey": self.api_key,
            "strategy": STRATEGY_NAME,
            "symbol": symbol,
            "action": action,
            "exchange": EXCHANGE,
            "pricetype": "MARKET",
            "quantity": quantity,
            "product": "NRML",
        }
        try:
            url = f"{self.host}/api/v1/placeorder"
            resp = requests.post(url, json=payload, timeout=5)
            if resp.status_code == 200:
                res = resp.json()
                if res.get("status") == "success":
                    oid = str(res.get("orderid") or "")
                    logger.info(f"  [SQUARE-OFF SENT] {action} {quantity}x {symbol} ({reason}) | OrderId: {oid}")
                    filled, _ = self.wait_for_fill(oid, timeout_sec=5.0)
                    return filled
                else:
                    logger.error(f"  [SQUARE-OFF REJECTED] {symbol}: {res}")
                    return False
            logger.error(f"  [SQUARE-OFF FAILED] HTTP {resp.status_code}: {resp.text}")
            return False
        except Exception as e:
            logger.error(f"  [SQUARE-OFF ERROR] Could not square off {symbol}: {e}")
            return False

    # --------------------------------------------------------------------------
    # Dynamic Position Sizing & Compounding Engine
    # --------------------------------------------------------------------------
    def compute_dynamic_lots(self, asset: str) -> int:
        """
        Computes dynamic contract sizing using portfolio risk allocation:
        - Base capital: Rs 10,000 per asset (or live wallet balance divided by active assets)
        - Margin utilization cap: 65% (preserving 35% free margin buffer)
        - Compounding: As wallet balance grows from trading profits, contract size scales automatically
        - Target: Exactly 92 contracts for BTC ($800 width) and 242 contracts for ETH ($30 width) on Rs 10,000 base
        """
        cfg = ASSET_CONFIGS[asset]
        if not DYNAMIC_SIZING:
            return cfg["default_lots"]

        available_inr = CAPITAL_BASE_INR
        usd_inr = USD_INR_RATE
        try:
            url = f"{self.host}/api/v1/funds"
            resp = requests.post(url, json={"apikey": self.api_key}, timeout=4)
            if resp.status_code == 200:
                funds_data = resp.json().get("data", {})
                live_cash = float(funds_data.get("availablecash", 0.0))
                live_usd = float(funds_data.get("availablecash_usd") or 0.0)
                if live_usd > 0 and live_cash > 0:
                    usd_inr = live_cash / live_usd

                if live_cash >= 5_000_000:
                    # OpenAlgo Sandbox mode (1 Cr virtual) -> use configured base testing capital directly
                    available_inr = CAPITAL_BASE_INR
                    logger.info(f"[{asset}] [SANDBOX FUNDS] Sandbox mode detected. Using base capital: Rs {available_inr:,.2f}")
                elif live_cash > 0:
                    # Live Delta Exchange India account -> allocate live wallet cash among symbols
                    available_inr = live_cash / max(1, len(self.symbols_to_trade))
                    logger.info(f"[{asset}] [LIVE FUNDS] Live Cash allocated ({len(self.symbols_to_trade)} assets): Rs {available_inr:,.2f}")
        except Exception as e:
            logger.warning(f"[{asset}] Funds API check notice: {e}")

        # Margin per contract = Wing Spread Width ($) * Contract Multiplier * USD_INR
        wing_width_usd = cfg["strike_step"] * cfg["wing_width_strikes"]
        margin_per_lot_usd = wing_width_usd * cfg["contract_mult"]
        margin_per_lot_inr = margin_per_lot_usd * usd_inr

        usable_margin_inr = available_inr * MARGIN_UTILIZATION_CAP
        computed_lots = int(usable_margin_inr / margin_per_lot_inr) if margin_per_lot_inr > 0 else cfg["default_lots"]

        # Growth cap to prevent runaway sizing (max 3000 contracts, min 10)
        final_lots = max(10, min(computed_lots, 3000))
        logger.info(
            f"[{asset}] Dynamic Sizing: Capital Base=Rs {available_inr:,.2f} | "
            f"65% Usable=Rs {usable_margin_inr:,.2f} | Margin/Lot=Rs {margin_per_lot_inr:.2f} | "
            f"Final={final_lots} contracts (35% Free Buffer Preserved)"
        )
        return final_lots

    # --------------------------------------------------------------------------
    # 4-Leg Entry Sequence ("Hedge-First")
    # --------------------------------------------------------------------------
    def enter_iron_condor(self, asset: str) -> bool:
        cfg = ASSET_CONFIGS[asset]
        spot = self.get_spot_price(asset)
        if not spot:
            logger.warning(f"[{asset}] Cannot enter condor: Spot LTP unavailable")
            return False

        expiry = self.get_1dte_expiry(asset)
        if not expiry:
            logger.warning(f"[{asset}] Cannot enter condor: No active 1DTE expiry contract found")
            return False

        logger.info(f"\n================================================================================")
        logger.info(f"[{asset}] INITIATING OVERNIGHT CONDOR ENTRY (Spot: ${spot:.2f} | Expiry: {expiry})")
        logger.info(f"================================================================================")

        # 1. Calculate Target Strike Levels
        step = cfg["strike_step"]
        otm_pct = cfg["otm_pct"]
        wing_width = step * cfg["wing_width_strikes"]

        ce_short_strike = round(spot * (1.0 + otm_pct) / step) * step
        pe_short_strike = round(spot * (1.0 - otm_pct) / step) * step
        ce_wing_strike = ce_short_strike + wing_width
        pe_wing_strike = pe_short_strike - wing_width

        lots = self.compute_dynamic_lots(asset)

        # 2. Pre-Flight Liquidity Probe & Strike Hunting
        # Hunt Wings first (Walk inward if far OTM is illiquid)
        ce_wing_sym, ce_wing_k, ce_wing_bid, ce_wing_ask = self.hunt_liquid_strike(
            asset, ce_wing_strike, "CE", "BUY", expiry, is_wing=True, walk_direction=-1
        )
        pe_wing_sym, pe_wing_k, pe_wing_bid, pe_wing_ask = self.hunt_liquid_strike(
            asset, pe_wing_strike, "PE", "BUY", expiry, is_wing=True, walk_direction=1
        )

        if not ce_wing_sym or not pe_wing_sym:
            logger.error(
                f"[{asset}] 🚨 LIQUIDITY GUARD ABORT: Could not acquire verified liquid hedge wings. "
                f"Entry aborted with ZERO naked risk."
            )
            return False

        # Hunt Shorts
        ce_short_sym, ce_short_k, ce_short_bid, ce_short_ask = self.hunt_liquid_strike(
            asset, ce_short_strike, "CE", "SELL", expiry, is_wing=False
        )
        pe_short_sym, pe_short_k, pe_short_bid, pe_short_ask = self.hunt_liquid_strike(
            asset, pe_short_strike, "PE", "SELL", expiry, is_wing=False
        )

        if not ce_short_sym or not pe_short_sym:
            logger.error(f"[{asset}] 🚨 LIQUIDITY GUARD ABORT: Short strikes orderbook spread exceeded threshold.")
            return False

        # Calculate estimated initial net credit
        net_credit_est = (ce_short_bid + pe_short_bid) - (ce_wing_ask + pe_wing_ask)
        if net_credit_est <= 0.0:
            logger.warning(f"[{asset}] Entry aborted: Estimated net credit is negative (${net_credit_est:.2f}).")
            return False

        logger.info(f"[{asset}] STRIKE AUDIT PASSED:")
        logger.info(f"  BUY Call Wing : {ce_wing_sym}  (Ask: ${ce_wing_ask:.2f})")
        logger.info(f"  BUY Put Wing  : {pe_wing_sym}  (Ask: ${pe_wing_ask:.2f})")
        logger.info(f"  SELL Short Call: {ce_short_sym} (Bid: ${ce_short_bid:.2f})")
        logger.info(f"  SELL Short Put : {pe_short_sym} (Bid: ${pe_short_bid:.2f})")
        logger.info(f"  Est. Net Credit: ${net_credit_est:.2f} per unit")

        # 3. EXECUTION: STEP 1 - BUY WINGS FIRST (At Ask price for guaranteed fill)
        logger.info(f"[{asset}] Step 1/2: Submitting BUY orders for protective wings at Ask...")
        cw_ok, cw_id = self.place_pegged_limit_order(ce_wing_sym, "BUY", lots, ce_wing_bid, ce_wing_ask, is_wing=True)
        pw_ok, pw_id = self.place_pegged_limit_order(pe_wing_sym, "BUY", lots, pe_wing_bid, pe_wing_ask, is_wing=True)

        if not (cw_ok and pw_ok):
            logger.error(f"[{asset}] 🚨 WING PLACEMENT FAILED! Aborting condor. Zero short risk.")
            if cw_id:
                self.cancel_order(cw_id)
            if pw_id:
                self.cancel_order(pw_id)
            return False

        # Wait for fill confirmation on BOTH wings before touching shorts
        cw_filled, cw_fill_px = self.wait_for_fill(cw_id, timeout_sec=8.0)
        pw_filled, pw_fill_px = self.wait_for_fill(pw_id, timeout_sec=8.0)

        if not (cw_filled and pw_filled):
            logger.error(f"[{asset}] 🚨 WING FILL FAILED! Wings did not fill. Aborting condor. ZERO naked short risk.")
            if cw_filled:
                self.square_off_position(ce_wing_sym, "SELL", lots, "Wing Unwind on Abort")
            if pw_filled:
                self.square_off_position(pe_wing_sym, "SELL", lots, "Wing Unwind on Abort")
            return False

        # 4. EXECUTION: STEP 2 - SELL SHORTS (Only after wings are 100% FILLED)
        logger.info(f"[{asset}] Step 2/2: Wings 100% filled and secured. Submitting SELL orders for short legs...")
        cs_ok, cs_id = self.place_pegged_limit_order(ce_short_sym, "SELL", lots, ce_short_bid, ce_short_ask, is_wing=False)
        ps_ok, ps_id = self.place_pegged_limit_order(pe_short_sym, "SELL", lots, pe_short_bid, pe_short_ask, is_wing=False)

        if not (cs_ok and ps_ok):
            logger.error(f"[{asset}] 🚨 SHORT PLACEMENT FAILED! Unwinding wings immediately for safety.")
            self.square_off_position(ce_wing_sym, "SELL", lots, "Emergency Basket Exit")
            self.square_off_position(pe_wing_sym, "SELL", lots, "Emergency Basket Exit")
            if cs_id:
                self.cancel_order(cs_id)
            if ps_id:
                self.cancel_order(ps_id)
            return False

        cs_filled, cs_fill_px = self.wait_for_fill(cs_id, timeout_sec=8.0)
        ps_filled, ps_fill_px = self.wait_for_fill(ps_id, timeout_sec=8.0)

        if not (cs_filled and ps_filled):
            logger.error(f"[{asset}] 🚨 SHORT FILL FAILED! Squaring off filled shorts first, then wings for safety.")
            if cs_filled:
                self.square_off_position(ce_short_sym, "BUY", lots, "Emergency Basket Exit")
            if ps_filled:
                self.square_off_position(pe_short_sym, "BUY", lots, "Emergency Basket Exit")
            self.square_off_position(ce_wing_sym, "SELL", lots, "Emergency Basket Exit")
            self.square_off_position(pe_wing_sym, "SELL", lots, "Emergency Basket Exit")
            return False

        # 5. Record State
        total_initial_credit_usd = net_credit_est * cfg["contract_mult"] * lots
        target_profit_usd = total_initial_credit_usd * TARGET_DECAY_PCT

        self.state_data["assets"][asset] = {
            "active": True,
            "entry_done": True,
            "expiry_date": expiry,
            "entry_time": get_current_ist_datetime().isoformat(),
            "lots": lots,
            "initial_net_credit": round(total_initial_credit_usd, 2),
            "target_profit_value": round(target_profit_usd, 2),
            "positions": {
                "CE_WING": {"symbol": ce_wing_sym, "type": "BUY", "fill_price": ce_wing_ask, "active": True},
                "PE_WING": {"symbol": pe_wing_sym, "type": "BUY", "fill_price": pe_wing_ask, "active": True},
                "CE_SHORT": {
                    "symbol": ce_short_sym,
                    "type": "SELL",
                    "fill_price": ce_short_bid,
                    "sl_price": round(ce_short_bid * SL_MULTIPLIER, 2),
                    "active": True,
                },
                "PE_SHORT": {
                    "symbol": pe_short_sym,
                    "type": "SELL",
                    "fill_price": pe_short_bid,
                    "sl_price": round(pe_short_bid * SL_MULTIPLIER, 2),
                    "active": True,
                },
            }
        }
        self._save_state()
        logger.info(
            f"[{asset}] ✅ CONDOR POSITION ACTIVATED | Net Credit: ${total_initial_credit_usd:.2f} | "
            f"TP (50% Decay): +${target_profit_usd:.2f} | Cutoff: 06:30 AM IST"
        )
        return True

    # --------------------------------------------------------------------------
    # Position Monitoring & Dual Exit Engine (50% TP & 06:30 Hard Cutoff)
    # --------------------------------------------------------------------------
    def monitor_positions(self):
        now_ist = get_current_ist_datetime()
        curr_time = now_ist.time()

        is_morning_cutoff = curr_time >= MORNING_CUTOFF_TIME and curr_time < ENTRY_TIME_START

        for asset in self.symbols_to_trade:
            a_state = self.state_data["assets"].get(asset, {})
            if not a_state.get("active"):
                continue

            # Square-off retry guard: If previous square-off attempt had failed legs, continuously retry
            if a_state.get("_squaring_off"):
                logger.info(f"[{asset}] 🔁 [SQUARE-OFF IN PROGRESS] Retrying unclosed legs until confirmed flat...")
                self._square_off_entire_condor(asset, a_state.get("_square_off_reason", "RETRY_UNCLOSED_LEGS"))
                continue

            cfg = ASSET_CONFIGS[asset]
            positions = a_state.get("positions", {})
            lots = a_state.get("lots", cfg["default_lots"])
            mult = cfg["contract_mult"]

            # Exit Rule 1: 06:30 AM IST Hard Cutoff Exit
            if is_morning_cutoff:
                logger.info(f"[{asset}] ⏰ MORNING HARD CUTOFF (06:30 AM IST REACHED) - Squaring off all positions...")
                self._square_off_entire_condor(asset, "Morning_0630_Cutoff")
                continue

            # Fetch live prices for active legs
            leg_prices = {}
            for leg_key, leg_data in positions.items():
                if leg_data.get("active"):
                    bid, ask, _, _ = self.get_l2_depth(leg_data["symbol"])
                    leg_prices[leg_key] = (bid, ask)

            ce_short_data = positions.get("CE_SHORT", {})
            pe_short_data = positions.get("PE_SHORT", {})

            # Exit Rule 2: 2.0x Per-Leg Stop Loss on Shorts
            if ce_short_data.get("active") and "CE_SHORT" in leg_prices:
                _, cs_ask = leg_prices["CE_SHORT"]
                if cs_ask and cs_ask >= ce_short_data["sl_price"]:
                    logger.warning(
                        f"[{asset}] 🛑 CE SHORT STOP LOSS HIT! Live Ask: ${cs_ask:.2f} >= SL: ${ce_short_data['sl_price']:.2f}"
                    )
                    self._square_off_entire_condor(asset, "CE_Short_SL_Triggered")
                    continue

            if pe_short_data.get("active") and "PE_SHORT" in leg_prices:
                _, ps_ask = leg_prices["PE_SHORT"]
                if ps_ask and ps_ask >= pe_short_data["sl_price"]:
                    logger.warning(
                        f"[{asset}] 🛑 PE SHORT STOP LOSS HIT! Live Ask: ${ps_ask:.2f} >= SL: ${pe_short_data['sl_price']:.2f}"
                    )
                    self._square_off_entire_condor(asset, "PE_Short_SL_Triggered")
                    continue

            # Exit Rule 3: 50% Decay Take Profit Target
            # Current basket cost to close = (Shorts Ask) - (Wings Bid)
            if all(k in leg_prices for k in ["CE_SHORT", "PE_SHORT", "CE_WING", "PE_WING"]):
                cs_bid, cs_ask = leg_prices["CE_SHORT"]
                ps_bid, ps_ask = leg_prices["PE_SHORT"]
                cw_bid, cw_ask = leg_prices["CE_WING"]
                pw_bid, pw_ask = leg_prices["PE_WING"]

                if cs_ask and ps_ask and cw_bid and pw_bid:
                    current_cost_per_unit = (cs_ask + ps_ask) - (cw_bid + pw_bid)
                    current_basket_value_usd = current_cost_per_unit * mult * lots
                    initial_credit = a_state.get("initial_net_credit", 0.0)
                    accumulated_profit = initial_credit - current_basket_value_usd
                    target_profit = a_state.get("target_profit_value", initial_credit * TARGET_DECAY_PCT)

                    if accumulated_profit >= target_profit:
                        logger.info(
                            f"[{asset}] 🎯 TAKE PROFIT REACHED (50% DECAY): Current Profit: +${accumulated_profit:.2f} >= Target: +${target_profit:.2f}"
                        )
                        self._square_off_entire_condor(asset, "50%_Decay_TP_Achieved")
                        continue

    def _square_off_entire_condor(self, asset: str, reason: str):
        a_state = self.state_data["assets"][asset]
        positions = a_state.get("positions", {})
        lots = a_state.get("lots", ASSET_CONFIGS[asset]["default_lots"])
        a_state["_square_off_reason"] = reason

        # Step 1: Unwind short legs first to eliminate risk
        for leg_key in ["CE_SHORT", "PE_SHORT"]:
            leg = positions.get(leg_key, {})
            if leg.get("active"):
                leg["exit_attempted"] = True
                ok = self.square_off_position(leg["symbol"], "BUY", lots, reason)
                if ok:
                    leg["active"] = False

        # Step 2: Unwind long wing legs second
        for leg_key in ["CE_WING", "PE_WING"]:
            leg = positions.get(leg_key, {})
            if leg.get("active"):
                leg["exit_attempted"] = True
                ok = self.square_off_position(leg["symbol"], "SELL", lots, reason)
                if ok:
                    leg["active"] = False

        any_active = any(leg.get("active") for leg in positions.values())
        if not any_active:
            a_state["active"] = False
            a_state["_squaring_off"] = False
            a_state["exit_time"] = get_current_ist_datetime().isoformat()
            a_state["exit_reason"] = reason
            self._save_state()
            logger.info(f"[{asset}] ✅ ENTIRE CONDOR CLOSED ({reason})")
        else:
            a_state["_squaring_off"] = True
            self._save_state()
            logger.warning(f"[{asset}] ⚠️ Some legs failed to square off ({reason}). Retrying on next cycle.")

    # --------------------------------------------------------------------------
    # Main Strategy Execution Loop
    # --------------------------------------------------------------------------
    def run(self):
        logger.info(f"Starting {STRATEGY_NAME} Engine on Port 5001...")
        logger.info(f"Symbols: {self.symbols_to_trade} | Mode: {'DRY-RUN' if self.dry_run else 'LIVE'}")
        logger.info(f"Schedule: Entry Window: 22:00 - 04:30 IST | Morning Cutoff: 06:30 AM IST")
        if self.force_entry:
            logger.info("⚡ [FORCE ENTRY ACTIVE] Strategy will enter immediately regardless of time window.")
            for asset in self.symbols_to_trade:
                if not self.state_data["assets"][asset]["active"]:
                    self.state_data["assets"][asset]["entry_done"] = False

        logged_completed = set()

        # Audit and reconcile broker positions immediately upon startup/restart
        self.audit_and_recover_positions_on_startup()

        # Guard: If launched outside overnight window without active positions or force_entry
        curr_time = get_current_ist_time()
        is_entry_time = (curr_time >= ENTRY_TIME_START) or (curr_time <= ENTRY_TIME_END)
        if not is_entry_time and not self.force_entry:
            any_active = any(self.state_data["assets"][a].get("active") for a in self.symbols_to_trade)
            if not any_active:
                logger.info("=" * 80)
                logger.info(f"⏰ Current time ({curr_time.strftime('%H:%M')} IST) is outside overnight window (22:00 - 04:30 IST) and no active positions.")
                logger.info("Overnight session closed. Exiting cleanly. Next overnight cycle opens at 22:00 IST.")
                logger.info("=" * 80)
                return

        while self.running:
            try:
                # 0. Reconcile active positions with live broker positionbook
                self.reconcile_positions_with_broker()

                curr_time = get_current_ist_time()

                # Check Entry Window (Overnight: 22:00 to 04:30 IST)
                is_entry_time = (curr_time >= ENTRY_TIME_START) or (curr_time <= ENTRY_TIME_END)
                if is_entry_time or self.force_entry:
                    for asset in self.symbols_to_trade:
                        a_state = self.state_data["assets"].get(asset, {})
                        if not a_state.get("active") and not a_state.get("entry_done"):
                            self.enter_iron_condor(asset)
                        elif not a_state.get("active") and a_state.get("entry_done"):
                            if asset not in logged_completed:
                                logger.info(
                                    f"[{asset}] Overnight cycle already completed for today ({a_state.get('exit_reason')}). "
                                    f"Next cycle opens at 22:00 IST."
                                )
                                logged_completed.add(asset)

                # Monitor active positions (squares off on 06:30 AM cutoff or SL/TP)
                self.monitor_positions()

                # Hard morning exit at 06:30 AM IST (Clean handover to daytime strategies)
                is_past_morning_cutoff = (curr_time >= MORNING_CUTOFF_TIME) and (curr_time < ENTRY_TIME_START)
                if is_past_morning_cutoff and not self.force_entry:
                    any_active = any(self.state_data["assets"][a].get("active") for a in self.symbols_to_trade)
                    if not any_active:
                        logger.info("=" * 80)
                        logger.info("⏰ [06:30 AM IST MORNING CUTOFF] All overnight condor positions closed.")
                        logger.info("Overnight session complete. Exiting cleanly to hand over to daytime strategies.")
                        logger.info("=" * 80)
                        self.running = False
                        break

                # Reset entry_done flag for the next overnight cycle (after 18:00 IST)
                if curr_time >= datetime.strptime("18:00", "%H:%M").time() and curr_time < datetime.strptime("21:00", "%H:%M").time():
                    for asset in self.symbols_to_trade:
                        if not self.state_data["assets"][asset]["active"]:
                            self.state_data["assets"][asset]["entry_done"] = False

                time.sleep(5)
            except KeyboardInterrupt:
                logger.info("Shutdown signal received. Preserving state.")
                self._save_state()
                break
            except Exception as e:
                logger.error(f"Error in main loop: {e}", exc_info=True)
                time.sleep(5)


# ------------------------------------------------------------------------------
# 4. CLI ENTRY POINT & SIGNAL HANDLING
# ------------------------------------------------------------------------------
def parse_arguments():
    parser = argparse.ArgumentParser(description="Overnight Crypto Delta Options Strategy")
    parser.add_argument(
        "--symbol",
        type=str,
        default=os.getenv("CRYPTO_OVERNIGHT_ASSET", "BOTH").upper(),
        choices=["BTC", "ETH", "BOTH"],
        help="Target crypto asset to trade (BTC, ETH, or BOTH)",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default="live",
        choices=["live", "dry_run"],
        help="Execution mode (live or dry_run)",
    )
    parser.add_argument("--lots", type=int, default=None, help="Override default contract lots")
    parser.add_argument("--sl-mult", type=float, default=2.0, help="Stop loss multiplier on short legs (default: 2.0x)")
    parser.add_argument("--tp-pct", type=float, default=0.50, help="Take profit decay target (default: 0.50 = 50%)")
    parser.add_argument(
        "--force-entry",
        action="store_true",
        help="Force immediate entry upon startup without waiting for entry time window",
    )
    return parser.parse_args()

def main():
    args = parse_arguments()
    dry_run = args.mode == "dry_run"

    if args.symbol == "BOTH":
        symbols = ["BTC", "ETH"]
    else:
        symbols = [args.symbol]

    if args.lots:
        for s in symbols:
            ASSET_CONFIGS[s]["default_lots"] = args.lots

    global SL_MULTIPLIER, TARGET_DECAY_PCT
    SL_MULTIPLIER = args.sl_mult
    TARGET_DECAY_PCT = args.tp_pct

    engine = OvernightCryptoOptionsEngine(
        symbols_to_trade=symbols,
        dry_run=dry_run,
        force_entry=args.force_entry
    )

    def sig_handler(signum, frame):
        logger.info(f"Signal {signum} received. Safely halting...")
        engine.running = False

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    engine.run()

if __name__ == "__main__":
    main()
