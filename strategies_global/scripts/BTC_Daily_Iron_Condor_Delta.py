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
Risk Management  : 1.5x Premium Stop Loss on Short Legs | 85% Decay Target | 17:15 IST EOD Exit
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
import re
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple, Set

import requests

try:
    from dotenv import load_dotenv
    for p in (Path("/app/.env"), Path(__file__).resolve().parent.parent.parent / ".env.global", Path(".env")):
        if p.exists():
            load_dotenv(p)
            break
except Exception:
    pass

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


def acquire_singleton_lock() -> Any:
    """Acquire exclusive non-blocking file lock to prevent duplicate strategy instances."""
    lock_file = Path(__file__).resolve().parent.parent / "data" / "btc_daily_iron_condor.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    try:
        f = open(lock_file, "w")
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except Exception:
        logger.warning("🚨 [SINGLETON GUARD] Another instance of BTC_Daily_Iron_Condor is already running! Exiting immediately.")
        sys.exit(0)


logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("BTC_Daily_Iron_Condor")

# ------------------------------------------------------------------------------
# 0b. ALERTING INFRASTRUCTURE (Telegram / Slack / PagerDuty)
# ------------------------------------------------------------------------------
ALERT_COOLDOWN_SEC = int(os.getenv("BTC_IC_ALERT_COOLDOWN_SEC", "300"))
_last_alert_ts: Dict[str, float] = {}
_alert_lock = threading.Lock()


def send_alert(message: str, level: str = "warning") -> None:
    """
    Send alert to configured channels. Rate-limited by alert text hash to avoid spam.
    Reads from env:
      - TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID
      - SLACK_WEBHOOK_URL
      - PAGERDUTY_ROUTING_KEY
    """
    now = time.time()
    alert_key = f"{level}:{message[:120]}"
    with _alert_lock:
        last = _last_alert_ts.get(alert_key, 0.0)
        if now - last < ALERT_COOLDOWN_SEC:
            return
        _last_alert_ts[alert_key] = now

    logger.warning(f"🚨 [ALERT {level.upper()}] {message}")

    # Telegram
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID")
    if tg_token and tg_chat:
        try:
            requests.post(
                f"https://api.telegram.org/bot{tg_token}/sendMessage",
                json={"chat_id": tg_chat, "text": f"[{level.upper()}] BTC IC Bot\n{message}"},
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Telegram alert failed: {e}")

    # Slack
    slack_url = os.getenv("SLACK_WEBHOOK_URL")
    if slack_url:
        try:
            requests.post(
                slack_url,
                json={"text": f"[{level.upper()}] BTC IC Bot: {message}"},
                timeout=5,
            )
        except Exception as e:
            logger.error(f"Slack alert failed: {e}")

    # PagerDuty
    pd_key = os.getenv("PAGERDUTY_ROUTING_KEY")
    if pd_key:
        try:
            requests.post(
                "https://events.pagerduty.com/v2/enqueue",
                json={
                    "routing_key": pd_key,
                    "event_action": "trigger",
                    "payload": {
                        "summary": f"BTC Daily Iron Condor: {message}",
                        "severity": "critical" if level == "critical" else "warning",
                        "source": STRATEGY_NAME,
                    },
                },
                timeout=5,
            )
        except Exception as e:
            logger.error(f"PagerDuty alert failed: {e}")


# ------------------------------------------------------------------------------
# 0c. MARKET DATA / CONTRACT SPECS / FX HELPERS
# ------------------------------------------------------------------------------
_contract_specs_cache: Optional[Dict[str, Any]] = None
_usd_inr_cache: Optional[Tuple[float, float]] = None  # (rate, timestamp)


def fetch_usd_inr_rate() -> float:
    """Fetch live USD/INR rate; prioritize configured USD_INR_RATE if explicitly provided."""
    global _usd_inr_cache
    now = time.time()
    if _usd_inr_cache and (now - _usd_inr_cache[1]) < 3600:
        return _usd_inr_cache[0]

    env_rate = os.getenv("USD_INR_RATE")
    if env_rate:
        try:
            rate = float(env_rate)
            if rate > 0:
                _usd_inr_cache = (rate, now)
                return rate
        except ValueError:
            pass

    fallback = 88.0

    endpoints = [
        "https://api.exchangerate-api.com/v4/latest/USD",
        "https://open.er-api.com/v6/latest/USD",
    ]
    for url in endpoints:
        try:
            resp = requests.get(url, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                rate = float(data.get("rates", {}).get("INR", 0.0))
                if rate > 0:
                    _usd_inr_cache = (rate, now)
                    logger.info(f"[LIVE FX] USD/INR fetched from {url.split('/')[2]}: {rate:.4f}")
                    return rate
        except Exception as e:
            logger.debug(f"FX fetch from {url} failed: {e}")

    send_alert(
        f"USD/INR live feed unavailable. Using fallback rate {fallback:.2f}. "
        "Please verify CAPITAL_BASE_INR and margin calculations manually.",
        level="warning",
    )
    return fallback


def fetch_contract_specs(host: str, api_key: str) -> Dict[str, Any]:
    """
    Returns BTC option contract multiplier.
    On Delta Exchange, 1 BTC option contract is strictly 0.001 BTC.
    """
    return {"multiplier": 0.001, "source": "DELTA_EXCHANGE_BTC_OPTIONS_SPEC", "underlying": "BTC"}


# ------------------------------------------------------------------------------
# 0e. RATE LIMITER & MULTI-TICK SL CONFIRMER (P0/P2 FIXES)
# ------------------------------------------------------------------------------
class RateLimiter:
    """Token-bucket rate limiter to protect OpenAlgo gateway and prevent HTTP 429."""
    def __init__(self, rate_per_sec: float = 8.0, burst: int = 16):
        self.rate = rate_per_sec
        self.capacity = burst
        self.tokens = float(burst)
        self.updated = time.monotonic()

    def acquire(self) -> None:
        now = time.monotonic()
        elapsed = now - self.updated
        self.updated = now
        self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
        if self.tokens >= 1.0:
            self.tokens -= 1.0
            return
        sleep_s = (1.0 - self.tokens) / self.rate
        time.sleep(max(0.0, sleep_s))
        self.tokens = 0.0


class SLConfirmer:
    """N-tick confirmation with spread and freshness guards to eliminate single-tick flash wicks."""
    def __init__(self, need: int = 2, window_s: float = 60.0, max_spread: float = 0.40, max_age: float = 5.0):
        self.need = need
        self.window_s = window_s
        self.max_spread = max_spread
        self.max_age = max_age
        self.ticks: Dict[str, List[Dict[str, Any]]] = {}

    def add_tick(self, symbol: str, ask: float, spread: Optional[float] = None, size: int = 100, age: float = 0.5) -> None:
        if symbol not in self.ticks:
            self.ticks[symbol] = []
        t = self.ticks[symbol]
        t.append({
            "ts": time.time(),
            "ask": ask,
            "spread": spread if spread is not None else 0.05,
            "size": size,
            "age": age,
        })
        while len(t) > 8:
            t.pop(0)

    def triggered(self, symbol: str, sl: float) -> Tuple[bool, str]:
        if sl <= 0:
            return False, "NO_SL"
        t = self.ticks.get(symbol, [])
        if len(t) < self.need:
            return False, "NEED_MORE_TICKS"
        now = time.time()
        recent = [x for x in t if now - x["ts"] <= self.window_s][-self.need:]
        if len(recent) < self.need:
            return False, "WINDOW_SHORT"
        for x in recent:
            if x["ask"] < sl:
                return False, "BELOW_SL"
            if x["spread"] is not None and x["spread"] > self.max_spread:
                return False, "SPREAD_WIDE"
            if x["size"] <= 0:
                return False, "ZERO_SIZE"
            if x["age"] > self.max_age:
                return False, "STALE_QUOTE"
        return True, f"{self.need}x_CONFIRMED"


# ------------------------------------------------------------------------------
# 1. PARAMETERS & CONFIGURATION
# ------------------------------------------------------------------------------
STRATEGY_NAME = "BTC_Daily_Iron_Condor"
EXCHANGE = "CRYPTO"
UNDERLYING = "BTC"

DEFAULT_LOTS = int(os.getenv("BTC_IC_LOTS", "92"))             # 92 contracts = 0.092 BTC (~$7,500 notional)
DYNAMIC_SIZING = os.getenv("BTC_IC_DYNAMIC_SIZING", "True").lower() in ("true", "1", "yes")
CAPITAL_BASE_INR = float(os.getenv("CAPITAL_BASE_INR", os.getenv("BTC_IC_CAPITAL_INR", "20000.0")))
MARGIN_UTILIZATION_CAP = float(os.getenv("BTC_IC_MARGIN_CAP", "0.65")) # 65% margin cap (35% free buffer)
USD_INR_RATE = float(os.getenv("USD_INR_RATE", "88.0"))
MAX_DAILY_LOSS_INR = float(os.getenv("MAX_DAILY_LOSS_INR", "4500.0")) # Rs 4,500 circuit breaker
# Architecture A2 Parameters (Dynamic OTM Hedged Iron Condor: 1.5% -> 1.1% -> 0.8%)
OTM_PCT = float(os.getenv("BTC_IC_OTM_PCT", "0.015"))            # Starts at 1.5% OTM, steps down dynamically if illiquid
SPREAD_WIDTH = float(os.getenv("BTC_IC_SPREAD_WIDTH", "800.0"))   # $800 wing spread width


def compute_dynamic_sl_mult(otm_pct: float) -> float:
    """Option 1 Dynamic SL Scaling based on OTM distance:
    >= 1.8% OTM: 1.5x SL (room for chop built into low delta)
    1.3% - 1.7% OTM: 1.8x SL
    1.0% - 1.2% OTM: 2.0x SL (absorbs peak gamma fluctuations)
    Can be overridden if BTC_IC_SL_MULT environment variable is explicitly provided.
    """
    override = os.getenv("BTC_IC_SL_MULT")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    if otm_pct >= 0.018:
        return 1.5
    elif otm_pct >= 0.013:
        return 1.8
    else:
        return 2.0


SL_MULTIPLIER = compute_dynamic_sl_mult(OTM_PCT)        # Dynamic SL based on OTM distance (Option 1)
TARGET_DECAY_PCT = float(os.getenv("BTC_IC_TARGET_PCT", "0.75")) # 75% decay target
ENABLE_BASKET_SL = os.getenv("BTC_ENABLE_BASKET_SL", "true").lower() in ("true", "1", "yes")
BASKET_SL_MULT = float(os.getenv("BTC_BASKET_SL_MULT", "1.0"))
DISABLE_LEG_SL = os.getenv("BTC_DISABLE_LEG_SL", "false").lower() in ("true", "1", "yes")
MIN_PREMIUM_THRESHOLD = float(os.getenv("BTC_IC_MIN_PREMIUM", "100.0")) # $100.0 minimum premium for shorts in Session 1
MAX_SPREAD_PCT = float(os.getenv("BTC_IC_MAX_SPREAD", "0.05"))   # 5% max bid-ask spread

# Architecture B3 Parameters (Dynamic ATM Straddle with 30% SL)
STRADDLE_SL_PCT = float(os.getenv("BTC_STRADDLE_SL_PCT", "0.30")) # 30% per-leg stop loss
STRATEGY_MODE = os.getenv("BTC_STRATEGY_MODE", "condor").lower()  # "condor" (Arch A2) or "straddle" (Arch B3)

ENTRY_TIME_START = datetime.strptime(os.getenv("BTC_IC_ENTRY_START", "06:45"), "%H:%M").time()    # 06:45 IST
ENTRY_TIME_END = datetime.strptime(os.getenv("BTC_IC_ENTRY_END", "16:30"), "%H:%M").time()        # 16:30 IST (Extended for afternoon session)
REENTRY_CUTOFF_TIME = datetime.strptime(os.getenv("BTC_REENTRY_CUTOFF", "16:30"), "%H:%M").time() # Cutoff for Session 2/3
MAX_DAILY_SESSIONS = int(os.getenv("BTC_MAX_SESSIONS", "3"))        # Max 3 sessions per day
EOD_EXIT_TIME = datetime.strptime("17:15", "%H:%M").time()       # 15 mins before 17:30 IST expiry


def get_state_file_path(dry_run: bool = False) -> Path:
    base_dir = Path(__file__).resolve().parent.parent / "data"
    base_dir.mkdir(parents=True, exist_ok=True)
    today_str = get_current_ist_datetime().strftime("%Y%m%d")
    suffix = "_dryrun" if dry_run else ""
    return base_dir / f"btc_daily_iron_condor_{today_str}{suffix}.json"


# ------------------------------------------------------------------------------
# 2. CLIENT & HOST RESOLUTION (DUAL-INSTANCE INVARIANT: PORT 5001)
# ------------------------------------------------------------------------------
def resolve_crypto_host_and_key() -> Tuple[str, str]:
    default_host = "http://127.0.0.1:5000" if os.path.exists("/.dockerenv") else "http://127.0.0.1:5001"
    host = os.getenv("OPENALGO_HOST_CRYPTO") or os.getenv("OPENALGO_HOST", default_host)
    api_key = (
        os.getenv("OPENALGO_API_KEY_CRYPTO")
        or os.getenv("OPENALGO_API_KEY")
        or ""
    )
    if not api_key:
        raise ValueError("Missing OPENALGO_API_KEY_CRYPTO / OPENALGO_API_KEY. Refusing to run without explicit credentials.")
    return host.rstrip("/"), api_key


# ------------------------------------------------------------------------------
# 2b. SYMBOL / STRIKE PARSING UTILITIES
# ------------------------------------------------------------------------------
def parse_strike_from_symbol(symbol: str) -> Optional[float]:
    """
    Extract numeric strike from Delta Exchange / OpenAlgo option symbol.
    Handles common formats:
      BTC21SEP2681200CE or BTC-21SEP26-81200-CE (Delta standard)
      BTC24102470000CE (YYMMDD)
      C-BTC-70000-241024 (Deribit style)
      BTC_70000_CE_241024
      BTC-24OCT24-70000-CE
    """
    if not symbol:
        return None
    s = symbol.upper().strip()

    # 1. Delta Exchange Standard format: BTC21SEP2681200CE or BTC-21SEP26-81200-CE
    m = re.match(r"^([A-Z]+)[-_]?(\d{1,2}[A-Z]{3}\d{2})[-_]?(\d+)[-_]?(CE|PE)$", s)
    if m:
        return float(m.group(3))

    # 2. YYMMDD format: BTC24102470000CE or BTC-241024-70000-CE
    m = re.match(r"^([A-Z]+)[-_]?(\d{6})[-_]?(\d+)[-_]?(CE|PE)$", s)
    if m:
        return float(m.group(3))

    # 3. Deribit / Inverse format: C-BTC-70000-241024 or P-BTC-70000-241024
    m = re.match(r"^[CP]-([A-Z]+)-(\d+)-(\d{6})$", s)
    if m:
        return float(m.group(2))

    # 4. Underscore format: BTC_70000_CE_241024
    m = re.match(r"^([A-Z]+)_(\d+)_(CE|PE)", s)
    if m:
        return float(m.group(2))

    # 5. Format: BTC-24OCT24-70000-CE (split by hyphen)
    MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
    parts = s.split("-")
    for part in parts:
        if part.isdigit() and 5 <= len(part) <= 8:
            return float(part)
        clean = part
        for mo in MONTHS:
            clean = clean.replace(mo, "")
        if clean.isdigit() and 5 <= len(clean) <= 8:
            return float(clean)

    # 6. Fallback: contiguous 5-8 digit block immediately before CE/PE (no preceding year digits)
    match = re.search(r"(?:^|[A-Z_-])(\d{5,8})(CE|PE)\b", s)
    if match:
        return float(match.group(1))

    return None


def option_type_from_symbol(symbol: str) -> Optional[str]:
    s = symbol.upper()
    if "CE" in s:
        return "CE"
    if "PE" in s:
        return "PE"
    return None


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

        # Load contract specs and FX rate at startup
        self.contract_specs = fetch_contract_specs(host, api_key)
        self.multiplier = 0.001  # Delta Exchange BTC options multiplier: 1 contract = 0.001 BTC
        self.usd_inr_rate = fetch_usd_inr_rate()

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
            self.sl_multiplier = compute_dynamic_sl_mult(self.otm_pct)
            self.straddle_sl_pct = 0.0
            self.min_premium = MIN_PREMIUM_THRESHOLD
            self.max_spread_pct = MAX_SPREAD_PCT
            self.target_decay_pct = TARGET_DECAY_PCT
            logger.info(f"🛡️ [STRATEGY MODE] Architecture A2: Delta 15-20 Hedged Iron Condor ({self.otm_pct*100:.1f}% OTM, {self.sl_multiplier:.1f}x Dynamic SL)")

        # State tracking
        self.current_session = 1
        self.session_history: List[Dict[str, Any]] = []
        self.current_date = get_current_ist_datetime().strftime("%Y-%m-%d")
        self.trade_taken_today = False
        self.trade_active = False
        self.positions: Dict[str, Dict[str, Any]] = {}
        self.net_credit_collected = 0.0
        self.enable_basket_sl = ENABLE_BASKET_SL
        self.basket_sl_mult = BASKET_SL_MULT
        self.disable_leg_sl = DISABLE_LEG_SL
        self.target_profit = 0.0
        self.expiry_date: Optional[str] = None
        self.entry_time: Optional[str] = None
        self.entry_attempts = 0
        self.last_attempt_time = 0.0
        self.last_cooldown_log = 0.0
        self.last_attempt_log = 0.0
        self.max_entry_attempts = int(os.getenv("BTC_IC_MAX_ATTEMPTS", "3"))
        self.retry_cooldown_sec = int(os.getenv("BTC_IC_RETRY_COOLDOWN", "180"))
        self.batch_cooldown_sec = int(os.getenv("BTC_IC_BATCH_COOLDOWN", "900"))  # 15 minutes cooldown after max attempts

        # Rate Limiter & Multi-Tick SL Confirmer (P0/P2 improvements)
        self.limiter = RateLimiter(rate_per_sec=8.0, burst=16)
        confirm_ticks = int(os.getenv("BTC_SL_CONFIRM_TICKS", "1" if dry_run or "pytest" in sys.modules else "2"))
        confirm_window = float(os.getenv("BTC_SL_CONFIRM_WINDOW_S", "60.0"))
        self.sl_confirmer = SLConfirmer(need=confirm_ticks, window_s=confirm_window, max_spread=0.40, max_age=5.0)
        self._stop_requested = False

        self._load_state()

        if self.force:
            logger.info("⚡ [--force flag active] Overriding prior state: resetting trade_taken_today and attempt counters.")
            self.trade_taken_today = False
            self.entry_attempts = 0
            self.last_attempt_time = 0.0
            self.current_session = 1
            self.session_history = []
            self._save_state()

        # NOTE: Startup audit is intentionally invoked only in run(), not here,
        # to avoid duplicate reconciliation on every launch.

    def _load_state(self):
        state_path = get_state_file_path(dry_run=self.dry_run)
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
                f"Attempts: {self.entry_attempts} | Legs: {len(self.positions)} | "
                f"Stuck: {sum(1 for p in self.positions.values() if p.get('status') == 'STUCK')}"
            )
        except Exception as e:
            logger.error(f"[STATE LOAD ERROR] {e}")

    def _save_state(self):
        state_path = get_state_file_path(dry_run=self.dry_run)
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

    # --------------------------------------------------------------------------
    # STRATEGY-SCOPED POSITION DISCOVERY
    # --------------------------------------------------------------------------
    def _get_strategy_symbols_from_orderbook(self) -> Set[str]:
        """
        Fetch today's orderbook filtered by strategy tag to determine which
        symbols legitimately belong to this strategy. This prevents cross-strategy
        interference when the same API key is shared.
        """
        symbols: Set[str] = set()
        if self.dry_run:
            return symbols
        try:
            url = f"{self.host}/api/v1/orderbook"
            resp = requests.post(url, json={"apikey": self.api_key}, timeout=5)
            if resp.status_code != 200:
                logger.warning(f"[ORDERBOOK FILTER] HTTP {resp.status_code}: {resp.text}")
                return symbols
            data = resp.json().get("data", {})
            orders = data.get("orders", []) if isinstance(data, dict) else []
            if not isinstance(orders, list):
                return symbols
            today_str = get_current_ist_datetime().strftime("%Y-%m-%d")
            for o in orders:
                if not isinstance(o, dict):
                    continue
                # Filter by strategy tag client-side
                strat = str(o.get("strategy") or "").strip()
                if strat and strat != STRATEGY_NAME:
                    continue
                sym = str(o.get("symbol") or o.get("tradingsymbol") or "").strip()
                if not sym:
                    continue
                # Only consider orders placed today
                ts = o.get("order_timestamp") or o.get("timestamp") or o.get("exch_time")
                if ts:
                    try:
                        order_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                        if order_dt.astimezone(IST_TZ).strftime("%Y-%m-%d") != today_str:
                            continue
                    except Exception:
                        pass
                symbols.add(sym)
        except Exception as e:
            logger.warning(f"[ORDERBOOK FILTER] Failed to fetch strategy orderbook: {e}")
        return symbols

    def audit_and_recover_positions_on_startup(self):
        """
        Critical Startup Invariant:
        When restarted or resumed after downtime/crash/server reload:
        1. Query live broker positions from /api/v1/positionbook.
        2. Filter to symbols owned by this strategy via today's orderbook.
        3. Sync & reconcile open legs. If local state was missing, reconstruct from broker.
        4. Ensure every short leg has an assigned Stop Loss threshold (1.5x of entry).
        5. Immediately check live LTP against SL:
           - If LTP >= SL (breached during downtime): immediately unwind breached side.
           - If LTP < SL: log protection armed and continue normal real-time monitoring.
        6. Persist reconciled state to disk.
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

            strategy_symbols = self._get_strategy_symbols_from_orderbook()
            logger.info(f"📚 [STRATEGY SCOPE] Today's {STRATEGY_NAME} orderbook symbols: {sorted(strategy_symbols) or 'NONE (will rely on local state symbols)'}")

            broker_positions: Dict[str, Dict] = {}
            for p in pbook:
                if not isinstance(p, dict) or not p.get("symbol"):
                    continue
                sym = str(p.get("symbol")).strip()
                # Strategy-scoped filtering: symbol must be in today's orderbook for this strategy
                # OR already tracked in local state. This prevents adopting foreign strategy legs.
                if sym not in strategy_symbols and sym not in self.positions:
                    continue
                # Must be a BTC option
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
                logger.info("ℹ️ [STARTUP AUDIT] No strategy-scoped BTC option positions on broker.")
                changed = False
                for sym, pos in list(self.positions.items()):
                    if pos.get("status") == "OPEN" and not pos.get("exit_attempted"):
                        # Only mark closed if not stuck and no exit was attempted
                        continue
                    if pos.get("status") == "OPEN":
                        pos["status"] = "CLOSED"
                        pos["exit_reason"] = "BROKER_RECONCILED_FLAT"
                        changed = True
                if changed:
                    self.trade_active = False
                    self._save_state()
                return

            logger.info(f"📊 [STARTUP AUDIT] Detected {len(broker_positions)} active strategy-scoped BTC option position(s) on broker:")
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
                        pos["stop_loss"] = round(entry * self.sl_multiplier, 2)
                        logger.info(f"🛡️ [STARTUP AUDIT] Armed missing Stop Loss for {sym} at ${pos['stop_loss']:.2f} (Entry: ${entry:.2f}, Mult: {self.sl_multiplier:.1f}x)")

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
                        closed_short, avg_p, _, _ = self.place_and_verify_order(sym, "BUY", qty_to_close, timeout_sec=8)
                        if closed_short:
                            pos["status"] = "CLOSED"
                            pos["exit_price"] = avg_p if avg_p > 0 else ltp
                            pos["exit_reason"] = "STARTUP_SL_BREACH"
                            logger.info(f"  ✅ [STARTUP AUDIT] Confirmed closed short leg {sym} @ ${pos['exit_price']:.2f}")
                        else:
                            pos["status"] = "STUCK"
                            pos["stuck_reason"] = "STARTUP_SL_BREACH_EXIT_FAILED"
                            logger.error(f"  ❌ [STARTUP AUDIT] Failed to fill short leg exit {sym}. Marked STUCK for manual review.")
                            send_alert(f"STUCK POSITION: {sym} - startup SL breach exit failed. Immediate manual review required.", level="critical")

                    # Step B: Close protective wing hedge
                    wing_type = "LONG_CE" if pos["leg_type"] == "SHORT_CE" else "LONG_PE"
                    for w_sym, w_pos in list(self.positions.items()):
                        if w_pos.get("leg_type") == wing_type and w_pos.get("status") == "OPEN":
                            w_pos["exit_attempted"] = True
                            w_live_qty = self._get_broker_net_qty(w_sym)
                            w_qty = abs(int(w_live_qty)) if w_live_qty is not None else w_pos["quantity"]
                            if w_qty > 0:
                                closed_wing, w_avg_p, _, _ = self.place_and_verify_order(w_sym, "SELL", w_qty, timeout_sec=8)
                                if closed_wing:
                                    w_pos["status"] = "CLOSED"
                                    w_pos["exit_price"] = w_avg_p if w_avg_p > 0 else (self.get_quote_ltp(w_sym) or 0.0)
                                    w_pos["exit_reason"] = "STARTUP_WING_UNWIND"
                                    logger.info(f"  ✅ [STARTUP AUDIT] Confirmed closed protective wing {w_sym} @ ${w_pos['exit_price']:.2f}")
                                else:
                                    w_pos["status"] = "STUCK"
                                    w_pos["stuck_reason"] = "STARTUP_WING_UNWIND_FAILED"
                                    logger.error(f"  ❌ [STARTUP AUDIT] Failed to close wing {w_sym}. Marked STUCK for manual review.")
                                    send_alert(f"STUCK POSITION: {w_sym} - startup wing unwind failed. Immediate manual review required.", level="critical")
                else:
                    logger.info(
                        f"🛡️ [STARTUP SL INTACT] {sym} LTP ${ltp:.2f} < SL ${sl:.2f} (Entry: ${pos.get('entry_price', 0.0):.2f}). Active protection running."
                    )

            # Check if all legs are closed after audit (ignore stuck for trade_active flag)
            all_closed_or_stuck = all(p.get("status") in ("CLOSED", "STUCK") for p in self.positions.values())
            if all_closed_or_stuck and self.positions:
                self._handle_all_legs_closed(exit_trigger="STARTUP_RECOVERY_ALL_CLOSED")

            self._save_state()
            logger.info("✅ [STARTUP POSITION AUDIT COMPLETE] Strategy state synchronized with broker.")
        except Exception as e:
            logger.exception(f"❌ [STARTUP AUDIT ERROR] Failed during position recovery: {e}")

    def get_spot_price(self) -> Optional[float]:
        self.limiter.acquire()
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

    # --------------------------------------------------------------------------
    # STRIKE RESOLUTION WITH DIRECT VALIDATION
    # --------------------------------------------------------------------------
    def resolve_option_symbol(self, strike: float, option_type: str, expiry: str) -> Optional[str]:
        """
        Resolve option symbol by strike. First attempts direct strike-based lookup
        if the broker API supports a `strike` parameter; falls back to the
        offset=ATM + underlying_ltp trick only when necessary. Validates that the
        returned symbol's parsed strike matches the requested target.
        """
        strike_int = int(round(strike / 100.0) * 100)
        option_type = option_type.upper()

        # Attempt 1: Direct strike-based lookup (preferred, API-dependent)
        try:
            url = f"{self.host}/api/v1/optionsymbol"
            resp = requests.post(url, json={
                "apikey": self.api_key,
                "underlying": "BTC",
                "exchange": EXCHANGE,
                "expiry_date": expiry,
                "option_type": option_type,
                "strike": strike_int,
            }, timeout=4)
            if resp.status_code == 200:
                sym = resp.json().get("symbol")
                if sym and self._validate_resolved_symbol(sym, strike_int, option_type):
                    return sym
        except Exception as e:
            logger.debug(f"Direct strike lookup failed for {strike_int} {option_type}: {e}")

        # Attempt 2: Fallback offset=ATM with fake underlying_ltp
        try:
            url = f"{self.host}/api/v1/optionsymbol"
            resp = requests.post(url, json={
                "apikey": self.api_key,
                "underlying": "BTC",
                "exchange": EXCHANGE,
                "expiry_date": expiry,
                "offset": "ATM",
                "option_type": option_type,
                "underlying_ltp": strike_int,
            }, timeout=4)
            if resp.status_code == 200:
                sym = resp.json().get("symbol")
                if sym and self._validate_resolved_symbol(sym, strike_int, option_type):
                    logger.warning(
                        f"[STRIKE RESOLUTION] Used fallback ATM-underlying_ltp trick for {strike_int} {option_type}. "
                        "Consider migrating to a direct strike API."
                    )
                    return sym
        except Exception as e:
            logger.warning(f"Symbol lookup failed for strike {strike} {option_type}: {e}")
        return None

    def _validate_resolved_symbol(self, symbol: str, expected_strike: int, expected_type: str) -> bool:
        """Validate that resolved symbol matches expected strike and option type."""
        parsed_strike = parse_strike_from_symbol(symbol)
        parsed_type = option_type_from_symbol(symbol)
        if parsed_strike is None or parsed_type is None:
            logger.warning(f"[SYMBOL VALIDATION] Could not parse strike/type from {symbol}. Rejecting.")
            return False
        if parsed_type != expected_type:
            logger.warning(f"[SYMBOL VALIDATION] Type mismatch for {symbol}: expected {expected_type}, got {parsed_type}. Rejecting.")
            return False
        # Allow ±$100 tolerance around target due to exchange strike granularity
        if abs(parsed_strike - expected_strike) > 100:
            logger.warning(
                f"[SYMBOL VALIDATION] Strike mismatch for {symbol}: expected {expected_strike}, parsed {int(parsed_strike)}. Rejecting."
            )
            return False
        return True

    def check_strike_liquidity(
        self, symbol: str, action: str, max_wing_price: float = 110.0
    ) -> Tuple[bool, float, float]:
        """
        Validates whether symbol has active bid/ask market maker quotes on Delta Exchange.
        - For BUY (wings): requires ask > 0 and ask <= max_wing_price (protects from collar/slippage).
        - For SELL (shorts): requires bid > 0 (prevents resting limit collar orders).
        Returns: (is_liquid: bool, bid: float, ask: float)
        """
        if self.dry_run:
            return True, 5.0, 6.0

        self.limiter.acquire()
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

                # Stale-quote guard: Only reject if quote is plainly anomalous or corrupted
                if high > 0.0 and ltp > high * 1.5:
                    logger.info(
                        f"  ⚠️ [STRIKE SKIPPED] {symbol} has anomalous ticker range "
                        f"(LTP ${ltp:.2f} > 1.5x Day High ${high:.2f}). Hunting next strike..."
                    )
                    return False, 0.0, 0.0

                # Spread filter (reject strikes with spread > max_spread_pct)
                if ask > 0.0 and bid > 0.0:
                    spread_pct = (ask - bid) / ask
                    dollar_spr = ask - bid
                    if action == "BUY":
                        # Protective wings (cheap insurance): accept if dollar spread <= $5.00 OR spread_pct <= 20%
                        if spread_pct > 0.20 and dollar_spr > 5.0:
                            logger.info(
                                f"  ⚠️ [WING SKIPPED] {symbol} spread too wide (${dollar_spr:.2f} / {spread_pct*100:.1f}% > $5.00 / 20%). Hunting next strike..."
                            )
                            return False, 0.0, 0.0
                    else:
                        # Short legs: accept if spread_pct <= max_spread_pct OR dollar_spr <= $5.00
                        max_spr = getattr(self, "max_spread_pct", 0.08)
                        max_dollar_spr = float(os.getenv("BTC_MAX_SHORT_SPREAD_DOLLAR", "5.0"))
                        if spread_pct > max_spr and dollar_spr > max_dollar_spr:
                            logger.info(
                                f"  ⚠️ [STRIKE SKIPPED] {symbol} short spread too wide (${dollar_spr:.2f} / {spread_pct*100:.1f}% > ${max_dollar_spr:.2f} / {max_spr*100:.1f}%). Hunting next strike..."
                            )
                            return False, 0.0, 0.0

                if action == "BUY":
                    if ask > 0.0 and ask <= max_wing_price:
                        return True, bid, ask
                elif action == "SELL":
                    # Session 1: enforce $100.00 min premium to harvest substantial decay
                    # Session 2+: no $100.00 floor (accept any valid liquid bid >= $1.00) to remain at 1.5% OTM
                    if getattr(self, "current_session", 1) == 1:
                        min_prem = getattr(self, "min_premium", MIN_PREMIUM_THRESHOLD)
                    else:
                        min_prem = 1.0
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
        max_wing_price: float = 110.0,
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
            # Walk inwards towards ATM:
            # Session 1: up to 35 steps to meet MIN_PREMIUM_THRESHOLD ($100.0)
            # Session 2+: only up to 6 steps if 1.5% OTM is illiquid, staying safely far OTM
            max_inward_steps = 35 if getattr(self, "current_session", 1) == 1 else 6
            if option_type == "CE":
                for step in range(1, max_inward_steps):
                    cand = primary_strike - (step * strike_step)
                    if cand not in candidate_strikes:
                        candidate_strikes.append(cand)
            elif option_type == "PE":
                for step in range(1, max_inward_steps):
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

    def get_quote_data(self, symbol: str) -> Dict[str, Optional[float]]:
        """Fetch full quote data (ask, bid, ltp, spread) to support accurate execution-side pricing."""
        self.limiter.acquire()
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
                ask = float(inner["ask"]) if inner.get("ask") and float(inner["ask"]) > 0 else None
                bid = float(inner["bid"]) if inner.get("bid") and float(inner["bid"]) > 0 else None
                ltp = float(inner["ltp"]) if inner.get("ltp") and float(inner["ltp"]) > 0 else None
                spread = None
                if ask and bid and ask > 0:
                    spread = (ask - bid) / ask
                return {"ask": ask, "bid": bid, "ltp": ltp, "spread": spread}
        except Exception as e:
            logger.debug(f"Could not fetch quote data for {symbol}: {e}")
        return {"ask": None, "bid": None, "ltp": None, "spread": None}

    def get_quote_ltp(self, symbol: str) -> Optional[float]:
        qd = self.get_quote_data(symbol)
        return qd.get("ltp") or qd.get("ask")

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
            err_text = resp.text.lower()
            if any(term in err_text for term in ("already", "not found", "filled", "closed", "terminal", "cancelled")):
                logger.info(f"Order {order_id} already terminal on broker ({resp.text.strip()})")
                return True
            logger.warning(f"Cancel order {order_id} returned HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            logger.error(f"Error cancelling order {order_id}: {e}")
        return False

    def place_order(self, symbol: str, action: str, qty: int) -> Dict[str, Any]:
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would execute {action} {qty} contracts of {symbol}")
            return {"status": "success", "orderid": "DRY_ORDER_123", "symbol": symbol, "price": 150.0}

        self.limiter.acquire()
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

    def _extract_fill_info(self, order_data: Dict[str, Any], requested_qty: int) -> Tuple[bool, int, float]:
        """
        Extract filled quantity and average price from order status payload.
        Returns (complete_or_partial, filled_qty, avg_price).
        """
        if not isinstance(order_data, dict):
            return False, 0, 0.0

        status = str(order_data.get("order_status") or order_data.get("status") or "").lower()
        if status not in ("complete", "filled", "partially filled", "open", "pending"):
            return False, 0, 0.0

        filled_qty_raw = order_data.get("filled_quantity") or order_data.get("filledqty") or order_data.get("traded_quantity") or order_data.get("tradedqty")
        quantity_raw = order_data.get("quantity") or order_data.get("qty") or requested_qty
        avg_price_raw = order_data.get("average_price") or order_data.get("avg_price") or order_data.get("price") or 0.0

        try:
            filled_qty = int(float(filled_qty_raw)) if filled_qty_raw is not None else 0
        except (ValueError, TypeError):
            filled_qty = 0

        try:
            quantity = int(float(quantity_raw)) if quantity_raw is not None else requested_qty
        except (ValueError, TypeError):
            quantity = requested_qty

        # If status is complete but no filled_quantity provided, assume full fill
        if status in ("complete", "filled") and filled_qty == 0 and quantity > 0:
            filled_qty = quantity

        avg_price = float(avg_price_raw) if avg_price_raw else 0.0
        return True, filled_qty, avg_price

    def place_and_verify_order(
        self, symbol: str, action: str, qty: int, timeout_sec: int = 8
    ) -> Tuple[bool, float, Optional[str], int]:
        """
        Submits order and polls orderstatus / orderbook until 'complete'.
        Handles partial fills by recording actual filled quantity.
        If unfilled after timeout_sec, cancels resting limit collar order to prevent unexpected future fills.
        Returns: (success: bool, fill_price: float, order_id: Optional[str], filled_qty: int)
        """
        if self.dry_run:
            logger.info(f"[DRY-RUN] Would execute {action} {qty} contracts of {symbol}")
            return True, 150.0, "DRY_ORDER_123", qty

        res = self.place_order(symbol, action, qty)
        if res.get("status") != "success":
            logger.error(f"Failed to submit {action} {qty} {symbol}: {res}")
            return False, 0.0, None, 0

        order_id = str(res.get("orderid", "")).strip()
        if not order_id:
            logger.error(f"Order placement for {symbol} returned no orderid: {res}")
            return False, 0.0, None, 0

        logger.info(f"  ⏳ Submitted order {order_id} ({action} {qty} {symbol}). Verifying fill...")

        deadline = time.time() + timeout_sec
        total_filled_qty = 0
        weighted_price_sum = 0.0

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
                    ok, filled, avg = self._extract_fill_info(data, qty)
                    if ok and filled > 0:
                        total_filled_qty = filled
                        weighted_price_sum = avg * filled
                        if filled >= qty:
                            logger.info(f"  ✅ Order {order_id} confirmed FILLED ({filled}/{qty}) @ ${avg:.2f}")
                            return True, avg, order_id, filled
                        else:
                            logger.warning(
                                f"  ⚠️ Order {order_id} PARTIAL FILL ({filled}/{qty}) @ ${avg:.2f}. "
                                "Will continue monitoring but recording actual filled quantity."
                            )
                    status = str(data.get("order_status", "")).lower()
                    if status in ("cancelled", "rejected"):
                        logger.error(f"  ❌ Order {order_id} was {status.upper()}: {data}")
                        # Return partial fill info if any
                        if total_filled_qty > 0:
                            avg_fill = weighted_price_sum / total_filled_qty if total_filled_qty > 0 else 0.0
                            return True, avg_fill, order_id, total_filled_qty
                        return False, 0.0, order_id, 0
            except Exception as e:
                logger.debug(f"orderstatus poll notice for {order_id}: {e}")

            # Check 2: Fallback to orderbook query
            try:
                ob_resp = requests.post(
                    f"{self.host}/api/v1/orderbook",
                    json={"apikey": self.api_key, "strategy": STRATEGY_NAME},
                    timeout=3,
                )
                if ob_resp.status_code == 200:
                    orders = ob_resp.json().get("data", {}).get("orders", [])
                    if isinstance(orders, list):
                        target = next((o for o in orders if str(o.get("orderid")) == order_id or str(o.get("orderId")) == order_id), None)
                        if target:
                            ok, filled, avg = self._extract_fill_info(target, qty)
                            if ok and filled > 0:
                                total_filled_qty = filled
                                weighted_price_sum = avg * filled
                                if filled >= qty:
                                    logger.info(f"  ✅ Order {order_id} confirmed FILLED via orderbook ({filled}/{qty}) @ ${avg:.2f}")
                                    return True, avg, order_id, filled
                                else:
                                    logger.warning(
                                        f"  ⚠️ Order {order_id} PARTIAL FILL via orderbook ({filled}/{qty}) @ ${avg:.2f}."
                                    )
                            status = str(target.get("order_status", "")).lower()
                            if status in ("cancelled", "rejected"):
                                logger.error(f"  ❌ Order {order_id} was {status.upper()} in orderbook")
                                if total_filled_qty > 0:
                                    avg_fill = weighted_price_sum / total_filled_qty
                                    return True, avg_fill, order_id, total_filled_qty
                                return False, 0.0, order_id, 0
            except Exception as e:
                logger.debug(f"orderbook poll notice for {order_id}: {e}")

        # Timeout reached: cancel resting order to avoid stale fills
        logger.warning(f"  ⏳ Order {order_id} ({action} {symbol}) did not fully fill within {timeout_sec}s. Cancelling remainder...")
        self.cancel_order_by_id(order_id)
        time.sleep(0.5)

        # Final probe after cancel in case order filled at the boundary
        try:
            os_resp = requests.post(
                f"{self.host}/api/v1/orderstatus",
                json={"apikey": self.api_key, "strategy": STRATEGY_NAME, "orderid": order_id},
                timeout=3,
            )
            if os_resp.status_code == 200:
                data = os_resp.json().get("data", {})
                ok, filled, avg = self._extract_fill_info(data, qty)
                if ok and filled > total_filled_qty:
                    total_filled_qty = filled
                    weighted_price_sum = avg * filled
        except Exception:
            pass

        if total_filled_qty > 0:
            avg_fill = weighted_price_sum / total_filled_qty
            if total_filled_qty >= qty:
                logger.info(f"  ✅ Order {order_id} confirmed FILLED on post-cancel probe ({total_filled_qty}/{qty}) @ ${avg_fill:.2f}")
                return True, avg_fill, order_id, total_filled_qty
            logger.warning(f"  ⚠️ Returning partial fill for {symbol}: {total_filled_qty}/{qty} @ ${avg_fill:.2f}")
            return True, avg_fill, order_id, total_filled_qty

        return False, 0.0, order_id, 0

    def compute_dynamic_lots(self, spot: float = 77000.0) -> int:
        """
        Computes dynamic lot sizing maintaining safe margin buffers.
        Re-runs every morning at entry — automatically captures weekly capital growth.
        - Condor (Architecture A2): Hedged spread margin = SPREAD_WIDTH * multiplier USD.
        - Straddle (Architecture B3): Unhedged short margin ~10% notional = spot * multiplier * 0.10.
        Growth cap: lots cannot more than double from the previous day's lot count (prevents runaway sizing).
        Uses live USD/INR rate and fetched contract multiplier.
        """
        if not DYNAMIC_SIZING:
            return self.lots

        # Refresh FX rate at entry time
        self.usd_inr_rate = fetch_usd_inr_rate()
        usd_inr = self.usd_inr_rate

        allocation_pct = float(os.getenv("BTC_IC_ALLOCATION_PCT", "0.50"))
        available_inr = self.capital * allocation_pct
        try:
            url = f"{self.host}/api/v1/funds"
            resp = requests.post(url, json={"apikey": self.api_key}, timeout=4)
            if resp.status_code == 200:
                funds_data = resp.json().get("data", {})
                live_cash = float(funds_data.get("availablecash", 0.0))
                # Try to derive live USD/INR rate from the wallet response (balance_inr / balance_usd)
                live_usd = float(funds_data.get("availablecash_usd") or 0.0)
                if live_usd > 0 and live_cash > 0:
                    derived_rate = live_cash / live_usd
                    if abs(derived_rate - usd_inr) / usd_inr > 0.05:
                        logger.warning(
                            f"[FX DISCREPANCY] Wallet-derived USD/INR {derived_rate:.2f} differs >5% from feed {usd_inr:.2f}. "
                            "Using live feed."
                        )

                if live_cash >= 5000000:
                    # OpenAlgo Sandbox mode (1 Cr) -> use configured base testing capital directly
                    available_inr = self.capital * allocation_pct
                    logger.info(f"[SANDBOX FUNDS] Sandbox mode detected. Using allocated base capital ({allocation_pct*100:.0f}%): Rs {available_inr:,.2f}")
                elif live_cash > 0:
                    # Live Delta Exchange India account -> allocate 50% for dual-symbol concurrency
                    available_inr = live_cash * allocation_pct
                    logger.info(f"[LIVE FUNDS] Live Cash allocated ({allocation_pct*100:.0f}%): Rs {available_inr:,.2f} of Rs {live_cash:,.2f}")
                else:
                    algo_mode = str(resp.json().get("mode", "")).lower()
                    if algo_mode == "live" and not self.dry_run:
                        logger.error("❌ [LIVE FUNDS ZERO] Live Delta Exchange wallet balance is Rs 0.00. Aborting entry until funded.")
                        return 0
        except Exception as e:
            logger.warning(f"Funds API check notice: {e}")

        prev_lots = self.lots  # Track previous lots for growth cap
        multiplier = self.multiplier or 0.001

        if self.mode == "straddle":
            # 10% notional margin per unhedged short leg with 50% max margin cap
            margin_per_lot_usd = spot * multiplier * 0.10
            margin_per_lot_inr = margin_per_lot_usd * usd_inr
            usable_margin_inr = available_inr * 0.50
            computed_lots = int(usable_margin_inr / margin_per_lot_inr) if margin_per_lot_inr > 0 else 0
            if computed_lots < 1:
                logger.warning("Computed straddle lots below minimum (1). Capital insufficient.")
                return 0
            growth_cap = max(prev_lots * 2 if prev_lots > 0 else DEFAULT_LOTS, DEFAULT_LOTS)
            final_lots = max(1, min(computed_lots, 1000, growth_cap))
            logger.info(
                f"Dynamic Straddle Sizing: Capital Base=Rs {available_inr:,.2f} | "
                f"50% Usable=Rs {usable_margin_inr:,.2f} | Computed={computed_lots} | "
                f"GrowthCap={growth_cap} | Final={final_lots} lots | Multiplier={multiplier}"
            )
        else:
            # Hedged spread margin
            margin_per_lot_usd = self.spread_width * multiplier
            margin_per_lot_inr = margin_per_lot_usd * usd_inr
            usable_margin_inr = available_inr * MARGIN_UTILIZATION_CAP
            computed_lots = int(usable_margin_inr / margin_per_lot_inr) if margin_per_lot_inr > 0 else 0
            if computed_lots < 10:
                logger.warning(f"Computed condor lots ({computed_lots}) below minimum (10). Capital insufficient.")
                return 0
            growth_cap = max(prev_lots * 2 if prev_lots > 0 else DEFAULT_LOTS, DEFAULT_LOTS)
            final_lots = max(10, min(computed_lots, 3000, growth_cap))
            logger.info(
                f"Dynamic Condor Sizing: Capital Base=Rs {available_inr:,.2f} | "
                f"{int(MARGIN_UTILIZATION_CAP*100)}% Usable=Rs {usable_margin_inr:,.2f} | Computed={computed_lots} | "
                f"GrowthCap={growth_cap} | Final={final_lots} lots (35% Free Buffer Preserved) | Multiplier={multiplier}"
            )
        return final_lots

    # --------------------------------------------------------------------------
    # CONCURRENT LEG EXECUTION
    # --------------------------------------------------------------------------
    def _execute_leg(self, leg_type: str, sym: str, action: str, qty: int) -> Tuple[str, Dict[str, Any]]:
        """Helper for ThreadPoolExecutor: execute a single leg and return result."""
        success, fill_p, oid, filled_qty = self.place_and_verify_order(sym, action, qty, timeout_sec=8)
        result = {
            "leg_type": leg_type,
            "symbol": sym,
            "action": action,
            "requested_qty": qty,
            "quantity": filled_qty,
            "entry_price": fill_p,
            "status": "OPEN" if success and filled_qty > 0 else "FAILED",
            "order_id": oid,
            "stop_loss": fill_p * self.sl_multiplier if success and action == "SELL" and filled_qty > 0 else 0.0,
        }
        return sym, result

    def _rollback_leg(self, sym: str, pos: Dict[str, Any]) -> Tuple[str, bool]:
        """Rollback a single filled leg. Returns (symbol, success)."""
        rb_action = "SELL" if pos["action"] == "BUY" else "BUY"
        qty = pos.get("quantity", 0)
        logger.info(f"  Rollback: {rb_action} {qty} {sym}")
        for attempt in range(2):
            rb_success, _, _, _ = self.place_and_verify_order(sym, rb_action, qty, timeout_sec=8)
            if rb_success:
                logger.info(f"  ✅ Successfully rolled back {sym}")
                return sym, True
            else:
                logger.warning(f"  ⚠️ Rollback attempt {attempt + 1} failed for {sym}, retrying...")
                time.sleep(1.0)
        return sym, False

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

        # 1. Compute Strike Targets with Adaptive OTM Step-Down Ladder
        # Evaluates primary target (2.0%), then fallback tiers (1.5%, 1.0%) down to floor
        min_otm_floor = float(os.getenv("BTC_IC_MIN_OTM_FLOOR", "0.008"))
        ladder_steps = [1.0, 0.75, 0.5]
        otm_candidates: List[float] = []
        for factor in ladder_steps:
            cand = round(self.otm_pct * factor, 4)
            if cand >= min_otm_floor and cand not in otm_candidates:
                otm_candidates.append(cand)
        if not otm_candidates:
            otm_candidates = [self.otm_pct]

        max_wing_px = float(os.getenv("BTC_MAX_WING_PRICE", "110.0"))
        sym_short_ce, sym_short_pe, sym_long_ce, sym_long_pe = None, None, None, None
        selected_otm = self.otm_pct

        for idx, cand_otm in enumerate(otm_candidates):
            logger.info(f"🔍 [ADAPTIVE OTM LADDER] Evaluating {cand_otm*100:.2f}% OTM (Attempt {idx+1}/{len(otm_candidates)})...")
            short_call_target = spot * (1.0 + cand_otm)
            long_call_target = short_call_target + self.spread_width
            short_put_target = spot * (1.0 - cand_otm)
            long_put_target = short_put_target - self.spread_width

            cand_s_ce = self.resolve_liquid_option_symbol(
                short_call_target, "CE", expiry, action="SELL", is_wing=False
            )
            cand_s_pe = self.resolve_liquid_option_symbol(
                short_put_target, "PE", expiry, action="SELL", is_wing=False
            )
            if not cand_s_ce or not cand_s_pe:
                logger.warning(f"  ⚠️ [ADAPTIVE LADDER] Short legs unavailable/illiquid at {cand_otm*100:.2f}% OTM. Trying next tier...")
                continue

            # Base wing hedge targets on actual resolved short strikes to maintain exact spread width
            actual_s_ce_strike = parse_strike_from_symbol(cand_s_ce) or short_call_target
            actual_s_pe_strike = parse_strike_from_symbol(cand_s_pe) or short_put_target
            resolved_l_ce_target = actual_s_ce_strike + self.spread_width
            resolved_l_pe_target = actual_s_pe_strike - self.spread_width

            cand_l_ce = self.resolve_liquid_option_symbol(
                resolved_l_ce_target, "CE", expiry, action="BUY", is_wing=True, short_strike_ref=actual_s_ce_strike, max_wing_price=max_wing_px
            )
            cand_l_pe = self.resolve_liquid_option_symbol(
                resolved_l_pe_target, "PE", expiry, action="BUY", is_wing=True, short_strike_ref=actual_s_pe_strike, max_wing_price=max_wing_px
            )
            if not cand_l_ce or not cand_l_pe:
                logger.warning(f"  ⚠️ [ADAPTIVE LADDER] Wing hedges unavailable/over ceiling at {cand_otm*100:.2f}% OTM. Trying next tier...")
                continue

            # Strict Strike Hierarchy Guard: Prevent Inverted Condors
            def _get_strike(symbol: str) -> float:
                m = re.search(r"[A-Z]{3}\d{2}-?(\d+)-?(CE|PE)$", symbol)
                if m:
                    return float(m.group(1))
                m_gen = re.search(r"(\d+)(CE|PE)$", symbol)
                return float(m_gen.group(1)) if m_gen else 0.0

            strike_s_ce = _get_strike(cand_s_ce)
            strike_s_pe = _get_strike(cand_s_pe)
            if strike_s_ce < strike_s_pe:
                logger.warning(
                    f"  ⚠️ [ADAPTIVE LADDER] Inverted strikes ({cand_s_ce} [${strike_s_ce}] < {cand_s_pe} [${strike_s_pe}]) "
                    f"at {cand_otm*100:.2f}% OTM. Trying next tier..."
                )
                continue

            # Complete balanced basket resolved!
            sym_short_ce, sym_short_pe, sym_long_ce, sym_long_pe = cand_s_ce, cand_s_pe, cand_l_ce, cand_l_pe
            selected_otm = cand_otm
            if self.mode == "condor":
                self.sl_multiplier = compute_dynamic_sl_mult(selected_otm)
            logger.info(f"🎯 [ADAPTIVE OTM LADDER] Confirmed 4 liquid strikes at {selected_otm*100:.2f}% OTM (Dynamic SL: {self.sl_multiplier:.1f}x)!")
            break

        if not (sym_short_ce and sym_short_pe and sym_long_ce and sym_long_pe):
            logger.error(f"Failed to resolve complete liquid 4-leg basket across {len(otm_candidates)} OTM ladder attempts. Aborting execution.")
            return False

        logger.info(f"Liquid Strikes Verified & Selected ({expiry}):")
        logger.info(f"  • Call Wing (BUY Hedge) : {sym_long_ce}")
        logger.info(f"  • Call Short (SELL)     : {sym_short_ce}")
        logger.info(f"  • Put Short (SELL)      : {sym_short_pe}")
        logger.info(f"  • Put Wing (BUY Hedge)  : {sym_long_pe}")

        # 3. Two-Phase Concurrent Order Sequence (Wings First, then Shorts)
        executed_positions: Dict[str, Dict[str, Any]] = {}

        # Phase 1: Wings concurrently
        wing_legs = [
            ("LONG_CE", sym_long_ce, "BUY", self.lots),
            ("LONG_PE", sym_long_pe, "BUY", self.lots),
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(self._execute_leg, lt, sym, act, qty): (lt, sym, act, qty)
                       for lt, sym, act, qty in wing_legs}
            for future in as_completed(futures):
                sym, result = future.result()
                if result["status"] == "OPEN":
                    executed_positions[sym] = result
                    logger.info(f"  ✅ [{result['leg_type']}] {result['action']} {result['quantity']} {sym} established @ ${result['entry_price']:.2f}")
                else:
                    logger.error(f"  ❌ Wing execution failed for [{result['leg_type']}] {result['action']} {sym}. Triggering ATOMIC ROLLBACK...")

        # If any wing failed or was only partially filled, rollback any filled wings and abort
        if len(executed_positions) < len(wing_legs) or any(p.get("quantity", 0) < self.lots for p in executed_positions.values()):
            logger.warning(f"Incomplete wings ({len(executed_positions)}/{len(wing_legs)}). Rolling back...")
            rollback_ok = self._rollback_failed_entry(executed_positions)
            if not rollback_ok:
                self.trade_active = True
                self.trade_taken_today = False
                self._save_state()
                logger.error("❌ Wing rollback FAILED. STUCK positions flagged for manual review.")
                send_alert("Wing entry rollback FAILED. STUCK positions require immediate manual review.", level="critical")
                return False
            self.positions = {}
            self.trade_active = False
            self.trade_taken_today = False
            self._save_state()
            return False

        # Phase 2: Re-validate spot before short execution to avoid legging risk
        current_spot = self.get_spot_price()
        if current_spot:
            drift_pct = abs(current_spot - spot) / spot
            drift_tol = float(os.getenv("BTC_IC_SPOT_DRIFT_TOL", "0.005"))  # 0.5% tolerance
            if drift_pct > drift_tol:
                logger.warning(
                    f"⚠️ [SPOT DRIFT] Spot moved {drift_pct*100:.2f}% during wing execution "
                    f"(${spot:,.2f} -> ${current_spot:,.2f} > {drift_tol*100:.1f}%). Aborting shorts to avoid misaligned strikes."
                )
                rollback_ok = self._rollback_failed_entry(executed_positions)
                if not rollback_ok:
                    self.trade_active = True
                    self.trade_taken_today = False
                    self._save_state()
                    logger.error("❌ Spot drift rollback FAILED. STUCK positions flagged for manual review.")
                    send_alert("Spot drift rollback FAILED. STUCK positions require immediate manual review.", level="critical")
                    return False
                self.positions = {}
                self.trade_active = False
                self.trade_taken_today = False
                self._save_state()
                return False

        short_legs = [
            ("SHORT_CE", sym_short_ce, "SELL", self.lots),
            ("SHORT_PE", sym_short_pe, "SELL", self.lots),
        ]
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(self._execute_leg, lt, sym, act, qty): (lt, sym, act, qty)
                       for lt, sym, act, qty in short_legs}
            for future in as_completed(futures):
                sym, result = future.result()
                if result["status"] == "OPEN":
                    executed_positions[sym] = result
                    logger.info(f"  ✅ [{result['leg_type']}] {result['action']} {result['quantity']} {sym} established @ ${result['entry_price']:.2f} (SL: ${result['stop_loss']:.2f})")
                else:
                    logger.error(f"  ❌ Short execution failed for [{result['leg_type']}] {result['action']} {sym}. Triggering ATOMIC ROLLBACK...")

        # If any short failed or partially filled, initiate atomic rollback
        if len(executed_positions) < len(wing_legs) + len(short_legs) or any(p.get("quantity", 0) < self.lots for p in executed_positions.values()):
            logger.warning(
                f"Incomplete Condor ({len(executed_positions)}/{len(wing_legs) + len(short_legs)} legs, partial fill detected). "
                f"Rolling back all filled legs to eliminate unhedged exposure..."
            )
            rollback_ok = self._rollback_failed_entry(executed_positions)
            if not rollback_ok:
                # If rollback failed, state persists with STUCK legs; do NOT clear positions.
                self.trade_active = True  # Keep monitoring
                self.trade_taken_today = False
                self._save_state()
                logger.error("❌ Iron Condor establishment aborted due to incomplete rollback. STUCK positions flagged for manual review.")
                send_alert("Iron Condor entry rollback FAILED. STUCK positions require immediate manual review.", level="critical")
                return False
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

        # Calculate Net Credit using fetched multiplier net of exchange fees
        sc_prem = self.positions[sym_short_ce]["entry_price"]
        sp_prem = self.positions[sym_short_pe]["entry_price"]
        lc_prem = self.positions[sym_long_ce]["entry_price"]
        lp_prem = self.positions[sym_long_pe]["entry_price"]
        net_credit_per_contract = max(0.0, (sc_prem + sp_prem) - (lc_prem + lp_prem))
        gross_credit = net_credit_per_contract * self.lots * self.multiplier
        taker_fee_bps = 30.0 / 10000.0
        fees_in = sum(px * self.multiplier * self.lots * taker_fee_bps + 0.05
                      for px in (sc_prem, sp_prem, lc_prem, lp_prem))
        self.net_credit_collected = max(0.0, gross_credit - fees_in) if gross_credit > fees_in else gross_credit
        self.target_profit = self.net_credit_collected * self.target_decay_pct

        logger.info("=" * 80)
        logger.info(f"🎉 4-LEG IRON CONDOR FULLY ESTABLISHED & VERIFIED!")
        logger.info(f"Net Credit Expected: ~${self.net_credit_collected:.2f} USD (~Rs {self.net_credit_collected * self.usd_inr_rate:,.2f} INR)")
        logger.info(f"Target Profit ({self.target_decay_pct*100:.0f}% Decay): ~${self.target_profit:.2f} USD (~Rs {self.target_profit * self.usd_inr_rate:,.2f} INR)")
        logger.info(f"Call SL Threshold: ${self.positions[sym_short_ce]['stop_loss']:.2f} | Put SL Threshold: ${self.positions[sym_short_pe]['stop_loss']:.2f}")
        logger.info("=" * 80)
        self._save_state()
        return True

    def _rollback_failed_entry(self, executed_positions: Dict[str, Dict[str, Any]]) -> bool:
        """
        Rollback all legs from a failed entry. Shorts are closed first to eliminate naked risk.
        Returns True only if every leg was successfully closed.
        On any failure, marks the leg STUCK and alerts the operator.
        """
        if not executed_positions:
            return True

        # Sort: Shorts first (BUY to close), Wings second (SELL to close)
        rb_legs = sorted(
            list(executed_positions.items()),
            key=lambda item: 0 if item[1].get("action") == "SELL" else 1
        )

        all_rolled_back = True
        for sym, pos in rb_legs:
            sym, success = self._rollback_leg(sym, pos)
            if success:
                pos["status"] = "CLOSED"
                pos["exit_reason"] = "ENTRY_ROLLBACK"
            else:
                pos["status"] = "STUCK"
                pos["stuck_reason"] = "ENTRY_ROLLBACK_FAILED"
                pos["exit_attempted"] = True
                all_rolled_back = False
                send_alert(f"STUCK POSITION: {sym} ({pos['leg_type']}) - entry rollback failed. Manual intervention required.", level="critical")

        # Persist any stuck legs in positions so they remain monitored
        self.positions.update(executed_positions)
        return all_rolled_back

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

        legs = [
            ("SHORT_CE", sym_short_ce, "SELL", self.lots),
            ("SHORT_PE", sym_short_pe, "SELL", self.lots),
        ]

        executed_positions: Dict[str, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = {executor.submit(self._execute_leg, lt, sym, act, qty): (lt, sym, act, qty)
                       for lt, sym, act, qty in legs}
            for future in as_completed(futures):
                sym, result = future.result()
                if result["status"] == "OPEN":
                    result["stop_loss"] = result["entry_price"] * (1.0 + self.straddle_sl_pct)
                    executed_positions[sym] = result
                    logger.info(f"  ✅ [{result['leg_type']}] {result['action']} {result['quantity']} {sym} established @ ${result['entry_price']:.2f} (SL: ${result['stop_loss']:.2f})")
                else:
                    logger.error(f"  ❌ Leg execution failed for [{result['leg_type']}] {result['action']} {sym}. Triggering ATOMIC ROLLBACK...")

        if len(executed_positions) < len(legs):
            logger.warning(f"Incomplete Straddle ({len(executed_positions)}/{len(legs)} legs). Rolling back...")
            rollback_ok = self._rollback_failed_entry(executed_positions)
            if not rollback_ok:
                self.trade_active = True
                self.trade_taken_today = False
                self._save_state()
                logger.error("❌ Straddle establishment aborted due to incomplete rollback. STUCK positions flagged for manual review.")
                send_alert("Straddle entry rollback FAILED. STUCK positions require immediate manual review.", level="critical")
                return False
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
        self.net_credit_collected = (sc_prem + sp_prem) * self.lots * self.multiplier
        self.target_profit = self.net_credit_collected * self.target_decay_pct

        logger.info("=" * 80)
        logger.info(f"🎉 2-LEG ATM STRADDLE FULLY ESTABLISHED & VERIFIED!")
        logger.info(f"Total Premium Collected: ~${self.net_credit_collected:.2f} USD (~Rs {self.net_credit_collected * self.usd_inr_rate:,.2f} INR)")
        logger.info(f"Target Profit ({self.target_decay_pct*100:.0f}% Decay): ~${self.target_profit:.2f} USD (~Rs {self.target_profit * self.usd_inr_rate:,.2f} INR)")
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
                    return None
        except Exception as e:
            logger.debug(f"Positionbook check for {symbol} notice: {e}")
        return None

    def reconcile_positions_with_broker(self):
        """
        Queries live positionbook from OpenAlgo and verifies active positions.
        Aggregates net quantity across records to prevent 0-qty overwrite.
        Only reconciles local state to CLOSED if an exit was already attempted.
        Uses strategy-scoped symbol filtering.
        """
        if self.dry_run or not self.positions:
            return

        try:
            url = f"{self.host}/api/v1/positionbook"
            resp = requests.post(url, json={"apikey": self.api_key}, timeout=4)
            if resp.status_code == 200:
                data = resp.json().get("data", [])
                if isinstance(data, list):
                    broker_net_qty: Dict[str, float] = {}
                    strategy_symbols = self._get_strategy_symbols_from_orderbook()
                    for p in data:
                        if isinstance(p, dict) and p.get("symbol"):
                            sym = p.get("symbol")
                            if sym not in strategy_symbols and sym not in self.positions:
                                continue
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
                            elif abs(live_qty) > 0.0:
                                # Update actual quantity if broker differs from local record
                                actual_qty = abs(int(live_qty))
                                if actual_qty != pos.get("quantity"):
                                    logger.warning(
                                        f"🔄 [RECONCILIATION] Quantity mismatch for {sym}: local {pos.get('quantity')} vs broker {actual_qty}. "
                                        f"Updating local record to actual."
                                    )
                                    pos["quantity"] = actual_qty
                                    changed = True

                    all_closed_or_stuck = all(p.get("status") in ("CLOSED", "STUCK") for p in self.positions.values())
                    if all_closed_or_stuck and self.positions:
                        logger.info("🔄 [RECONCILIATION] All tracked positions are confirmed flat/stuck on broker. Setting trade_active = False.")
                        self.trade_active = False
                        changed = True

                    if changed:
                        self._save_state()
        except Exception as e:
            logger.debug(f"Position reconciliation check notice: {e}")

    def _get_quote_cached(self, symbol: str) -> Dict[str, Optional[float]]:
        """Fetch full quote data (ask, bid, ltp, spread) with 3s TTL cache."""
        now_ts = time.time()
        if not hasattr(self, "_quote_cache"):
            self._quote_cache: Dict[str, tuple] = {}
        cached = self._quote_cache.get(symbol)
        if cached and (now_ts - cached[1]) < 3.0:
            return cached[0]
        qd = self.get_quote_data(symbol)
        if qd.get("ask") or qd.get("ltp"):
            self._quote_cache[symbol] = (qd, now_ts)
        return qd

    def _get_ltp_cached(self, symbol: str) -> Optional[float]:
        """Fetch LTP with 3s TTL cache to avoid hammering the quotes API every 5s tick."""
        qd = self._get_quote_cached(symbol)
        return qd.get("ltp") or qd.get("ask")

    def monitor_stuck_positions(self):
        """
        Alert operator repeatedly about any STUCK positions until resolved.
        This ensures failed rollbacks or square-offs are never silently forgotten.
        """
        stuck = [(sym, pos) for sym, pos in self.positions.items() if pos.get("status") == "STUCK"]
        if not stuck:
            return
        for sym, pos in stuck:
            reason = pos.get("stuck_reason", "UNKNOWN")
            send_alert(
                f"STUCK POSITION PENDING: {sym} ({pos.get('leg_type')}) | Reason: {reason} | "
                f"Qty: {pos.get('quantity')} | Entry: ${pos.get('entry_price', 0):.2f}. "
                "Manual broker intervention or position review required.",
                level="critical",
            )

    def manage_active_positions(self):
        # Alert on stuck positions first
        self.monitor_stuck_positions()

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

        # 1b. Portfolio Max Daily Loss Circuit Breaker Check (Rs 4,500)
        total_unrealized_usd = 0.0
        for s, p in self.positions.items():
            if p.get("status") == "OPEN":
                mult = self.multiplier
                qty = p.get("quantity", 0)
                qd = self._get_quote_cached(s)
                if p.get("action") == "SELL":
                    px = qd.get("ask") if qd.get("ask") is not None else qd.get("ltp")
                    if px is not None and px >= 0:
                        total_unrealized_usd += (p.get("entry_price", 0.0) - px) * mult * qty
                else:
                    px = qd.get("bid") if qd.get("bid") is not None else (qd.get("ltp") or 0.0)
                    total_unrealized_usd += (px - p.get("entry_price", 0.0)) * mult * qty
        if total_unrealized_usd < 0:
            loss_inr = -total_unrealized_usd * self.usd_inr_rate
            if loss_inr >= MAX_DAILY_LOSS_INR:
                logger.error(f"🚨 [PORTFOLIO HALT] MAX_DAILY_LOSS_INR breached (-Rs {loss_inr:,.2f} >= Rs {MAX_DAILY_LOSS_INR:,.0f}). Squaring off all legs!")
                send_alert(f"PORTFOLIO HALT: Max daily loss Rs {loss_inr:,.0f} >= Rs {MAX_DAILY_LOSS_INR:,.0f}. Flattening all legs.", level="critical")
                self.square_off_all("MAX_DAILY_LOSS_CIRCUIT_BREAKER")
                return

        # 1c. Basket Stop Loss Check (-1.0x Net Credit Collected - Solution 1)
        if self.enable_basket_sl and getattr(self, "net_credit_collected", 0.0) > 0:
            basket_sl_thresh = -1.0 * self.net_credit_collected * self.basket_sl_mult
            if total_unrealized_usd <= basket_sl_thresh:
                logger.warning(
                    f"🚨 [BASKET STOP LOSS HIT] Total unrealized PnL ${total_unrealized_usd:.2f} <= "
                    f"-{self.basket_sl_mult:.1f}x credit (${basket_sl_thresh:.2f}). Squaring off full basket!"
                )
                send_alert(
                    f"BASKET STOP LOSS: Unrealized PnL ${total_unrealized_usd:.2f} <= "
                    f"-{self.basket_sl_mult:.1f}x credit (${basket_sl_thresh:.2f}). Squaring off.",
                    level="warning"
                )
                self.square_off_all("BASKET_SL_HIT")
                return

        # 2. Target Profit Check (Theta Decay Captured or Dollar Target Achieved)
        target_dollar = getattr(self, "target_profit", 0.0)
        dollar_tp_hit = (target_dollar > 0 and total_unrealized_usd >= target_dollar)

        short_legs = [p for p in self.positions.values() if p["status"] == "OPEN" and p["action"] == "SELL"]
        short_decay_hit = False
        decay_pct = 0.0
        if short_legs:
            initial_short_prem = sum(p["entry_price"] for p in short_legs)
            current_short_prem = 0.0
            quotes_ok = True
            for pos in short_legs:
                sym = [s for s, p in self.positions.items() if p == pos][0]
                qd = self._get_quote_cached(sym)
                # Use LTP first to avoid resting wide asks blocking TP, fall back to ask
                px = qd.get("ltp") if (qd.get("ltp") and qd.get("ltp") > 0) else qd.get("ask")
                if px is not None and px >= 0.0:
                    current_short_prem += px
                else:
                    quotes_ok = False

            decay_threshold = initial_short_prem * (1.0 - self.target_decay_pct)
            if quotes_ok and initial_short_prem > 0 and current_short_prem <= decay_threshold:
                short_decay_hit = True
                decay_pct = ((initial_short_prem - current_short_prem) / initial_short_prem) * 100.0

        if short_decay_hit or dollar_tp_hit:
            reason_detail = f"Short decay {decay_pct:.1f}% >= {self.target_decay_pct*100:.0f}%" if short_decay_hit else f"Dollar profit ${total_unrealized_usd:.2f} >= Target ${target_dollar:.2f}"
            logger.info(f"🎯 [TARGET PROFIT REACHED] {reason_detail}. Squaring off all legs to lock in profit!")
            self.square_off_all("TARGET_PROFIT_DECAY")
            return

        # 3. Check Stop Loss on Short Legs with Verified Unwind (Solution 1: bypassed when Basket SL active)
        for sym, pos in list(self.positions.items()):
            if self.disable_leg_sl:
                break
            if pos["status"] != "OPEN" or pos["action"] != "SELL":
                continue

            qd = self._get_quote_cached(sym)
            ask = qd.get("ask") or qd.get("ltp")
            spread = qd.get("spread")
            sl = pos["stop_loss"]

            if ask and sl > 0:
                # Anomaly/spray guard: ignore if bid-ask spread > 35%
                if spread is not None and spread > 0.35:
                    logger.warning(
                        f"⚠️ [SL SPREAD GUARD] {sym} Ask ${ask:.2f} >= SL ${sl:.2f} but spread {spread*100:.1f}% > 35%. "
                        "Skipping anomalous tick."
                    )
                    continue

                self.sl_confirmer.add_tick(sym, ask=ask, spread=spread if spread is not None else 0.05)
                sl_ok, sl_why = self.sl_confirmer.triggered(sym, sl)
                if not sl_ok:
                    continue

                logger.warning(f"🚨 [STOP LOSS HIT - {sl_why}] {sym} Ask ${ask:.2f} >= SL ${sl:.2f} (Spread: {spread*100 if spread else 0:.1f}%). Closing tested side!")

                pos["exit_attempted"] = True

                # Step A: Close tested short leg with fill verification and flat check
                live_qty = self._get_broker_net_qty(sym)
                if live_qty is not None and abs(live_qty) == 0.0 and pos.get("exit_attempted"):
                    pos["status"] = "CLOSED"
                    pos["exit_reason"] = "ALREADY_FLAT"
                    logger.info(f"  ℹ️ {sym} is already FLAT on broker (0 qty). Skipping SL exit order.")
                    closed_short = True
                else:
                    qty_to_close = abs(int(live_qty)) if (live_qty is not None and abs(live_qty) > 0) else pos["quantity"]
                    closed_short, avg_p, _, _ = self.place_and_verify_order(sym, "BUY", qty_to_close, timeout_sec=8)
                    if not closed_short:
                        logger.warning(f"  ⚠️ SL exit for short leg {sym} timed out! Retrying with 10s window...")
                        closed_short, avg_p, _, _ = self.place_and_verify_order(sym, "BUY", qty_to_close, timeout_sec=10)

                    if closed_short:
                        pos["status"] = "CLOSED"
                        pos["exit_price"] = avg_p if avg_p > 0 else ask
                        pos["exit_reason"] = "SL_HIT"
                        logger.info(f"  ✅ Confirmed closed tested short leg {sym} @ ${pos['exit_price']:.2f}")
                    else:
                        pos["status"] = "STUCK"
                        pos["stuck_reason"] = "SL_EXIT_FAILED"
                        logger.error(f"  ❌ Could not confirm fill for closing short leg {sym}. Marked STUCK.")
                        send_alert(f"STUCK POSITION: {sym} - SL exit failed at Ask ${ask:.2f} >= SL ${sl:.2f}. Manual review required.", level="critical")

                # Step B: Close corresponding hedge wing with fill verification and retry
                wing_type = "LONG_CE" if pos["leg_type"] == "SHORT_CE" else "LONG_PE"
                for w_sym, w_pos in self.positions.items():
                    if w_pos["leg_type"] == wing_type and w_pos["status"] == "OPEN":
                        w_pos["exit_attempted"] = True
                        w_live_qty = self._get_broker_net_qty(w_sym)
                        if w_live_qty is not None and abs(w_live_qty) == 0.0 and w_pos.get("exit_attempted"):
                            w_pos["status"] = "CLOSED"
                            w_pos["exit_reason"] = "ALREADY_FLAT"
                            logger.info(f"  ℹ️ Protective wing {w_sym} is already FLAT on broker (0 qty). Skipping exit order.")
                        else:
                            w_qty_to_close = abs(int(w_live_qty)) if (w_live_qty is not None and abs(w_live_qty) > 0) else w_pos["quantity"]
                            closed_wing, w_avg_p, _, _ = self.place_and_verify_order(w_sym, "SELL", w_qty_to_close, timeout_sec=8)
                            if not closed_wing:
                                logger.warning(f"  ⚠️ Protective wing {w_sym} exit timed out! Retrying with 10s window...")
                                closed_wing, w_avg_p, _, _ = self.place_and_verify_order(w_sym, "SELL", w_qty_to_close, timeout_sec=10)

                            if closed_wing:
                                w_pos["status"] = "CLOSED"
                                w_pos["exit_price"] = w_avg_p if w_avg_p > 0 else (self._get_ltp_cached(w_sym) or 0.0)
                                w_pos["exit_reason"] = "WING_UNWIND"
                                logger.info(f"  ✅ Confirmed closed protective wing {w_sym} after short SL breach @ ${w_pos['exit_price']:.2f}.")
                            else:
                                w_pos["status"] = "STUCK"
                                w_pos["stuck_reason"] = "WING_UNWIND_FAILED"
                                logger.error(f"  ❌ Wing {w_sym} exit failed to fill. Marked STUCK.")
                                send_alert(f"STUCK POSITION: {w_sym} - wing unwind failed after SL on {sym}. Manual review required.", level="critical")

                self._save_state()

        # Check if all legs are closed (ignore stuck)
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
                if live_qty is not None and abs(live_qty) == 0.0 and pos.get("exit_attempted"):
                    pos["status"] = "CLOSED"
                    pos["exit_reason"] = "ALREADY_FLAT"
                    logger.info(f"  ℹ️ {sym} is already flat on broker (0 qty). Skipping order.")
                    continue
                qty_to_close = abs(int(live_qty)) if (live_qty is not None and abs(live_qty) > 0) else pos["quantity"]

                success, s_avg_p, _, _ = self.place_and_verify_order(sym, close_action, qty_to_close, timeout_sec=8)
                if not success:
                    logger.warning(f"  ⚠️ Square-off did not confirm fill for {sym}, retrying with 10s window...")
                    success, s_avg_p, _, _ = self.place_and_verify_order(sym, close_action, qty_to_close, timeout_sec=10)

                if success:
                    pos["status"] = "CLOSED"
                    pos["exit_price"] = s_avg_p if s_avg_p > 0 else (self._get_ltp_cached(sym) or 0.0)
                    pos["exit_reason"] = reason
                    logger.info(f"  ✅ Closed {sym} ({close_action} {qty_to_close}) @ ${pos['exit_price']:.2f} - Reason: {reason}")
                else:
                    # If this was a long wing (BUY) being closed (SELL) and market has zero bids,
                    # the short legs are ALREADY closed (due to sorted_legs processing shorts first).
                    # A worthless expiring long wing cannot create naked risk or margin breach.
                    is_wing = (pos.get("leg_type") in ("LONG_CE", "LONG_PE") or pos.get("action") == "BUY")
                    wing_qd = self._get_quote_cached(sym)
                    wing_bid = wing_qd.get("bid")
                    if is_wing and (wing_bid is None or wing_bid <= 0.0):
                        pos["status"] = "CLOSED"
                        pos["exit_price"] = 0.0
                        pos["exit_reason"] = f"{reason}_WORTHLESS_WING"
                        logger.info(f"  ℹ️ Long wing {sym} has zero bids (worthless). Short legs already flat; marking wing closed at $0.00.")
                    else:
                        pos["status"] = "STUCK"
                        pos["stuck_reason"] = f"SQUARE_OFF_FAILED_{reason}"
                        logger.error(f"  ❌ Square-off failed for {sym}. Marked STUCK.")
                        send_alert(f"STUCK POSITION: {sym} - square-off failed ({reason}). Manual broker intervention required.", level="critical")

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

        # Single startup audit point (removed duplicate from __init__)
        self.audit_and_recover_positions_on_startup()

        last_hb = time.time()
        while not self.shutdown_event:
            now = get_current_ist_time()
            now_epoch = time.time()

            # Midnight Rollover Check (00:00 next day)
            today_str = get_current_ist_datetime().strftime("%Y-%m-%d")
            if today_str != self.current_date:
                logger.info(
                    f"📅 [MIDNIGHT ROLLOVER] Calendar day changed from {self.current_date} to {today_str}. "
                    f"Resetting daily state for Session 1."
                )
                self.current_date = today_str
                self.current_session = 1
                self.session_history = []
                self.trade_taken_today = False
                self.trade_active = False
                self.positions = {}
                self.net_credit_collected = 0.0
                self.target_profit = 0.0
                self.expiry_date = None
                self.entry_time = None
                self.entry_attempts = 0
                self.last_attempt_time = 0.0
                self._save_state()

            # Heartbeat and broker reconciliation every 60s
            if now_epoch - last_hb >= 60.0:
                last_hb = now_epoch
                if self.trade_active:
                    self.reconcile_positions_with_broker()
                open_count = sum(1 for p in self.positions.values() if p.get("status") == "OPEN")
                stuck_count = sum(1 for p in self.positions.values() if p.get("status") == "STUCK")
                if self.trade_active:
                    status_str = f"ACTIVE SESSION {self.current_session}/{MAX_DAILY_SESSIONS} (Monitoring {open_count} Legs, {stuck_count} Stuck)"
                elif self.trade_taken_today:
                    status_str = f"DONE TODAY ({self.current_session} session(s) completed)"
                elif self.entry_attempts >= self.max_entry_attempts:
                    elapsed = now_epoch - self.last_attempt_time
                    cd_left = max(0, int(self.batch_cooldown_sec - elapsed))
                    mins_left = cd_left // 60
                    secs_left = cd_left % 60
                    status_str = f"MAX ATTEMPTS ({self.max_entry_attempts}) REACHED (Cooling down {mins_left}m {secs_left}s before retry batch)"
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
                        elapsed = now_epoch - self.last_attempt_time
                        if elapsed >= self.batch_cooldown_sec:
                            logger.info(
                                f"🔄 [BATCH COOLDOWN ELAPSED] {int(self.batch_cooldown_sec / 60)} minutes elapsed since max attempts reached. "
                                f"Resetting attempt counter (0/{self.max_entry_attempts}) to retry Session {self.current_session}!"
                            )
                            self.entry_attempts = 0
                            self._save_state()
                        else:
                            if now_epoch - self.last_attempt_log >= 180.0:
                                rem_sec = max(0, int(self.batch_cooldown_sec - elapsed))
                                logger.info(
                                    f"⏳ Maximum entry attempts ({self.max_entry_attempts}) reached for Session {self.current_session}. "
                                    f"Batch cooldown active: {rem_sec // 60}m {rem_sec % 60}s remaining before retrying automatically."
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

            # 1s sleep slices for graceful SIGTERM shutdown
            for _ in range(5):
                if getattr(self, "_stop_requested", False):
                    break
                time.sleep(1)
            if getattr(self, "_stop_requested", False):
                logger.info("🛑 Strategy loop exiting cleanly on shutdown request.")
                break


# ------------------------------------------------------------------------------
# 4. CLI ENTRY POINT
# ------------------------------------------------------------------------------
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="BTC Daily Options Strategy Engine (Delta Exchange)")
    parser.add_argument("--mode", type=str, choices=["condor", "straddle"], default=STRATEGY_MODE, help="Architecture mode: condor (Arch A2) or straddle (Arch B3)")
    parser.add_argument("--lots", type=int, default=DEFAULT_LOTS, help="Number of contracts to trade (default: 92)")
    parser.add_argument("--capital", type=float, default=CAPITAL_BASE_INR, help="Base capital in INR (default: 20000.0)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate orders without placing real orders")
    parser.add_argument("--test", action="store_true", help="Execute one-shot test entry and immediate square-off")
    parser.add_argument("--force", action="store_true", help="Force entry by resetting trade_taken_today and attempt counters")
    args = parser.parse_args()

    lock_handle = acquire_singleton_lock()
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

    def _sig(signum: int, _frame: Any) -> None:
        logger.info(f"Signal {signum} received. Stopping cleanly...")
        strategy._stop_requested = True

    try:
        signal.signal(signal.SIGINT, _sig)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass

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
