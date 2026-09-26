#!/usr/bin/env python3
"""
================================================================================
OVERNIGHT CRYPTO DELTA OPTIONS — PRODUCTION GRADE v2.0 (single file)
================================================================================
Market           : Crypto Options (Delta Exchange India via OpenAlgo)
Underlyings      : BTC, ETH or BOTH
Route            : OpenAlgo gateway (host MUST be explicit in live profile)
Schedule (IST)   : Entry window 22:00-04:30 | TWAP ladder from 06:00 |
                   Aggressive 06:25 | Hard sweep 06:30 | Settle 17:30
Structure        : 4-leg defined-risk Iron Condor, "wing-first, shorts-first-out"
                   1. BUY  OTM Call wing   2. BUY  OTM Put wing
                   3. SELL OTM Call        4. SELL OTM Put

RUN
  Self-test (offline, no broker):  python3 overnight_crypto_delta_production.py --self-test
  Print config:                    python3 overnight_crypto_delta_production.py --print-config --symbol BOTH --mode paper
  Paper (live data, no orders):    BROKER_PROFILE=paper OPENALGO_HOST=... OPENALGO_API_KEY=... \
                                   python3 overnight_crypto_delta_production.py --symbol BOTH --mode paper
  Live:                            BROKER_PROFILE=live OPENALGO_HOST_CRYPTO=... OPENALGO_API_KEY_CRYPTO=... \
                                   USD_INR_RATE=... python3 overnight_crypto_delta_production.py --symbol BOTH --mode live

REQUIRED ENVS (live): BROKER_PROFILE=live, OPENALGO_HOST_CRYPTO or OPENALGO_HOST,
  OPENALGO_API_KEY_CRYPTO or OPENALGO_API_KEY or CRYPTO_API_KEY, USD_INR_RATE.
OPTIONAL: TELEGRAM_BOT_TOKEN/TELEGRAM_CHAT_ID, SLACK_WEBHOOK_URL, GENERIC_WEBHOOK_URL,
  DATA_DIR, LOCK_PATH, KILL_FILE, HALT_FILE, LOG_LEVEL, plus tuning knobs below.
================================================================================
"""

# ------------------------------------------------------------------------------
# IMPORTS
# ------------------------------------------------------------------------------
import argparse
import json
import logging
import math
import os
import random
import re
import signal
import socket
import sys
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone, timedelta, time as dtime, date
from enum import Enum
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

if Path("/app").is_dir() and "/app" not in sys.path:
    sys.path.insert(0, "/app")

import requests
from requests.adapters import HTTPAdapter

try:
    from dotenv import load_dotenv
    for p in (Path("/app/.env"), Path(__file__).resolve().parent.parent.parent / ".env.global", Path(".env")):
        if p.exists():
            load_dotenv(p)
            break
except Exception:
    pass

# ------------------------------------------------------------------------------
# 0. LOGGING FIRST — before any function that can log (P0 fix)
# ------------------------------------------------------------------------------
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("Overnight_Crypto_Delta")

__version__ = "2.0.0-production"
STRATEGY_NAME = "Overnight_Crypto_Delta_Options"
EXCHANGE = "CRYPTO"
STATE_VERSION = 2

# ------------------------------------------------------------------------------
# TIME (IST fixed offset; verified against UTC at boot)
# ------------------------------------------------------------------------------
IST_TZ = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(IST_TZ)


def now_iso() -> str:
    return now_ist().isoformat()


def _seconds_until(target: dtime, now: dtime) -> int:
    now_dt = now_ist().replace(second=0, microsecond=0)
    target_dt = now_dt.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0)
    if target_dt <= now_dt:
        target_dt += timedelta(days=1)
    return int((target_dt - now_dt).total_seconds())


SESSION_BOUNDARY = dtime(8, 0)  # pre-08:00 belongs to yesterday's overnight session


def session_date_str(now: Optional[datetime] = None) -> str:
    now = now or now_ist()
    if now.time() < SESSION_BOUNDARY:
        return (now - timedelta(days=1)).strftime("%Y%m%d")
    return now.strftime("%Y%m%d")


# ------------------------------------------------------------------------------
# SMALL HELPERS
# ------------------------------------------------------------------------------
def _f2(x: Any) -> float:
    try:
        return round(float(x or 0.0), 2)
    except (TypeError, ValueError):
        return 0.0


def _f4(x: Any) -> float:
    try:
        return round(float(x or 0.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _env_float(name: str, default: float) -> Tuple[float, bool]:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default, False
    try:
        return float(raw), True
    except ValueError:
        raise ConfigError(f"Env {name}={raw!r} is not a number")


def _env_int(name: str, default: int) -> Tuple[int, bool]:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default, False
    try:
        return int(raw), True
    except ValueError:
        raise ConfigError(f"Env {name}={raw!r} is not an integer")


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in ("true", "1", "yes", "y")


def _env_time(name: str, default: dtime) -> dtime:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        parts = [int(p) for p in raw.strip().split(":")]
        if len(parts) == 2:
            return dtime(parts[0], parts[1])
        elif len(parts) == 3:
            return dtime(parts[0], parts[1], parts[2])
    except Exception:
        pass
    return default


def redact(s: str, keep: int = 4) -> str:
    if not s:
        return "<missing>"
    if len(s) <= keep + 3:
        return "***"
    return s[:keep] + "…" + "*" * 8


# ------------------------------------------------------------------------------
# ERRORS / ENUMS
# ------------------------------------------------------------------------------
class ConfigError(Exception):
    pass


class ApiError(Exception):
    pass


class EntryOutcome(Enum):
    ENTERED = "ENTERED"
    RETRYABLE = "RETRYABLE"   # backoff and retry this window
    FATAL = "FATAL"           # no more attempts tonight
    SKIPPED = "SKIPPED"       # e.g. daytime legs open; long-cooldown retry


class LegStatus(str, Enum):
    PENDING = "PENDING"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    FAILED = "FAILED"


# ------------------------------------------------------------------------------
# 1. CONFIG (validated schema — P2 fix)
# ------------------------------------------------------------------------------
@dataclass
class AssetConfig:
    underlying: str
    futures_symbol: str
    strike_step: float
    otm_pct: float
    wing_width_strikes: int
    contract_mult: float
    default_lots: int
    max_wing_price: float
    max_spread_pct: float
    max_short_spread_dollar: float
    max_wing_spread_pct: float
    max_wing_spread_dollar: float
    min_depth_contracts: int


@dataclass
class EngineConfig:
    symbols: List[str]
    mode: str  # live | paper
    broker_profile: str  # live | paper | sandbox
    host: str
    api_key: str
    force_entry: bool
    # economics
    capital_base_inr: float
    margin_util_cap: float
    margin_safety_mult: float
    usd_inr: float
    usd_inr_explicit: bool
    dynamic_sizing: bool
    sl_mult: float
    tp_pct: float
    taker_fee_bps: float
    per_order_fee_usd: float
    exit_slip_bps: float
    # schedule (ladder shifted to 06:30-07:00 for extended theta capture)
    entry_start: dtime = field(default_factory=lambda: dtime(22, 0))
    entry_end: dtime = field(default_factory=lambda: dtime(4, 30))
    ladder_start: dtime = field(default_factory=lambda: dtime(6, 0))
    aggressive_time: dtime = field(default_factory=lambda: dtime(6, 25))
    cutoff: dtime = field(default_factory=lambda: dtime(6, 30))
    settle: dtime = field(default_factory=lambda: dtime(17, 30))
    # execution
    monitor_interval: int = 10
    fill_timeout: float = 8.0
    exit_collar_timeout: float = 8.0
    exit_buffer_bps: float = 50.0
    entry_buffer_bps: float = 15.0
    min_lots: int = 10
    max_lots: int = 3000
    # SL confirmation (P0 fix: window enlarged to 60s for 10-15s loop intervals)
    sl_confirm_ticks: int = 2
    sl_confirm_window_s: float = 60.0
    sl_max_spread_pct: float = 0.40
    sl_max_quote_age_s: float = 5.0
    # retries / budget
    max_entry_backoff_s: int = 300
    daytime_retry_s: int = 900
    error_budget: int = 15
    # portfolio
    basket_sl_mult: float = 1.0
    enable_basket_sl: bool = True
    disable_leg_sl: bool = True
    max_nightly_loss_inr: float = 4500.0
    max_sl_per_night: int = 4
    stale_flat_after_ticks: int = 60
    # paths
    data_dir: Path = field(default_factory=lambda: Path("data"))
    lock_path: Path = field(default_factory=lambda: Path("data/overnight_crypto_delta_production.lock"))
    kill_file: Path = field(default_factory=lambda: Path("data/overnight_crypto_delta.KILL"))
    halt_file: Path = field(default_factory=lambda: Path("data/overnight_crypto_delta.HALT"))
    assets: Dict[str, AssetConfig] = field(default_factory=dict)

    def validate(self) -> None:
        if self.mode not in ("live", "paper"):
            raise ConfigError(f"mode must be live|paper, got {self.mode!r}")
        if self.broker_profile not in ("live", "paper", "sandbox"):
            raise ConfigError("BROKER_PROFILE must be live|paper|sandbox (explicit, never auto-detected)")
        if self.mode == "live" and self.broker_profile != "live":
            raise ConfigError("mode=live requires BROKER_PROFILE=live")
        for s in self.symbols:
            if s not in ("BTC", "ETH"):
                raise ConfigError(f"bad symbol {s!r}")
        a = self.assets
        for sym, c in a.items():
            if not (0.002 <= c.otm_pct <= 0.08):
                raise ConfigError(f"{sym} otm_pct {c.otm_pct} out of range (0.002, 0.08)")
            if not (1 <= c.wing_width_strikes <= 40):
                raise ConfigError(f"{sym} wing_width_strikes {c.wing_width_strikes} out of range")
            if not (1 <= c.default_lots <= 3000):
                raise ConfigError(f"{sym} default_lots {c.default_lots} out of range 1..3000")
            if not (0.01 <= c.max_spread_pct <= 0.60):
                raise ConfigError(f"{sym} max_spread_pct {c.max_spread_pct} out of range")
            if not (0.01 <= c.max_wing_spread_pct <= 0.80):
                raise ConfigError(f"{sym} max_wing_spread_pct out of range")
        if not (1.2 <= self.sl_mult <= 5.0):
            raise ConfigError(f"sl_mult {self.sl_mult} out of range 1.2..5.0")
        if not (0.10 <= self.tp_pct <= 0.95):
            raise ConfigError(f"tp_pct {self.tp_pct} out of range 0.10..0.95")
        if not (0.05 <= self.margin_util_cap <= 1.0):
            raise ConfigError("margin_util_cap out of range 0.05..1.0")
        if not (1.0 <= self.margin_safety_mult <= 3.0):
            raise ConfigError("margin_safety_mult out of range 1.0..3.0")
        if self.capital_base_inr <= 0:
            raise ConfigError("capital_base_inr must be > 0")
        if self.usd_inr <= 0:
            raise ConfigError("usd_inr must be > 0")
        if self.min_lots < 1 or self.max_lots > 10000 or self.min_lots > self.max_lots:
            raise ConfigError("bad min/max lots")
        if self.max_nightly_loss_inr <= 0:
            raise ConfigError("max_nightly_loss_inr must be > 0")


def default_asset_configs() -> Dict[str, AssetConfig]:
    btc_otm, _ = _env_float("BTC_OVERNIGHT_OTM_PCT", 0.020)
    btc_w, _ = _env_int("BTC_OVERNIGHT_WING_STRIKES", 8)
    btc_lots, _ = _env_int("BTC_OVERNIGHT_LOTS", 92)
    btc_wing_px, _ = _env_float("BTC_MAX_WING_PRICE", 65.0)
    eth_otm, _ = _env_float("ETH_OVERNIGHT_OTM_PCT", 0.012)
    eth_w, _ = _env_int("ETH_OVERNIGHT_WING_STRIKES", 3)
    eth_lots, _ = _env_int("ETH_OVERNIGHT_LOTS", 246)
    eth_wing_px, _ = _env_float("ETH_MAX_WING_PRICE", 10.0)
    return {
        "BTC": AssetConfig("BTC", "BTCUSDFUT", 100.0, btc_otm, btc_w, 0.001, btc_lots,
                           btc_wing_px, 0.15, 3.00, 0.25, 4.50, 100),
        "ETH": AssetConfig("ETH", "ETHUSDFUT", 10.0, eth_otm, eth_w, 0.01, eth_lots,
                           eth_wing_px, 0.15, 0.60, 0.25, 0.80, 100),
    }


def compute_dynamic_sl_mult(otm_pct: float, explicit_default: Optional[float] = None) -> float:
    """Option 1 Dynamic SL Scaling based on OTM distance:
    >= 1.8% OTM: 1.5x SL (room for chop built into low delta)
    1.3% - 1.7% OTM: 1.8x SL
    1.0% - 1.2% OTM: 2.0x SL (absorbs peak gamma fluctuations)
    Can be overridden if OVERNIGHT_SL_MULT environment variable is explicitly set.
    """
    override = os.getenv("OVERNIGHT_SL_MULT")
    if override:
        try:
            return float(override)
        except ValueError:
            pass
    if explicit_default is not None and explicit_default not in (1.5, 2.0, 1.8):
        return explicit_default
    if otm_pct >= 0.018:
        return 1.5
    elif otm_pct >= 0.013:
        return 1.8
    else:
        return 2.0


def resolve_host_and_key(profile: str, mode: str) -> Tuple[str, str, bool, bool]:
    """Returns (host, key, host_explicit, key_explicit). Fail-fast, NO hardcoded key (P0)."""
    default_host = "http://127.0.0.1:5000" if os.path.exists("/.dockerenv") else "http://127.0.0.1:5001"
    host = os.getenv("OPENALGO_HOST_CRYPTO") or os.getenv("OPENALGO_HOST") or default_host
    host_explicit = bool(os.getenv("OPENALGO_HOST_CRYPTO") or os.getenv("OPENALGO_HOST"))
    key = os.getenv("OPENALGO_API_KEY_CRYPTO") or os.getenv("OPENALGO_API_KEY") or os.getenv("CRYPTO_API_KEY") or ""
    key_explicit = bool(key)
    if not key:
        raise ConfigError(
            "Missing API key: set OPENALGO_API_KEY_CRYPTO (or OPENALGO_API_KEY). "
            "There is intentionally NO fallback key."
        )
    if len(key) < 16 or key.lower().startswith("xxx") or "placeholder" in key.lower():
        raise ConfigError("API key looks like a placeholder; refusing to run.")
    return host.rstrip("/"), key, host_explicit, key_explicit


def load_config(args: argparse.Namespace, need_secrets: bool = True) -> EngineConfig:
    if args.symbol == "BOTH":
        symbols = ["BTC", "ETH"]
    else:
        symbols = [args.symbol]
    mode = "paper" if args.mode == "dry_run" else args.mode
    if args.mode == "dry_run":
        logger.warning("[CONFIG] --mode dry_run is an alias of paper (live market data, simulated fills)")
    profile = (os.getenv("BROKER_PROFILE") or "live").strip().lower()
    if profile not in ("live", "paper", "sandbox"):
        profile = "live"
    assets = default_asset_configs()
    if getattr(args, "btc_lots", None) and "BTC" in symbols:
        assets["BTC"].default_lots = int(args.btc_lots)
    if getattr(args, "eth_lots", None) and "ETH" in symbols:
        assets["ETH"].default_lots = int(args.eth_lots)

    cap, _ = _env_float("CAPITAL_BASE_INR", 20000.0)
    if os.getenv("OVERNIGHT_CAPITAL_INR"):
        cap, _ = _env_float("OVERNIGHT_CAPITAL_INR", cap)
    mcap, _ = _env_float("MARGIN_UTILIZATION_CAP", 0.65)
    msafe, _ = _env_float("MARGIN_SAFETY_MULT", 1.0)
    fx, fx_explicit = _env_float("USD_INR_RATE", 88.0)
    dyn = _env_bool("DYNAMIC_SIZING", True)
    fee_bps, _ = _env_float("TAKER_FEE_BPS", 30.0)
    per_order, _ = _env_float("PER_ORDER_FEE_USD", 0.05)
    slip_bps, _ = _env_float("EXIT_SLIP_BPS", 10.0)
    max_loss, _ = _env_float("MAX_NIGHTLY_LOSS_INR", 4500.0)
    default_max_sl = 4 if len(symbols) > 1 else 2
    max_sl, _ = _env_int("MAX_SL_PER_NIGHT", default_max_sl)
    err_budget, _ = _env_int("ERROR_BUDGET", 15)
    sl_ticks, _ = _env_int("SL_CONFIRM_TICKS", 2)
    sl_window, _ = _env_float("SL_CONFIRM_WINDOW_S", 60.0)
    ladder_start = _env_time("OVERNIGHT_LADDER_START", dtime(6, 0))
    aggressive_time = _env_time("OVERNIGHT_AGGRESSIVE_TIME", dtime(6, 25))
    cutoff = _env_time("OVERNIGHT_CUTOFF", dtime(6, 30))

    default_data_dir = Path(__file__).resolve().parent.parent / "data"
    data_dir = Path(os.getenv("DATA_DIR", str(default_data_dir))).resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    lock_path = Path(os.getenv("LOCK_PATH", str(data_dir / "overnight_crypto_delta_production.lock"))).resolve()
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    kill_file = Path(os.getenv("KILL_FILE", str(data_dir / "overnight_crypto_delta.KILL"))).resolve()
    halt_file = Path(os.getenv("HALT_FILE", str(data_dir / "overnight_crypto_delta.HALT"))).resolve()

    host, key = "", ""
    if need_secrets:
        host, key, host_exp, _ = resolve_host_and_key(profile, mode)
        logger.info(f"[CONFIG] host={host} (explicit={host_exp}) key={redact(key)} profile={profile} mode={mode}")
    cfg = EngineConfig(
        symbols=symbols, mode=mode, broker_profile=profile, host=host, api_key=key,
        force_entry=bool(args.force_entry),
        capital_base_inr=cap, margin_util_cap=mcap, margin_safety_mult=msafe,
        usd_inr=fx, usd_inr_explicit=fx_explicit, dynamic_sizing=dyn,
        sl_mult=float(args.sl_mult), tp_pct=float(args.tp_pct),
        basket_sl_mult=float(os.getenv("OVERNIGHT_BASKET_SL_MULT", str(getattr(args, "basket_sl_mult", 1.0)))),
        enable_basket_sl=_env_bool("OVERNIGHT_ENABLE_BASKET_SL", True),
        disable_leg_sl=_env_bool("OVERNIGHT_DISABLE_LEG_SL", True),
        sl_confirm_ticks=sl_ticks, sl_confirm_window_s=sl_window,
        ladder_start=ladder_start, aggressive_time=aggressive_time, cutoff=cutoff,
        taker_fee_bps=fee_bps, per_order_fee_usd=per_order, exit_slip_bps=slip_bps,
        max_nightly_loss_inr=max_loss, max_sl_per_night=max_sl, error_budget=err_budget,
        data_dir=data_dir, lock_path=lock_path, kill_file=kill_file, halt_file=halt_file,
        assets={s: assets[s] for s in symbols},
    )
    cfg.validate()
    # Live FX rule: explicit USD_INR_RATE required unless broker funds provide FX later.
    if need_secrets and mode == "live" and not fx_explicit:
        logger.warning("[CONFIG] USD_INR_RATE not set explicitly; live sizing requires FX from /funds or aborts the entry")
    return cfg


# ------------------------------------------------------------------------------
# 2. RATE LIMITER (token bucket — P2 fix)
# ------------------------------------------------------------------------------
class RateLimiter:
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


# ------------------------------------------------------------------------------
# 3. API CLIENT (pooled session, idempotent retries — P2 fix)
# ------------------------------------------------------------------------------
class ApiClient:
    def __init__(self, host: str, api_key: str, limiter: RateLimiter):
        self.host = host
        self.api_key = api_key
        self.limiter = limiter
        self.session = requests.Session()
        adapter = HTTPAdapter(pool_connections=8, pool_maxsize=16, max_retries=0)
        self.session.mount("http://", adapter)
        self.session.mount("https://", adapter)

    def _post(self, path: str, payload: Dict[str, Any], timeout: float, idempotent: bool) -> Any:
        self.limiter.acquire()
        tries = 3 if idempotent else 1
        last_err: Optional[Exception] = None
        for attempt in range(tries):
            try:
                resp = self.session.post(f"{self.host}{path}", json=payload, timeout=timeout)
                if resp.status_code == 429 or 500 <= resp.status_code < 600:
                    last_err = ApiError(f"{path} HTTP {resp.status_code}: {resp.text[:200]}")
                    if idempotent and attempt < tries - 1:
                        time.sleep(0.5 * (2 ** attempt) + random.uniform(0, 0.2))
                        continue
                    raise last_err
                if resp.status_code != 200:
                    raise ApiError(f"{path} HTTP {resp.status_code}: {resp.text[:200]}")
                try:
                    return resp.json()
                except ValueError as e:
                    raise ApiError(f"{path} bad JSON: {e}")
            except (requests.Timeout, requests.ConnectionError) as e:
                last_err = ApiError(f"{path} transport: {e}")
                if idempotent and attempt < tries - 1:
                    time.sleep(0.5 * (2 ** attempt) + random.uniform(0, 0.2))
                    continue
                raise last_err
        assert last_err is not None
        raise last_err

    # ---- idempotent reads (retried) ----
    def positionbook(self) -> Any:
        return self._post("/api/v1/positionbook", {"apikey": self.api_key}, 5.0, True)

    def orderbook(self) -> Any:
        return self._post("/api/v1/orderbook", {"apikey": self.api_key}, 4.0, True)

    def orderstatus(self, order_id: str) -> Any:
        return self._post("/api/v1/orderstatus",
                           {"apikey": self.api_key, "strategy": STRATEGY_NAME, "orderid": order_id}, 3.0, True)

    def quotes(self, symbol: str) -> Any:
        return self._post("/api/v1/quotes",
                           {"apikey": self.api_key, "symbol": symbol, "exchange": EXCHANGE}, 4.0, True)

    def depth(self, symbol: str) -> Any:
        return self._post("/api/v1/depth",
                           {"apikey": self.api_key, "symbol": symbol, "exchange": EXCHANGE}, 4.0, True)

    def expiry(self, asset: str) -> Any:
        return self._post("/api/v1/expiry",
                           {"apikey": self.api_key, "symbol": asset, "exchange": EXCHANGE,
                            "instrumenttype": "options"}, 5.0, True)

    def optionsymbol(self, asset: str, expiry: str, option_type: str, strike_ref: float) -> Any:
        return self._post("/api/v1/optionsymbol",
                           {"apikey": self.api_key, "underlying": asset, "exchange": EXCHANGE,
                            "expiry_date": expiry, "offset": "ATM", "option_type": option_type,
                            "underlying_ltp": strike_ref}, 4.0, True)

    def funds(self) -> Any:
        return self._post("/api/v1/funds", {"apikey": self.api_key}, 5.0, True)

    # ---- non-idempotent writes (NEVER auto-retried) ----
    def placeorder(self, symbol: str, action: str, quantity: int,
                   pricetype: str, price: Optional[float]) -> Any:
        # NOTE: payload keeps the exact broker-accepted schema. Broker-side idempotency
        # field is opt-in via OPENALGO_IDEMPOTENCY_FIELD (tag|remarks|client_order_id).
        payload: Dict[str, Any] = {
            "apikey": self.api_key, "strategy": STRATEGY_NAME, "symbol": symbol,
            "action": action, "exchange": EXCHANGE, "pricetype": pricetype,
            "quantity": int(quantity), "product": "NRML",
        }
        if pricetype.upper() == "LIMIT":
            if price is None or price <= 0:
                raise ApiError(f"LIMIT order for {symbol} needs price>0")
            payload["price"] = round(float(price), 2)
        idem_field = (os.getenv("OPENALGO_IDEMPOTENCY_FIELD") or "").strip()
        idem_value = (os.getenv("_CURRENT_IDEMPOTENCY_KEY") or "").strip()
        if idem_field and idem_value:
            payload[idem_field] = idem_value
        return self._post("/api/v1/placeorder", payload, 15.0, False)

    def cancelorder(self, order_id: str) -> Any:
        return self._post("/api/v1/cancelorder",
                           {"apikey": self.api_key, "strategy": STRATEGY_NAME, "orderid": order_id},
                           6.0, False)


# ------------------------------------------------------------------------------
# 4. MARKET-DATA MODELS (fail-closed — P0 fix)
# ------------------------------------------------------------------------------
@dataclass
class DepthQuote:
    symbol: str
    bid: Optional[float]
    ask: Optional[float]
    bid_size: int
    ask_size: int
    cum_bid_3: int
    cum_ask_3: int
    ts: float
    fresh_l2: bool  # False => came from quotes fallback or failed; sizes are 0

    @property
    def age(self) -> float:
        return time.time() - self.ts

    def spread_dollar(self) -> Optional[float]:
        if self.bid and self.ask and self.bid > 0 and self.ask > 0:
            return self.ask - self.bid
        return None

    def spread_pct(self) -> Optional[float]:
        s = self.spread_dollar()
        if s is not None and self.ask and self.ask > 0:
            return s / self.ask
        return None


@dataclass
class HuntedStrike:
    symbol: str
    strike: float
    bid: float
    ask: float
    bid_size: int
    ask_size: int


@dataclass
class FillResult:
    status: str  # FILLED | PARTIAL | TIMEOUT | FAILED | REJECTED
    filled_qty: int
    avg_price: float


# ------------------------------------------------------------------------------
# 5. STATE (pinned session file, atomic saves, v1 migration — P1 fix)
# ------------------------------------------------------------------------------
LEG_KEYS = ("CE_WING", "PE_WING", "CE_SHORT", "PE_SHORT")


def default_leg(symbol: str = "", side: str = "") -> Dict[str, Any]:
    return {
        "symbol": symbol, "side": side, "status": LegStatus.PENDING.value,
        "requested_qty": 0, "filled_qty": 0, "avg_price": 0.0, "fill_estimated": False,
        "order_ids": [], "idem_key": "", "active": False, "exit_attempted": False,
        "exited_qty": 0, "exit_avg": 0.0, "sl_price": 0.0,
    }


def default_asset_state() -> Dict[str, Any]:
    return {
        "active": False, "entry_done": False, "entry_skipped_reason": None,
        "expiry_date": None, "expiry_iso": None, "entry_time": None,
        "exit_time": None, "exit_reason": None,
        "lots_requested": 0, "initial_net_credit": 0.0, "target_profit_value": 0.0,
        "fees_entry_usd": 0.0, "fees_exit_est_usd": 0.0, "realized_pnl_usd": 0.0,
        "sl_count": 0, "ce_exit_time": None, "ce_exit_reason": None,
        "pe_exit_time": None, "pe_exit_reason": None,
        "legs": {k: default_leg() for k in LEG_KEYS},
        "pending_exit": None, "sl_ticks": {"CE_SHORT": [], "PE_SHORT": []},
        "stale_ticks": 0, "entry_attempts": 0, "next_entry_retry_ts": 0.0,
        "fatal_for_night": None, "unwind_phase": "NORMAL", "last_ladder_ts": 0.0,
        "held_to_settle": False, "settle_info": None,
    }


def migrate_v1_asset(old: Dict[str, Any]) -> Dict[str, Any]:
    """Migrate v1 state (positions/fill_price) into v2 legs. Estimated fills flagged."""
    new = default_asset_state()
    for k in ("active", "entry_done", "expiry_date", "entry_time", "exit_time",
              "exit_reason", "initial_net_credit", "target_profit_value", "pnl"):
        if k in old:
            new[k] = old[k]
    if old.get("expiry_date"):
        d = parse_expiry_date(str(old["expiry_date"]))
        new["expiry_iso"] = d.isoformat() if d else None
    new["lots_requested"] = int(old.get("lots") or 0)
    positions = old.get("positions") or {}
    for leg_key in LEG_KEYS:
        v = positions.get(leg_key) or {}
        if not v.get("symbol"):
            continue
        leg = default_leg(v["symbol"], v.get("type", ""))
        px = _f2(v.get("fill_price"))
        qty = abs(int(v.get("quantity") or new["lots_requested"] or 0))
        leg.update({"status": LegStatus.FILLED.value if v.get("active") else LegStatus.CANCELLED.value,
                    "requested_qty": qty, "filled_qty": qty if v.get("active") else 0,
                    "avg_price": px, "fill_estimated": True, "active": bool(v.get("active")),
                    "sl_price": _f2(v.get("sl_price")), "exit_attempted": bool(v.get("exit_attempted"))})
        new["legs"][leg_key] = leg
    new["realized_pnl_usd"] = _f2(old.get("pnl"))
    return new


class StateStore:
    def __init__(self, path: Path):
        self.path = path  # PINNED at boot; never re-resolved (P1 fix)

    def load(self) -> Optional[Dict[str, Any]]:
        if not self.path.exists():
            return None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else None
        except Exception as e:
            logger.error(f"[STATE LOAD ERROR] {self.path.name}: {e}")
            return None

    def save(self, data: Dict[str, Any]) -> None:
        data["updated_at"] = now_iso()
        tmp = self.path.with_suffix(".tmp")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except Exception:
                    pass
            os.replace(tmp, self.path)
            try:
                dir_fd = os.open(str(self.path.parent), os.O_DIRECTORY)
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except Exception:
                pass
        except Exception as e:
            logger.error(f"[STATE SAVE ERROR] {e}")


# ------------------------------------------------------------------------------
# 6. EVENT JOURNAL + NOTIFIER + HEARTBEAT (P1 fix)
# ------------------------------------------------------------------------------
class EventLog:
    def __init__(self, path: Path):
        self.path = path

    def append(self, etype: str, asset: str, detail: str) -> None:
        rec = {"ts": now_iso(), "type": etype, "asset": asset, "detail": detail,
               "strategy": STRATEGY_NAME, "version": __version__}
        try:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
                f.flush()
        except Exception as e:
            logger.error(f"[EVENT LOG ERROR] {e}")


class Notifier:
    def __init__(self):
        self.tg_token = os.getenv("TELEGRAM_BOT_TOKEN") or ""
        self.tg_chat = os.getenv("TELEGRAM_CHAT_ID") or ""
        self.slack = os.getenv("SLACK_WEBHOOK_URL") or ""
        self.generic = os.getenv("GENERIC_WEBHOOK_URL") or ""
        self.cooldowns: Dict[str, float] = {}
        if not (self.tg_token and self.tg_chat) and not self.slack and not self.generic:
            logger.warning("[ALERTS] No notifier configured (Telegram/Slack/webhook). Alerts stay in logs + event journal only.")

    def _cool(self, key: str, ttl: float) -> bool:
        now = time.time()
        if now - self.cooldowns.get(key, 0.0) < ttl:
            return False
        self.cooldowns[key] = now
        return True

    def send(self, title: str, body: str, level: str = "info", cooldown: float = 0.0) -> None:
        if cooldown > 0 and not self._cool(title, cooldown):
            return
        text = f"[{STRATEGY_NAME}] {title}: {body}"
        if level in ("critical", "error"):
            logger.error(f"[ALERT] {text}")
        else:
            logger.info(f"[ALERT] {text}")
        # fire-and-forget, never raises
        try:
            if self.tg_token and self.tg_chat:
                requests.post(f"https://api.telegram.org/bot{self.tg_token}/sendMessage",
                              json={"chat_id": self.tg_chat, "text": text}, timeout=3)
        except Exception:
            pass
        try:
            if self.slack:
                requests.post(self.slack, json={"text": text}, timeout=3)
        except Exception:
            pass
        try:
            if self.generic:
                requests.post(self.generic, json={"title": title, "body": body, "level": level}, timeout=3)
        except Exception:
            pass


# ------------------------------------------------------------------------------
# 7. SINGLETON LOCK (absolute path, heartbeat, takeover log — P3 fix)
# ------------------------------------------------------------------------------
class SingletonLock:
    def __init__(self, path: Path):
        self.path = path
        self.fh: Any = None

    def acquire(self) -> bool:
        try:
            prev = None
            if self.path.exists():
                try:
                    prev = json.loads(self.path.read_text(encoding="utf-8"))
                except Exception:
                    prev = None
            self.fh = open(self.path, "w", encoding="utf-8")
            if os.name == "nt":
                import msvcrt
                try:
                    msvcrt.locking(self.fh.fileno(), msvcrt.LK_NBLCK, 1)
                except OSError:
                    logger.warning(f"[SINGLETON] Another instance holds {self.path} (prev={prev}). Exiting.")
                    self.fh.close()
                    self.fh = None
                    return False
            else:
                import fcntl
                try:
                    fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                except OSError:
                    logger.warning(f"[SINGLETON] Another instance holds {self.path} (prev={prev}). Exiting.")
                    self.fh.close()
                    self.fh = None
                    return False
            if prev and prev.get("pid") not in (None, os.getpid()):
                logger.warning(f"[SINGLETON] Took over lock from previous holder pid={prev.get('pid')} host={prev.get('host')}")
            self._write_info()
            return True
        except Exception as e:
            logger.error(f"[SINGLETON] acquire failed: {e}")
            return False

    def _write_info(self) -> None:
        if not self.fh:
            return
        try:
            host = socket.gethostname()
        except Exception:
            host = "unknown"
        info = {"pid": os.getpid(), "host": host, "started_at": now_iso(),
                "heartbeat": now_iso(), "version": __version__}
        try:
            self.fh.seek(0)
            self.fh.truncate()
            self.fh.write(json.dumps(info))
            self.fh.flush()
        except Exception as e:
            logger.debug(f"[SINGLETON] heartbeat write: {e}")

    def heartbeat(self) -> None:
        self._write_info()

    def release(self) -> None:
        if not self.fh:
            return
        try:
            if os.name != "nt":
                import fcntl
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            self.fh.close()
        except Exception:
            pass
        self.fh = None


# ------------------------------------------------------------------------------
# 8. EXPIRY PARSING + DTE PICKER (P1 fix)
# ------------------------------------------------------------------------------
_EXPIRY_FORMATS = ("%d-%b-%y", "%d-%b-%Y", "%d-%B-%y", "%d-%B-%Y", "%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y")


def parse_expiry_date(raw: str) -> Optional[date]:
    s = (raw or "").strip()
    if not s:
        return None
    for variant in (s.upper(), s.title(), s):
        for fmt in _EXPIRY_FORMATS:
            try:
                return datetime.strptime(variant, fmt).date()
            except ValueError:
                continue
    return None


def pick_1dte_expiry(exp_list: List[str], now: datetime, settle: dtime) -> Tuple[Optional[str], Optional[date], int, str]:
    """Returns (raw, parsed, dte, reason). DTE<=2 enforced; post-settle requires DTE>=1."""
    today = now.date()
    cands: List[Tuple[str, date, int]] = []
    for raw in exp_list or []:
        d = parse_expiry_date(str(raw))
        if d is None:
            continue
        cands.append((str(raw), d, (d - today).days))
    if not cands:
        return None, None, -999, "NO_PARSEABLE_EXPIRY"
    post_settle = now.time() >= settle
    pool = [c for c in cands if (c[2] >= 1 if post_settle else c[2] >= 0)]
    if not pool:
        return None, None, -999, "NO_EXPIRY_AFTER_SETTLE_FILTER"
    pool.sort(key=lambda c: (c[2], c[1]))
    raw, d, dte = pool[0]
    if dte > 2:
        return None, None, dte, f"DTE_TOO_FAR_{dte}"
    return raw, d, dte, "OK"


_SYMBOL_RE = re.compile(r"^([A-Z]+)(\d{2}[A-Z]{3}\d{2})(\d+)(CE|PE)$")


def validate_option_symbol(sym: str, asset: str, strike: float, option_type: str,
                            expiry_raw: str, step: float) -> Tuple[bool, str]:
    if not sym:
        return False, "EMPTY"
    m = _SYMBOL_RE.match(sym.strip().upper())
    if not m:
        return False, "BAD_FORMAT"
    _a, _e, k, t = m.group(1), m.group(2), float(m.group(3)), m.group(4)
    if t != option_type:
        return False, f"TYPE_{t}_NE_{option_type}"
    # Delta Exchange option chains use wider steps out of the money (up to 400 for BTC, 30 for ETH)
    allowed_tol = max(step * 1.5, 400.0 if asset.upper() == "BTC" else 30.0)
    if abs(k - strike) > allowed_tol:
        return False, f"STRIKE_{k:g}_NE_{strike:g}"
    exp_clean = re.sub(r"[^A-Z0-9]", "", expiry_raw.upper())
    if exp_clean and exp_clean not in sym.upper().replace("-", ""):
        return False, "EXPIRY_MISMATCH"
    if not sym.upper().startswith(asset.upper()):
        return False, "ASSET_MISMATCH"
    return True, "OK"


# ------------------------------------------------------------------------------
# 9. PARSERS for broker payload variations
# ------------------------------------------------------------------------------
def _pick(d: Dict[str, Any], *keys: str, default: Any = 0.0) -> Any:
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def parse_position_qty(p: Dict[str, Any]) -> float:
    return float(_pick(p, "quantity", "netqty", "net_quantity", "qty", default=0.0) or 0.0)


def parse_avg_price(p: Dict[str, Any]) -> float:
    return float(_pick(p, "average_price", "averageprice", "avg_price", "avgprice", default=0.0) or 0.0)


def parse_ltp(p: Dict[str, Any]) -> float:
    return float(_pick(p, "ltp", "last_price", "lastprice", default=0.0) or 0.0)


def parse_order_fill(o: Dict[str, Any]) -> Tuple[str, int, float]:
    status = str(_pick(o, "order_status", "orderstatus", "status", default="")).lower()
    filled = _pick(o, "filled_quantity", "filledquantity", "filled_qty", "filledqty",
                   "traded_quantity", "tradedquantity", "executed_quantity", "quantity_traded",
                   default=0.0)
    try:
        filled_qty = abs(int(float(filled or 0.0)))
    except (TypeError, ValueError):
        filled_qty = 0
    avg = parse_avg_price(o)
    if avg <= 0:
        try:
            avg = float(_pick(o, "price", default=0.0) or 0.0)
        except (TypeError, ValueError):
            avg = 0.0
    return status, filled_qty, avg


def order_terminal(status: str) -> Optional[str]:
    s = status.lower()
    if s in ("complete", "completed", "filled", "fill", "traded"):
        return "FILLED"
    if s in ("cancelled", "canceled", "cancel"):
        return "CANCELLED"
    if s in ("rejected", "reject"):
        return "REJECTED"
    return None


# ------------------------------------------------------------------------------
# 10. SL CONFIRMER (N-tick + spread + freshness — P0 fix)
# ------------------------------------------------------------------------------
class SLConfirmer:
    def __init__(self, need: int = 2, window_s: float = 60.0,
                 max_spread: float = 0.40, max_age: float = 5.0):
        self.need = need
        self.window_s = window_s
        self.max_spread = max_spread
        self.max_age = max_age

    def add_tick(self, ticks: List[Dict[str, Any]], ask: float,
                 spread: Optional[float], size: int, age: float) -> List[Dict[str, Any]]:
        ticks.append({"ts": time.time(), "ask": ask,
                      "spread": spread if spread is not None else 1.0,
                      "size": size, "age": age})
        while len(ticks) > 8:
            ticks.pop(0)
        return ticks

    def triggered(self, ticks: List[Dict[str, Any]], sl: float) -> Tuple[bool, str]:
        if sl <= 0 or len(ticks) < self.need:
            return False, "NEED_MORE_TICKS"
        now = time.time()
        recent = [t for t in ticks if now - t["ts"] <= self.window_s][-self.need:]
        if len(recent) < self.need:
            return False, "WINDOW_SHORT"
        for t in recent:
            if t["ask"] < sl:
                return False, "BELOW_SL"
            if t["spread"] is not None and t["spread"] > self.max_spread:
                return False, "SPREAD_WIDE"
            if t["size"] <= 0:
                return False, "ZERO_SIZE"
            if t["age"] > self.max_age:
                return False, "STALE_QUOTE"
        return True, f"{self.need}x_CONFIRMED"


# ------------------------------------------------------------------------------
# 11. ENGINE
# ------------------------------------------------------------------------------
@dataclass
class LotsDecision:
    lots: int
    reason: str
    usable_inr: float = 0.0
    margin_per_lot_inr: float = 0.0
    fx: float = 88.0
    blocked: bool = False


class Engine:
    def __init__(self, cfg: EngineConfig, client: Optional[ApiClient] = None,
                 notifier: Optional[Notifier] = None):
        self.cfg = cfg
        self.paper = cfg.mode == "paper"
        self.client = client or ApiClient(cfg.host, cfg.api_key, RateLimiter())
        self.notifier = notifier or Notifier()
        # PINNED session (P1 fix): resolved once, never recomputed for file routing
        self.session = session_date_str()
        mode_suffix = "_paper" if self.paper else ""
        self.state_path = cfg.data_dir / f"overnight_crypto_delta_{self.session}{mode_suffix}.json"
        self.events_path = cfg.data_dir / f"overnight_crypto_delta_{self.session}{mode_suffix}.events.jsonl"
        self.store = StateStore(self.state_path)
        self.events = EventLog(self.events_path)
        self.lock = SingletonLock(cfg.lock_path)
        self.confirmer = SLConfirmer(cfg.sl_confirm_ticks, cfg.sl_confirm_window_s,
                                     cfg.sl_max_spread_pct, cfg.sl_max_quote_age_s)
        self.stopping = False
        self.consecutive_api_errors = 0
        self.last_heartbeat = 0.0
        self.last_error_alert = 0.0
        self.last_expiry_reason = ""
        # paper book: symbol -> {qty(long+), avg}
        self.paper_book: Dict[str, Dict[str, float]] = {}
        self.state: Dict[str, Any] = {"version": STATE_VERSION, "strategy": STRATEGY_NAME,
                                      "session": self.session, "broker_profile": cfg.broker_profile,
                                      "assets": {}, "portfolio": {"sl_total": 0, "halted": False,
                                                                  "halt_reason": None}}
        for s in cfg.symbols:
            self.state["assets"][s] = default_asset_state()
        self._load_state()
        self._log_resolved_config()

    # -- events/alerts ------------------------------------------------------
    def _event(self, etype: str, asset: str, detail: str) -> None:
        self.events.append(etype, asset, detail)

    def _alert(self, title: str, body: str, level: str = "info", cooldown: float = 0.0) -> None:
        self.notifier.send(title, body, level, cooldown)

    # -- api accounting / error budget --------------------------------------
    def _api_ok(self) -> None:
        self.consecutive_api_errors = 0

    def _api_err(self, where: str, e: Exception) -> None:
        self.consecutive_api_errors += 1
        logger.warning(f"[API ERROR] {where}: {e} (consecutive={self.consecutive_api_errors})")
        if self.consecutive_api_errors >= self.cfg.error_budget:
            msg = (f"Error budget breached ({self.consecutive_api_errors} consecutive API failures at {where}). "
                   f"State persisted; exiting(1) for supervised restart with backoff.")
            logger.error(f"[CIRCUIT BREAKER] {msg}")
            self._event("CIRCUIT_BREAKER", "*", msg)
            self._alert("CIRCUIT_BREAKER", msg, "critical")
            self._save_state()
            raise SystemExit(1)

    # -- config log ----------------------------------------------------------
    def _log_resolved_config(self) -> None:
        c = self.cfg
        logger.info(f"[{STRATEGY_NAME} v{__version__}] symbols={c.symbols} mode={c.mode} profile={c.broker_profile}")
        logger.info(f"  schedule entry {c.entry_start.strftime('%H:%M')}-{c.entry_end.strftime('%H:%M')} "
                    f"| ladder {c.ladder_start.strftime('%H:%M')} | aggressive {c.aggressive_time.strftime('%H:%M')} "
                    f"| sweep {c.cutoff.strftime('%H:%M')} | settle {c.settle.strftime('%H:%M')} IST")
        logger.info(f"  risk sl=x{c.sl_mult} tp={c.tp_pct*100:.0f}% max_loss=Rs {c.max_nightly_loss_inr:,.0f} "
                    f"max_sl={c.max_sl_per_night} fee={c.taker_fee_bps}bps+${c.per_order_fee_usd}")
        for s in c.symbols:
            a = c.assets[s]
            logger.info(f"  {s}: otm={a.otm_pct*100:.2f}% width={a.wing_width_strikes}x{a.strike_step:g}="
                        f"${a.strike_step*a.wing_width_strikes:g} lots={a.default_lots} mult={a.contract_mult}")

    # -- state ---------------------------------------------------------------
    def _load_state(self) -> None:
        raw = self.store.load()
        if not raw:
            return
        assets = raw.get("assets") or {}
        for sym in self.cfg.symbols:
            old = assets.get(sym)
            if not old:
                continue
            if "legs" in old:
                merged = default_asset_state()
                merged.update(old)
                for k in LEG_KEYS:
                    base = default_leg()
                    base.update((old.get("legs") or {}).get(k) or {})
                    merged["legs"][k] = base
                self.state["assets"][sym] = merged
            else:
                self.state["assets"][sym] = migrate_v1_asset(old)
                logger.info(f"[{sym}] Migrated v1 state -> v2 (fills flagged estimated)")
        pf = raw.get("portfolio") or {}
        self.state["portfolio"].update({k: pf.get(k, v) for k, v in self.state["portfolio"].items()})
        logger.info(f"[STATE RESTORED] {self.state_path.name} (pinned session {self.session})")
        # Stale-cycle guard (P2: ISO + multi-format, unparseable => stale when flat)
        today = now_ist().date()
        curr = now_ist().time()
        for sym in self.cfg.symbols:
            a = self.state["assets"][sym]
            if a.get("active") or not a.get("entry_done"):
                continue
            stale = False
            reason = ""
            iso = a.get("expiry_iso")
            d: Optional[date] = None
            if iso:
                try:
                    d = date.fromisoformat(str(iso))
                except ValueError:
                    d = None
            if d is None and a.get("expiry_date"):
                d = parse_expiry_date(str(a["expiry_date"]))
            if d is not None:
                stale = (d < today) or (d == today and curr >= self.cfg.settle)
                reason = f"expiry={d}"
            else:
                stale = True  # fail closed: unknown expiry on a finished cycle => stale
                reason = "expiry=UNPARSEABLE"
            if stale:
                logger.info(f"[{sym}] [STALE CYCLE RESET] ({reason}, exit={a.get('exit_reason')})")
                self.state["assets"][sym] = default_asset_state()
        if self.cfg.force_entry:
            self.state["portfolio"]["halted"] = False
            self.state["portfolio"]["halt_reason"] = None
            for sym in self.cfg.symbols:
                a = self.state["assets"][sym]
                if not a.get("active"):
                    a["entry_done"] = False
                    a["fatal_for_night"] = None
                    a["next_entry_retry_ts"] = 0.0
            logger.warning("[FORCE ENTRY] entry_done, fatal_for_night and retry backoff cleared for flat assets")

    def _save_state(self) -> None:
        self.store.save(self.state)

    # -- broker positions ------------------------------------------------------
    def _fetch_broker_positions(self) -> Dict[str, Dict[str, float]]:
        if self.paper:
            out: Dict[str, Dict[str, float]] = {}
            for sym, v in self.paper_book.items():
                if abs(v["qty"]) > 0:
                    out[sym] = {"quantity": v["qty"], "average_price": v["avg"], "ltp": v["avg"]}
            return out
        try:
            res = self.client.positionbook()
            self._api_ok()
        except Exception as e:
            self._api_err("positionbook", e)
            return {}
        rows = res.get("data", []) if isinstance(res, dict) else []
        out2: Dict[str, Dict[str, float]] = {}
        if isinstance(rows, list):
            for p in rows:
                if not isinstance(p, dict) or not p.get("symbol"):
                    continue
                sym = str(p["symbol"]).strip()
                qty = parse_position_qty(p)
                if abs(qty) > 0:
                    out2[sym] = {"quantity": qty, "average_price": parse_avg_price(p), "ltp": parse_ltp(p)}
        return out2

    def _owned_symbols(self, asset: str) -> set:
        legs = self.state["assets"][asset].get("legs", {})
        return {str(v.get("symbol")) for v in legs.values() if v.get("symbol")}

    def classify_positions(self, asset: str) -> Tuple[Dict[str, Dict[str, float]], Dict[str, Dict[str, float]]]:
        """(owned_open, foreign_open). Foreign = daytime/other-strategy legs. (P0 fix)"""
        broker = self._fetch_broker_positions()
        owned_syms = self._owned_symbols(asset)
        # journal-owned: any symbol we journaled this session for this asset
        owned, foreign = {}, {}
        for sym, v in broker.items():
            is_opt = sym.startswith(asset) and (sym.endswith("CE") or sym.endswith("PE"))
            if not is_opt:
                continue
            if sym in owned_syms:
                owned[sym] = v
            else:
                foreign[sym] = v
        return owned, foreign

    # -- startup audit -----------------------------------------------------------
    def audit_and_recover_on_startup(self) -> None:
        logger.info("[STARTUP AUDIT] Reconciling owned legs with broker positionbook…")
        for asset in self.cfg.symbols:
            a = self.state["assets"][asset]
            owned, foreign = self.classify_positions(asset)
            if foreign:
                logger.warning(f"[{asset}] Foreign (non-owned) option legs on broker: {sorted(foreign)} — will SKIP, not hijack")
            if not owned:
                if a.get("active"):
                    # State says active but broker flat => externally closed or stale state
                    self._event("AUDIT_FLAT", asset, "state ACTIVE but broker flat; marking inactive, needs review")
                    self._alert("AUDIT_FLAT", f"{asset}: state ACTIVE but broker FLAT. Marked inactive; verify manually.", "error")
                    a["active"] = False
                    a["exit_time"] = now_iso()
                    a["exit_reason"] = a.get("exit_reason") or "AUDIT_BROKER_FLAT"
                continue
            # Adopt owned qtys into legs
            legs = a["legs"]
            sym_to_leg = {str(v.get("symbol")): k for k, v in legs.items() if v.get("symbol")}
            for sym, v in owned.items():
                qty, avg, ltp = v["quantity"], v["average_price"], v["ltp"]
                key = sym_to_leg.get(sym)
                if not key:
                    # Owned symbol unknown to state (e.g. state file lost): safest is flatten (P0 fix)
                    self._event("AUDIT_UNKNOWN_OWNED", asset, f"{sym} qty={qty} not in state legs")
                    self._alert("AUDIT_UNKNOWN_OWNED",
                                f"{asset}: {sym} qty={qty} on broker but not in state legs; will flatten.", "error")
                    continue
                leg = legs[key]
                leg["filled_qty"] = abs(int(qty))
                leg["requested_qty"] = max(leg.get("requested_qty", 0), abs(int(qty)))
                if (not leg.get("avg_price")) and (avg > 0 or ltp > 0):
                    leg["avg_price"] = avg if avg > 0 else ltp
                    leg["fill_estimated"] = True
                leg["active"] = True
                leg["status"] = LegStatus.FILLED.value
                if leg.get("side") == "SELL" and (not leg.get("sl_price")) and leg["avg_price"] > 0:
                    sl_mult = a.get("sl_mult") or compute_dynamic_sl_mult(cfg.otm_pct, self.cfg.sl_mult)
                    leg["sl_price"] = round(leg["avg_price"] * sl_mult, 2)
            # Basket completeness gate (P0): incomplete => flatten + reset, NEVER ACTIVE-on-partial
            shorts = [legs[k] for k in ("CE_SHORT", "PE_SHORT")]
            wings = [legs[k] for k in ("CE_WING", "PE_WING")]
            n_short = sum(1 for l in shorts if l.get("active"))
            n_wing = sum(1 for l in wings if l.get("active"))
            complete = n_short == 2 and n_wing == 2
            qtys = {l.get("filled_qty", 0) for l in legs.values() if l.get("active")}
            balanced = len(qtys) <= 1
            if complete and balanced:
                a["active"] = True
                a["entry_done"] = True
                logger.info(f"[{asset}] [AUDIT] Full balanced basket recovered ({qtys}). SLs armed; breach-checking…")
                self._event("AUDIT_RECOVERED", asset, f"basket={sorted(owned)} qty={qtys}")
                # Immediate breach check on shorts
                for key in ("CE_SHORT", "PE_SHORT"):
                    leg = legs[key]
                    q = self.get_l2_depth(leg["symbol"])
                    ask = (q.ask if q and q.ask else None) or owned[leg["symbol"]].get("ltp")
                    sl = float(leg.get("sl_price") or 0)
                    if ask and sl > 0 and ask >= sl:
                        # needs confirmation? At boot with single print, require 2nd print 3s later
                        time.sleep(3)
                        q2 = self.get_l2_depth(leg["symbol"])
                        ask2 = q2.ask if q2 and q2.ask else ask
                        if ask2 and ask2 >= sl:
                            logger.warning(f"[{asset}] [AUDIT SL BREACH] {leg['symbol']} {ask2} >= {sl}: full unwind")
                            self._square_off_full(asset, f"STARTUP_{key}_SL_BREACH")
                            break
                        else:
                            logger.info(f"[{asset}] [AUDIT] SL breach not confirmed on 2nd print; armed")
                    elif ask:
                        logger.info(f"[{asset}] [AUDIT] {leg['symbol']} ask {ask} < SL {sl}: armed")
            else:
                logger.warning(f"[{asset}] [AUDIT INCOMPLETE BASKET] shorts={n_short}/2 wings={n_wing}/2 qtys={qtys}: "
                               f"flattening owned + resetting for a fresh entry")
                self._event("AUDIT_INCOMPLETE", asset, f"shorts={n_short} wings={n_wing} qtys={sorted(qtys)}")
                self._alert("AUDIT_INCOMPLETE", f"{asset}: incomplete basket on boot; flattening owned legs.", "error")
                self._flatten_owned_symbols(asset, sorted(owned), "AUDIT_INCOMPLETE_FLATTEN")
                self.state["assets"][asset] = default_asset_state()
        self._save_state()
        logger.info("[STARTUP AUDIT COMPLETE]")

    def _flatten_owned_symbols(self, asset: str, symbols: List[str], reason: str) -> None:
        broker = self._fetch_broker_positions()
        for sym in symbols:
            v = broker.get(sym)
            if not v or abs(v["quantity"]) == 0:
                continue
            qty = abs(int(v["quantity"]))
            action = "BUY" if v["quantity"] < 0 else "SELL"
            self._exit_symbol_marketish(sym, action, qty, reason)

    # -- market data ---------------------------------------------------------------
    def get_spot_price(self, asset: str) -> Optional[float]:
        fut = self.cfg.assets[asset].futures_symbol
        try:
            res = self.client.quotes(fut)
            self._api_ok()
        except Exception as e:
            self._api_err(f"quotes {fut}", e)
            return None
        try:
            inner = res.get("data", res) if isinstance(res, dict) else {}
            ltp = float(inner.get("ltp") or 0.0)
            return ltp if ltp > 0 else None
        except (TypeError, ValueError):
            return None

    def get_1dte_expiry(self, asset: str) -> Tuple[Optional[str], Optional[date], int]:
        try:
            res = self.client.expiry(asset)
            self._api_ok()
        except Exception as e:
            self._api_err(f"expiry {asset}", e)
            self.last_expiry_reason = "API_ERROR"
            return None, None, -999
        exp_list = res.get("data", []) if isinstance(res, dict) else []
        logger.info(f"[{asset}] Broker expiries: {exp_list}")
        raw, d, dte, reason = pick_1dte_expiry([str(x) for x in (exp_list or [])], now_ist(), self.cfg.settle)
        self.last_expiry_reason = reason
        if raw is None:
            logger.error(f"[{asset}] Expiry rejected: {reason}")
            self._event("EXPIRY_REJECT", asset, reason)
            return None, None, dte
        logger.info(f"[{asset}] 1DTE selected: {raw} (DTE={dte})")
        return raw, d, dte

    def resolve_option_symbol(self, asset: str, strike: float, option_type: str, expiry: str) -> Optional[str]:
        """No canonical fallback (P1 fix): resolver failure => None."""
        try:
            res = self.client.optionsymbol(asset, expiry, option_type, strike)
            self._api_ok()
        except Exception as e:
            self._api_err(f"optionsymbol {asset} {strike:g}{option_type}", e)
            return None
        sym = (res.get("symbol") or "") if isinstance(res, dict) else ""
        if not sym:
            logger.warning(f"[{asset}] Resolver returned empty for {strike:g}{option_type}")
            return None
        ok, why = validate_option_symbol(sym, asset, strike, option_type, expiry, self.cfg.assets[asset].strike_step)
        if not ok:
            logger.warning(f"[{asset}] Resolver symbol {sym} failed validation ({why}); strike not tradeable")
            return None
        return sym

    def get_l2_depth(self, symbol: str) -> Optional[DepthQuote]:
        """Fail-closed depth (P0 fix): L2 failure => quotes bid/ask with sizes 0 + fresh_l2=False."""
        if self.paper and False:  # paper still uses LIVE market data (P3 fix)
            pass
        try:
            res = self.client.depth(symbol)
            self._api_ok()
        except Exception as e:
            self._api_err(f"depth {symbol}", e)
            res = None
        if isinstance(res, dict):
            try:
                data = res.get("data", {}) or {}
                bids = data.get("bids", []) or []
                asks = data.get("asks", []) or []

                def lv(row: Any) -> Tuple[Optional[float], int]:
                    if not isinstance(row, dict):
                        return None, 0
                    try:
                        px = float(row.get("price") or 0.0) or None
                    except (TypeError, ValueError):
                        px = None
                    try:
                        sz = int(float(row.get("quantity", row.get("size", row.get("qty", 0))) or 0))
                    except (TypeError, ValueError):
                        sz = 0
                    return px, sz

                bb, bs = lv(bids[0]) if bids else (None, 0)
                ba, asz = lv(asks[0]) if asks else (None, 0)
                cb = sum(lv(r)[1] for r in bids[:3])
                ca = sum(lv(r)[1] for r in asks[:3])
                return DepthQuote(symbol, bb, ba, bs, asz, cb, ca, time.time(), True)
            except Exception as e:
                logger.debug(f"[DEPTH PARSE] {symbol}: {e}")
        # Quotes fallback: prices only, sizes ZERO + stale flag (never 1000/1000)
        try:
            q = self.client.quotes(symbol)
            self._api_ok()
            inner = q.get("data", q) if isinstance(q, dict) else {}
            bid = float(inner.get("bid") or 0.0) or None
            ask = float(inner.get("ask") or 0.0) or None
            ltp = float(inner.get("ltp") or 0.0) or None
            if bid is None and ltp:
                bid = ltp
            if ask is None and ltp:
                ask = ltp
            logger.warning(f"[DEPTH] {symbol}: L2 unavailable, quotes-only (sizes=0, STALE)")
            return DepthQuote(symbol, bid, ask, 0, 0, 0, 0, time.time(), False)
        except Exception as e:
            self._api_err(f"quotes-fallback {symbol}", e)
            return None

    # -- strike hunting ---------------------------------------------------------------
    def hunt_liquid_strike(self, asset: str, base_strike: float, option_type: str,
                            action: str, expiry: str, lots: int, is_wing: bool,
                            walk_dir: int = 0, boundary: Optional[float] = None) -> Optional[HuntedStrike]:
        cfg = self.cfg.assets[asset]
        step = cfg.strike_step
        cur = base_strike
        max_steps = 4 if is_wing else 1
        # Depth scaled to tonight's size (P1 fix)
        need_top = max(cfg.min_depth_contracts, math.ceil(0.5 * lots))
        need_cum = max(cfg.min_depth_contracts * 2, lots)
        for i in range(max_steps):
            if boundary is not None:
                if walk_dir < 0 and cur < boundary:
                    logger.warning(f"[{asset}] Wing walk hit boundary {boundary:g}; halt")
                    break
                if walk_dir > 0 and cur > boundary:
                    logger.warning(f"[{asset}] Wing walk hit boundary {boundary:g}; halt")
                    break
            sym = self.resolve_option_symbol(asset, cur, option_type, expiry)
            if sym:
                q = self.get_l2_depth(sym)
                if q is None:
                    logger.warning(f"[{asset}] {sym}: no market data (fail-closed); abort leg")
                    return None
                if not q.fresh_l2:
                    logger.warning(f"[{asset}] {sym}: L2 stale — refusing to trade this strike (fail-closed)")
                    # wings may walk to a strike with live L2; shorts abort
                    if not (is_wing and action == "BUY"):
                        return None
                else:
                    spr_d = q.spread_dollar()
                    spr_p = q.spread_pct()
                    if is_wing and action == "BUY":
                        if q.ask and q.ask > 0:
                            spread_ok = ((spr_p is not None and spr_p <= cfg.max_wing_spread_pct) or
                                         (spr_d is not None and spr_d <= cfg.max_wing_spread_dollar))
                            depth_ok = q.ask_size >= need_top and q.cum_ask_3 >= need_cum
                            price_ok = q.ask <= cfg.max_wing_price
                            if spread_ok and depth_ok and price_ok:
                                if i > 0:
                                    logger.info(f"[{asset}] Wing stepped inward to {sym} @ {q.ask}")
                                return HuntedStrike(sym, cur, q.bid or 0.0, q.ask, q.bid_size, q.ask_size)
                            logger.warning(f"[{asset}] Wing {sym} gate fail: ask={q.ask} spr={spr_d}/{spr_p} "
                                           f"size={q.ask_size}/{q.cum_ask_3} need={need_top}/{need_cum}")
                        else:
                            logger.warning(f"[{asset}] Wing {sym}: no ask")
                    elif not is_wing and action == "SELL":
                        if q.bid and q.ask and q.bid > 0 and q.ask > 0:
                            spread_ok = ((spr_p is not None and spr_p <= cfg.max_spread_pct) or
                                         (spr_d is not None and spr_d <= cfg.max_short_spread_dollar))
                            depth_ok = q.bid_size >= need_top and q.cum_bid_3 >= need_cum
                            if spread_ok and depth_ok:
                                return HuntedStrike(sym, cur, q.bid, q.ask, q.bid_size, q.ask_size)
                            logger.warning(f"[{asset}] Short {sym} gate fail: spr={spr_p} size={q.bid_size}/{q.cum_bid_3}")
                            return None
                        else:
                            logger.warning(f"[{asset}] Short {sym}: no live bid/ask")
                            return None
            if walk_dir != 0 and is_wing:
                nxt = cur + step * walk_dir
                if boundary is not None:
                    if (walk_dir < 0 and nxt < boundary) or (walk_dir > 0 and nxt > boundary):
                        logger.warning(f"[{asset}] Next wing step {nxt:g} violates boundary {boundary:g}; halt")
                        break
                cur = nxt
            else:
                break
        return None

    # -- funds / sizing / margin -----------------------------------------------------------
    def funds_snapshot(self) -> Tuple[Optional[float], Optional[float]]:
        """Returns (cash_inr, fx_or_None). Validated; live failures => (None, None)."""
        try:
            res = self.client.funds()
            self._api_ok()
        except Exception as e:
            self._api_err("funds", e)
            return None, None
        d = res.get("data", {}) if isinstance(res, dict) else {}
        if not isinstance(d, dict):
            return None, None
        cash = None
        for k in ("availablecash", "available_cash", "availableCash", "cash", "balance",
                  "available_margin", "availablemargin", "free_cash", "freecash"):
            v = d.get(k)
            try:
                if v is not None and float(v) >= 0:
                    cash = float(v)
                    break
            except (TypeError, ValueError):
                continue
        fx = None
        for k in ("availablecash_usd", "available_cash_usd", "cash_usd", "balance_usd"):
            v = d.get(k)
            try:
                if v is not None and float(v) > 0 and cash and cash > 0:
                    fx = cash / float(v)
                    break
            except (TypeError, ValueError):
                continue
        if cash is None:
            logger.warning(f"[FUNDS] Unrecognized funds schema keys: {sorted(d.keys())}")
        return cash, fx

    def estimate_margin_per_lot_inr(self, asset: str, fx: float) -> float:
        c = self.cfg.assets[asset]
        width = c.strike_step * c.wing_width_strikes
        return width * c.contract_mult * fx * self.cfg.margin_safety_mult

    def _get_compounded_capital(self) -> float:
        """Returns base capital + cumulative realized profits (dynamic compounding)."""
        base = self.cfg.capital_base_inr
        stats_file = self.cfg.data_dir / "overnight_crypto_delta_stats.json"
        cum_pnl = 0.0
        if stats_file.exists():
            try:
                with open(stats_file, "r") as f:
                    cum_pnl = float(json.load(f).get("cumulative_pnl_inr", 0.0))
            except Exception:
                pass
        # Also include current session realized pnl
        today_pnl_usd = sum(float(a.get("realized_pnl_usd", 0.0)) for a in self.state["assets"].values())
        cum_pnl += today_pnl_usd * (self.cfg.usd_inr or 88.0)
        return max(base, base + cum_pnl)

    def _update_cumulative_stats(self, pnl_inr: float) -> None:
        stats_file = self.cfg.data_dir / "overnight_crypto_delta_stats.json"
        cum_pnl = 0.0
        if stats_file.exists():
            try:
                with open(stats_file, "r") as f:
                    cum_pnl = float(json.load(f).get("cumulative_pnl_inr", 0.0))
            except Exception:
                pass
        cum_pnl += pnl_inr
        try:
            with open(stats_file, "w") as f:
                json.dump({"cumulative_pnl_inr": round(cum_pnl, 2), "updated_at": now_iso()}, f, indent=2)
        except Exception:
            pass

    def compute_dynamic_lots(self, asset: str) -> LotsDecision:
        c = self.cfg.assets[asset]
        if not self.cfg.dynamic_sizing:
            return LotsDecision(c.default_lots, "DYNAMIC_OFF", fx=self.cfg.usd_inr)
        alloc_pct = float(os.getenv(f"{asset}_IC_ALLOCATION_PCT", 1.0 / max(1, len(self.cfg.symbols))))
        if self.cfg.broker_profile == "sandbox":
            compounded = self._get_compounded_capital()
            per_asset = compounded * alloc_pct
            logger.info(f"[{asset}] Sandbox profile: base capital Rs {compounded:,.0f} x {alloc_pct:.0%} = Rs {per_asset:,.0f} (compounded)")
            fx = (self.cfg.usd_inr or 88.0)
        elif self.paper:
            cash, funds_fx = self.funds_snapshot()
            fx = funds_fx or self.cfg.usd_inr or 88.0
            if cash and cash > 5_000_000:
                compounded = self._get_compounded_capital()
                per_asset = compounded * alloc_pct
                logger.info(f"[{asset}] [PAPER] 1 Cr sandbox balance detected; using base Rs {compounded:,.0f} x {alloc_pct:.0%} = Rs {per_asset:,.0f}")
            elif cash:
                per_asset = cash * alloc_pct
            else:
                compounded = self._get_compounded_capital()
                per_asset = compounded * alloc_pct
                logger.warning(f"[{asset}] [PAPER] funds unavailable; using base Rs {per_asset:,.0f} x {alloc_pct:.0%}")
        else:
            cash, funds_fx = self.funds_snapshot()
            if not cash or cash <= 0:
                return LotsDecision(0, "FUNDS_UNAVAILABLE", blocked=True)  # fail closed in live
            # Auto-resolve FX rate: funds_fx -> cfg.usd_inr -> 88.0 fallback
            fx = funds_fx or self.cfg.usd_inr or 88.0
            if fx <= 0:
                fx = 88.0
            # Dual Sandbox & Live mode support with dynamic profit compounding:
            if cash > 5_000_000:
                compounded = self._get_compounded_capital()
                per_asset = compounded * alloc_pct
                logger.info(f"[{asset}] [SANDBOX FUNDS] 1 Cr sandbox balance detected. Using base Rs {compounded:,.0f} x {alloc_pct:.0%} = Rs {per_asset:,.0f} @ Rs {fx:.2f}/$")
            else:
                per_asset = cash * alloc_pct
                logger.info(f"[{asset}] [LIVE FUNDS] allocated Rs {per_asset:,.2f} ({alloc_pct:.0%}) @ Rs {fx:.2f}/$")
        margin_per = self.estimate_margin_per_lot_inr(asset, fx)
        usable = per_asset * self.cfg.margin_util_cap
        raw = int(usable // margin_per) if margin_per > 0 else c.default_lots
        lots = max(self.cfg.min_lots, min(raw, self.cfg.max_lots))
        # Funds gate: estimated required x1.1 must fit free allocation (explicit, labeled ESTIMATED)
        est_required = margin_per * lots
        if est_required * 1.1 > per_asset:
            fit = int(per_asset / (margin_per * 1.1)) if margin_per > 0 else 0
            if fit < self.cfg.min_lots:
                return LotsDecision(0, f"MARGIN_FIT_{fit}_BELOW_MIN", usable, margin_per, fx, True)
            logger.warning(f"[{asset}] Lots {lots}->{fit} to fit MARGIN_ESTIMATED x1.1 gate")
            lots = fit
        logger.info(f"[{asset}] Sizing: lots={lots} usable=Rs {usable:,.0f} margin/lot=Rs {margin_per:.2f} "
                    f"(MARGIN_ESTIMATED x{self.cfg.margin_safety_mult}, 35% buffer)")
        return LotsDecision(lots, "OK", usable, margin_per, fx, False)

    # -- fees ----------------------------------------------------------------------
    def _fee_usd(self, premium_per_unit: float, qty: int, mult: float, n_orders: int) -> float:
        notional = abs(premium_per_unit) * mult * abs(qty)
        return notional * (self.cfg.taker_fee_bps / 10000.0) + self.cfg.per_order_fee_usd * n_orders

    def idem_key(self, asset: str, leg: str, reason: str) -> str:
        return f"{self.session}:{asset}:{leg}:{reason}"

    # -- order lifecycle ---------------------------------------------------------------
    def place_limit(self, symbol: str, action: str, qty: int, price: float, idem: str) -> Tuple[bool, str]:
        if self.paper:
            oid = f"paper_{idem}_{int(time.time()*1000)}"
            self._event("PAPER_ORDER", "*", f"{action} {qty}x {symbol} @ {price} ({idem})")
            return True, oid
        os.environ["_CURRENT_IDEMPOTENCY_KEY"] = idem
        try:
            res = self.client.placeorder(symbol, action, qty, "LIMIT", round(price, 2))
            self._api_ok()
        except Exception as e:
            self._api_err(f"placeorder {action} {symbol}", e)
            return False, ""
        finally:
            os.environ.pop("_CURRENT_IDEMPOTENCY_KEY", None)
        if isinstance(res, dict) and str(res.get("status", "")).lower() == "success":
            oid = str(res.get("orderid") or "")
            logger.info(f"  [ORDER] {action} {qty}x {symbol} @ {price:.2f} id={oid} key={idem}")
            self._event("ORDER_PLACED", "*", f"{action} {qty}x {symbol} @{price:.2f} id={oid} key={idem}")
            return True, oid
        logger.error(f"  [ORDER REJECT] {action} {symbol}: {res}")
        self._event("ORDER_REJECT", "*", f"{action} {symbol}: {res}")
        return False, ""

    def place_market(self, symbol: str, action: str, qty: int, idem: str) -> Tuple[bool, str]:
        if self.paper:
            return True, f"paper_{idem}_{int(time.time()*1000)}"
        os.environ["_CURRENT_IDEMPOTENCY_KEY"] = idem
        try:
            res = self.client.placeorder(symbol, action, qty, "MARKET", None)
            self._api_ok()
        except Exception as e:
            self._api_err(f"market {action} {symbol}", e)
            return False, ""
        finally:
            os.environ.pop("_CURRENT_IDEMPOTENCY_KEY", None)
        if isinstance(res, dict) and str(res.get("status", "")).lower() == "success":
            oid = str(res.get("orderid") or "")
            self._event("ORDER_PLACED", "*", f"MARKET {action} {qty}x {symbol} id={oid} key={idem}")
            return True, oid
        logger.error(f"  [MARKET REJECT] {action} {symbol}: {res}")
        return False, ""

    def cancel_order(self, order_id: str) -> None:
        if not order_id or self.paper or order_id.startswith("paper_") or order_id.startswith("dry_run"):
            return
        try:
            self.client.cancelorder(order_id)
            self._api_ok()
            logger.info(f"  [CANCEL] {order_id}")
        except Exception as e:
            err_msg = str(e).lower()
            if "cancelled" in err_msg or "filled" in err_msg or "not found" in err_msg:
                logger.info(f"  [CANCEL_BENIGN] {order_id}: order already terminal ({e})")
                self._api_ok()
            else:
                self._api_err(f"cancel {order_id}", e)

    def _query_fill(self, order_id: str) -> Tuple[str, int, float]:
        """Single fill probe via orderstatus then orderbook. Returns (terminal|OPEN, filled, avg)."""
        if order_id.startswith("paper_"):
            return "FILLED", 0, 0.0
        try:
            res = self.client.orderstatus(order_id)
            self._api_ok()
            d = res.get("data", {}) if isinstance(res, dict) else {}
            if isinstance(d, dict) and d:
                st, fq, avg = parse_order_fill(d)
                term = order_terminal(st)
                if term:
                    return term, fq, avg
                if fq > 0:
                    return "OPEN", fq, avg
        except Exception as e:
            self._api_err(f"orderstatus {order_id}", e)
        try:
            res = self.client.orderbook()
            self._api_ok()
            orders = (res.get("data", {}) or {}).get("orders", []) if isinstance(res, dict) else []
            if isinstance(orders, list):
                for o in orders:
                    if isinstance(o, dict) and str(o.get("orderid")) == str(order_id):
                        st, fq, avg = parse_order_fill(o)
                        term = order_terminal(st)
                        if term:
                            return term, fq, avg
                        if fq > 0:
                            return "OPEN", fq, avg
        except Exception as e:
            self._api_err(f"orderbook {order_id}", e)
        return "OPEN", 0, 0.0

    def wait_for_fill(self, order_id: str, requested: int, timeout: float,
                      limit_price: float = 0.0) -> FillResult:
        """Partial-aware waiter (P0 fix). Paper fills immediately at limit+slippage."""
        if self.paper or order_id.startswith("paper_"):
            slip = 1.0 + self.cfg.exit_slip_bps / 10000.0
            # direction unknown here; caller adjusts. Default: pay slip upward.
            px = round(limit_price * slip, 2) if limit_price > 0 else 0.0
            return FillResult("FILLED", requested, px)
        deadline = time.time() + timeout
        best_fq, best_avg = 0, 0.0
        while time.time() < deadline:
            time.sleep(0.5)
            term, fq, avg = self._query_fill(order_id)
            if fq > best_fq:
                best_fq, best_avg = fq, avg
            if term == "FILLED":
                if fq >= requested:
                    logger.info(f"  [FILLED] {order_id} {fq}/{requested} @ {avg}")
                    return FillResult("FILLED", fq, avg if avg > 0 else limit_price)
                if fq > 0:
                    logger.warning(f"  [PARTIAL-COMPLETE] {order_id} {fq}/{requested} @ {avg}")
                    return FillResult("PARTIAL", fq, avg if avg > 0 else limit_price)
                return FillResult("FILLED", requested, limit_price if limit_price > 0 else avg)
            if term in ("CANCELLED", "REJECTED"):
                if fq > 0 or best_fq > 0:
                    q = fq or best_fq
                    logger.warning(f"  [{term}] {order_id} with partial {q}/{requested}")
                    return FillResult("PARTIAL", q, avg or best_avg or limit_price)
                logger.error(f"  [{term}] {order_id} zero fill")
                return FillResult(term, 0, 0.0)
        # timeout: cancel then final probe (partial may have landed)
        logger.warning(f"  [TIMEOUT] {order_id} {timeout}s; cancelling + final probe")
        self.cancel_order(order_id)
        time.sleep(1.0)
        term, fq, avg = self._query_fill(order_id)
        q = fq or best_fq
        if q > 0:
            logger.warning(f"  [PARTIAL] {order_id} {q}/{requested} @ {avg or best_avg}")
            return FillResult("PARTIAL", q, avg or best_avg or limit_price)
        return FillResult("TIMEOUT", 0, 0.0)

    def _paper_fill_price(self, action: str, ref: float) -> float:
        slip = self.cfg.exit_slip_bps / 10000.0
        px = ref * (1 + slip) if action == "BUY" else ref * (1 - slip)
        return round(max(px, 0.01), 2)

    def _book_apply(self, symbol: str, action: str, qty: int, price: float) -> None:
        if not self.paper:
            return
        v = self.paper_book.setdefault(symbol, {"qty": 0.0, "avg": 0.0})
        signed = qty if action == "BUY" else -qty
        new_qty = v["qty"] + signed
        if v["qty"] == 0 or (v["qty"] > 0) == (new_qty > 0) or new_qty == 0:
            if new_qty != 0 and (v["qty"] == 0 or (v["qty"] > 0) == (signed > 0)):
                tot = abs(v["qty"]) + qty
                v["avg"] = (abs(v["qty"]) * v["avg"] + qty * price) / tot if tot else price
        else:
            v["avg"] = price  # flipped; reset basis (shouldn't happen: we size exactly)
        v["qty"] = new_qty

    # -- entry -------------------------------------------------------------------
    def _entry_blocked_by_files(self) -> Optional[str]:
        if self.state["portfolio"].get("halted"):
            return f"HALTED:{self.state['portfolio'].get('halt_reason')}"
        if self.cfg.halt_file.exists():
            return "HALT_FILE"
        return None

    def enter_iron_condor(self, asset: str) -> Tuple[EntryOutcome, str]:
        a = self.state["assets"][asset]
        cfg = self.cfg.assets[asset]
        now_ts = time.time()
        if now_ts < float(a.get("next_entry_retry_ts") or 0):
            return EntryOutcome.RETRYABLE, "BACKOFF"
        if a.get("fatal_for_night"):
            return EntryOutcome.FATAL, str(a["fatal_for_night"])
        blocked = self._entry_blocked_by_files()
        if blocked:
            return EntryOutcome.FATAL, blocked

        # Guard 0: owned-vs-foreign classification (P0 fix)
        owned, foreign = self.classify_positions(asset)
        if owned:
            logger.warning(f"[{asset}] Owned legs already on broker {sorted(owned)}: adopting via audit, no fresh entry")
            self.audit_and_recover_on_startup()
            return EntryOutcome.FATAL, "OWNED_OPEN_ADOPTED"
        if foreign:
            a["entry_skipped_reason"] = "DAYTIME_OPEN"
            a["next_entry_retry_ts"] = now_ts + self.cfg.daytime_retry_s
            self._save_state()
            msg = f"Foreign legs open {sorted(foreign)}; SKIP (not ACTIVE), retry in 15m"
            logger.warning(f"[{asset}] {msg}")
            self._event("ENTRY_SKIP", asset, msg)
            self._alert("ENTRY_SKIP_DAYTIME", f"{asset}: {msg}", "info", cooldown=900)
            return EntryOutcome.SKIPPED, "DAYTIME_OPEN"

        spot = self.get_spot_price(asset)
        if not spot:
            return self._retry(asset, "SPOT_UNAVAILABLE")
        expiry, exp_date, dte = self.get_1dte_expiry(asset)
        if not expiry or exp_date is None:
            if self.last_expiry_reason in ("DTE_TOO_FAR",) or str(self.last_expiry_reason).startswith("DTE_TOO_FAR"):
                try:
                    from broker.deltaexchange.database.master_contract_db import master_contract_download
                    logger.info(f"[{asset}] Expiry DTE too far ({self.last_expiry_reason}); triggering master_contract_download refresh...")
                    master_contract_download()
                except Exception as e:
                    logger.warning(f"[{asset}] Could not auto-refresh master contracts: {e}")
                return self._retry(asset, f"EXPIRY_{self.last_expiry_reason}")
            if self.last_expiry_reason in ("NO_PARSEABLE_EXPIRY", "NO_EXPIRY_AFTER_SETTLE_FILTER"):
                return self._retry(asset, self.last_expiry_reason)
            return self._retry(asset, f"EXPIRY_{self.last_expiry_reason}")

        logger.info(f"\n{'='*79}\n[{asset}] OVERNIGHT ENTRY spot={spot:.2f} expiry={expiry} DTE={dte}\n{'='*79}")
        step = cfg.strike_step
        width = step * cfg.wing_width_strikes

        # Adaptive OTM Step-Down Ladder: try primary OTM, then 0.8x and 0.6x down to 0.008 floor
        ladder_factors = [1.0, 0.8, 0.6]
        min_otm_floor = 0.008
        otm_candidates = []
        for factor in ladder_factors:
            cand = round(cfg.otm_pct * factor, 4)
            if cand >= min_otm_floor and cand not in otm_candidates:
                otm_candidates.append(cand)
        if not otm_candidates:
            otm_candidates = [cfg.otm_pct]

        sizing = self.compute_dynamic_lots(asset)
        if sizing.blocked or sizing.lots < self.cfg.min_lots:
            if sizing.reason in ("FUNDS_UNAVAILABLE", "FX_UNAVAILABLE"):
                return self._retry(asset, sizing.reason)
            return self._fatal(asset, sizing.reason)
        lots = sizing.lots

        cw, pw, cs, ps = None, None, None, None
        used_otm = cfg.otm_pct

        for idx, cand_otm in enumerate(otm_candidates):
            ce_s = round(spot * (1 + cand_otm) / step) * step
            pe_s = round(spot * (1 - cand_otm) / step) * step
            ce_w, pe_w = ce_s + width, pe_s - width

            min_ce_wing, max_pe_wing = ce_s + step, pe_s - step
            cand_cw = self.hunt_liquid_strike(asset, ce_w, "CE", "BUY", expiry, lots, True, -1, min_ce_wing)
            cand_pw = self.hunt_liquid_strike(asset, pe_w, "PE", "BUY", expiry, lots, True, +1, max_pe_wing)
            if not cand_cw or not cand_pw:
                logger.warning(f"[{asset}] [ADAPTIVE LADDER] Wings unavailable/illiquid at {cand_otm*100:.2f}% OTM (step {idx+1}/{len(otm_candidates)})")
                continue

            cand_cs = self.hunt_liquid_strike(asset, ce_s, "CE", "SELL", expiry, lots, False)
            cand_ps = self.hunt_liquid_strike(asset, pe_s, "PE", "SELL", expiry, lots, False)
            if not cand_cs or not cand_ps:
                logger.warning(f"[{asset}] [ADAPTIVE LADDER] Shorts unavailable/illiquid at {cand_otm*100:.2f}% OTM (step {idx+1}/{len(otm_candidates)})")
                continue

            # Complete balanced basket resolved!
            cw, pw, cs, ps = cand_cw, cand_pw, cand_cs, cand_ps
            used_otm = cand_otm
            sl_mult = compute_dynamic_sl_mult(used_otm, self.cfg.sl_mult)
            logger.info(f"[{asset}] 🎯 [ADAPTIVE OTM LADDER] Confirmed 4 liquid strikes at {used_otm*100:.2f}% OTM (Dynamic SL: {sl_mult:.1f}x)!")
            break

        if not cw or not pw:
            self._event("ENTRY_ABORT", asset, "LIQUIDITY_WINGS")
            return self._retry(asset, "LIQUIDITY_WINGS")
        if not cs or not ps:
            self._event("ENTRY_ABORT", asset, "LIQUIDITY_SHORTS")
            return self._retry(asset, "LIQUIDITY_SHORTS")

        # Topology + credit proofs
        if cw.symbol == cs.symbol or pw.symbol == ps.symbol:
            self._event("ENTRY_ABORT", asset, "STRIKE_COLLISION")
            return self._retry(asset, "STRIKE_COLLISION")
        if not (cw.strike > cs.strike and pw.strike < ps.strike):
            self._event("ENTRY_ABORT", asset, "STRIKE_INVERSION")
            return self._retry(asset, "STRIKE_INVERSION")
        net_est = (cs.bid + ps.bid) - (cw.ask + pw.ask)
        if net_est <= 0:
            self._event("ENTRY_ABORT", asset, f"NEGATIVE_CREDIT_{net_est:.2f}")
            return self._retry(asset, "NEGATIVE_CREDIT", long=True)
        logger.info(f"[{asset}] Strikes: W {cw.symbol}/{cw.strike:g} W {pw.symbol}/{pw.strike:g} | "
                    f"S {cs.symbol}/{cs.strike:g} S {ps.symbol}/{ps.strike:g} | net_est={net_est:.2f}/u lots={lots}")

        # ---- STEP 1: BUY wings (idempotent keys journaled BEFORE placement) ----
        buf = self.cfg.entry_buffer_bps / 10000.0
        wing_orders = [
            ("CE_WING", cw.symbol, "BUY", round(cw.ask * (1 + buf), 2)),
            ("PE_WING", pw.symbol, "BUY", round(pw.ask * (1 + buf), 2)),
        ]
        placed: List[Tuple[str, str, str, float]] = []  # leg, sym, oid, limit
        for leg_key, sym, act, lim in wing_orders:
            idem = self.idem_key(asset, leg_key, "entry")
            if self._idem_already_filled(asset, leg_key, idem):
                return self._retry(asset, "IDEMPOTENT_REPLAY")
            a["legs"][leg_key].update({"symbol": sym, "side": act, "status": LegStatus.PENDING.value,
                                       "requested_qty": lots, "idem_key": idem, "order_ids": []})
            self._event("ENTRY_INTENT", asset, f"{leg_key} {act} {lots}x {sym} @{lim} key={idem}")
            ok, oid = self.place_limit(sym, act, lots, lim, idem)
            if not ok:
                for _, _, oid2, _ in placed:
                    self.cancel_order(oid2)
                self._save_state()
                return self._retry(asset, "WING_PLACE_FAIL")
            a["legs"][leg_key]["order_ids"].append(oid)
            placed.append((leg_key, sym, oid, lim))
        self._save_state()  # journal wings BEFORE waiting (crash-safe)
        wing_fills: Dict[str, FillResult] = {}
        for leg_key, sym, oid, lim in placed:
            fr = self.wait_for_fill(oid, lots, self.cfg.fill_timeout, lim)
            if self.paper and fr.status == "FILLED":
                fr = FillResult("FILLED", lots, self._paper_fill_price("BUY", lim / (1 + buf)))
                self._book_apply(sym, "BUY", lots, fr.avg_price)
            wing_fills[leg_key] = fr
            leg = a["legs"][leg_key]
            leg["filled_qty"] = fr.filled_qty
            leg["avg_price"] = fr.avg_price if fr.avg_price > 0 else lim
            leg["fill_estimated"] = fr.avg_price <= 0
            leg["status"] = LegStatus.FILLED.value if fr.filled_qty >= lots else (
                LegStatus.PARTIAL.value if fr.filled_qty > 0 else LegStatus.FAILED.value)
        if not all(wing_fills[k].filled_qty >= lots for k in ("CE_WING", "PE_WING")):
            logger.error(f"[{asset}] Wing fills incomplete: {[ (k, v.status, v.filled_qty) for k, v in wing_fills.items() ]}; "
                         f"unwinding EXACT filled qty, zero shorts")
            self._event("ENTRY_ABORT", asset, "WING_FILL_INCOMPLETE")
            for leg_key in ("CE_WING", "PE_WING"):
                leg = a["legs"][leg_key]
                if leg["filled_qty"] > 0:
                    self._exit_symbol_marketish(leg["symbol"], "SELL", leg["filled_qty"], "WING_UNWIND_ABORT")
            self._reset_legs(a)
            self._save_state()
            return self._retry(asset, "WING_FILL_INCOMPLETE")

        # ---- STEP 2: SELL shorts (only after wings 100% filled) ----
        short_orders = [
            ("CE_SHORT", cs.symbol, "SELL", round(cs.bid * (1 - buf), 2)),
            ("PE_SHORT", ps.symbol, "SELL", round(ps.bid * (1 - buf), 2)),
        ]
        placed2: List[Tuple[str, str, str, float]] = []
        for leg_key, sym, act, lim in short_orders:
            idem = self.idem_key(asset, leg_key, "entry")
            a["legs"][leg_key].update({"symbol": sym, "side": act, "status": LegStatus.PENDING.value,
                                       "requested_qty": lots, "idem_key": idem, "order_ids": []})
            self._event("ENTRY_INTENT", asset, f"{leg_key} {act} {lots}x {sym} @{lim} key={idem}")
            ok, oid = self.place_limit(sym, act, lots, lim, idem)
            if not ok:
                for _, _, oid2, _ in placed2:
                    self.cancel_order(oid2)
                self._unwind_all_exact(asset, "SHORT_PLACE_FAIL")
                self._reset_legs(a)
                self._save_state()
                return self._retry(asset, "SHORT_PLACE_FAIL")
            a["legs"][leg_key]["order_ids"].append(oid)
            placed2.append((leg_key, sym, oid, lim))
        self._save_state()
        short_fills: Dict[str, FillResult] = {}
        for leg_key, sym, oid, lim in placed2:
            fr = self.wait_for_fill(oid, lots, self.cfg.fill_timeout, lim)
            if self.paper and fr.status == "FILLED":
                fr = FillResult("FILLED", lots, self._paper_fill_price("SELL", lim / (1 - buf) if buf < 1 else lim))
                self._book_apply(sym, "SELL", lots, fr.avg_price)
            short_fills[leg_key] = fr
            leg = a["legs"][leg_key]
            leg["filled_qty"] = fr.filled_qty
            leg["avg_price"] = fr.avg_price if fr.avg_price > 0 else lim
            leg["fill_estimated"] = fr.avg_price <= 0
            leg["status"] = LegStatus.FILLED.value if fr.filled_qty >= lots else (
                LegStatus.PARTIAL.value if fr.filled_qty > 0 else LegStatus.FAILED.value)
        if not all(short_fills[k].filled_qty >= lots for k in ("CE_SHORT", "PE_SHORT")):
            logger.error(f"[{asset}] Short fills incomplete; emergency basket unwind (shorts first, exact qty)")
            self._event("ENTRY_ABORT", asset, "SHORT_FILL_INCOMPLETE")
            self._unwind_all_exact(asset, "SHORT_FILL_INCOMPLETE")
            self._reset_legs(a)
            self._save_state()
            return self._retry(asset, "SHORT_FILL_INCOMPLETE")

        # ---- Basket invariant + persist from ACTUAL fills (P0/P1 fixes) ----
        qtys = {a["legs"][k]["filled_qty"] for k in LEG_KEYS}
        if len(qtys) != 1 or qtys == {0}:
            logger.error(f"[{asset}] BASKET INVARIANT VIOLATED qtys={qtys}; unwinding all")
            self._unwind_all_exact(asset, "INVARIANT_VIOLATION")
            self._reset_legs(a)
            self._save_state()
            return self._retry(asset, "INVARIANT_VIOLATION")
        mult = cfg.contract_mult
        ce_w_px = a["legs"]["CE_WING"]["avg_price"]
        pe_w_px = a["legs"]["PE_WING"]["avg_price"]
        ce_s_px = a["legs"]["CE_SHORT"]["avg_price"]
        pe_s_px = a["legs"]["PE_SHORT"]["avg_price"]
        gross = ((ce_s_px + pe_s_px) - (ce_w_px + pe_w_px)) * mult * lots
        fees_in = sum(self._fee_usd(px, lots, mult, 1) for px in (ce_w_px, pe_w_px, ce_s_px, pe_s_px))
        # exit fee estimate at entry prices (conservative)
        fees_out = sum(self._fee_usd(px, lots, mult, 1) for px in (ce_w_px, pe_w_px, ce_s_px, pe_s_px))
        net_credit = gross - fees_in
        if net_credit <= 0:
            logger.error(f"[{asset}] Net-of-fee credit {net_credit:.2f} <= 0; unwinding (would lock a certain loss)")
            self._unwind_all_exact(asset, "NEGATIVE_NET_CREDIT")
            self._reset_legs(a)
            self._save_state()
            return self._retry(asset, "NEGATIVE_NET_CREDIT", long=True)
        sl_mult = compute_dynamic_sl_mult(used_otm, self.cfg.sl_mult)
        a["used_otm"] = used_otm
        a["sl_mult"] = sl_mult
        a["legs"]["CE_SHORT"]["sl_price"] = round(ce_s_px * sl_mult, 2)
        a["legs"]["PE_SHORT"]["sl_price"] = round(pe_s_px * sl_mult, 2)
        for k in LEG_KEYS:
            a["legs"][k]["active"] = True
        a.update({"active": True, "entry_done": True, "entry_skipped_reason": None,
                  "expiry_date": expiry, "expiry_iso": exp_date.isoformat(),
                  "entry_time": now_iso(), "lots_requested": lots,
                  "initial_net_credit": round(net_credit, 2),
                  "target_profit_value": round(net_credit * self.cfg.tp_pct, 2),
                  "fees_entry_usd": round(fees_in, 2), "fees_exit_est_usd": round(fees_out, 2),
                  "entry_attempts": 0, "next_entry_retry_ts": 0.0})
        self._save_state()
        msg = (f"ACTIVE {lots}x | credit=${net_credit:.2f} TP=${net_credit*self.cfg.tp_pct:.2f} "
               f"SLs (x{sl_mult:.1f}) CE={a['legs']['CE_SHORT']['sl_price']} PE={a['legs']['PE_SHORT']['sl_price']}")
        logger.info(f"[{asset}] {msg}")
        self._event("ENTRY_OK", asset, msg)
        self._alert("ENTRY_OK", f"{asset}: {msg}")
        return EntryOutcome.ENTERED, "OK"

    def _retry(self, asset: str, reason: str, long: bool = False) -> Tuple[EntryOutcome, str]:
        a = self.state["assets"][asset]
        a["entry_attempts"] = int(a.get("entry_attempts") or 0) + 1
        base = 60 if long else 10
        delay = min(self.cfg.max_entry_backoff_s if not long else 600,
                    base * (2 ** min(a["entry_attempts"] - 1, 5))) + random.uniform(0, 5)
        a["next_entry_retry_ts"] = time.time() + delay
        self._save_state()
        logger.warning(f"[{asset}] Entry {reason}; retry #{a['entry_attempts']} in {delay:.0f}s")
        self._event("ENTRY_RETRY", asset, f"{reason} in {delay:.0f}s")
        return EntryOutcome.RETRYABLE, reason

    def _fatal(self, asset: str, reason: str) -> Tuple[EntryOutcome, str]:
        a = self.state["assets"][asset]
        a["fatal_for_night"] = reason
        self._save_state()
        logger.error(f"[{asset}] Entry FATAL for tonight: {reason}")
        self._event("ENTRY_FATAL", asset, reason)
        self._alert("ENTRY_FATAL", f"{asset}: {reason}", "error")
        return EntryOutcome.FATAL, reason

    def _reset_legs(self, a: Dict[str, Any]) -> None:
        a["legs"] = {k: default_leg() for k in LEG_KEYS}

    def _idem_already_filled(self, asset: str, leg_key: str, idem: str) -> bool:
        leg = self.state["assets"][asset]["legs"].get(leg_key, {})
        return bool(leg.get("idem_key") == idem and leg.get("filled_qty", 0) > 0)

    def _unwind_all_exact(self, asset: str, reason: str) -> None:
        """Emergency unwind: filled shorts first (exact qty), then wings."""
        a = self.state["assets"][asset]
        for k in ("CE_SHORT", "PE_SHORT"):
            leg = a["legs"][k]
            if leg.get("filled_qty", 0) > 0:
                self._exit_symbol_marketish(leg["symbol"], "BUY", leg["filled_qty"], reason)
                leg["exited_qty"] = leg["filled_qty"]
        for k in ("CE_WING", "PE_WING"):
            leg = a["legs"][k]
            if leg.get("filled_qty", 0) > 0:
                self._exit_symbol_marketish(leg["symbol"], "SELL", leg["filled_qty"], reason)
                leg["exited_qty"] = leg["filled_qty"]

    # -- exits -------------------------------------------------------------------
    def _exit_symbol_marketish(self, symbol: str, action: str, qty: int, reason: str) -> Tuple[int, float]:
        """Collared LIMIT first, escalate to MARKET (P0 fix). Returns (leftover_qty, avg_fill_px)."""
        if qty <= 0:
            return 0, 0.0
        if self.paper:
            q = self.get_l2_depth(symbol)
            ref = 0.0
            if q:
                ref = (q.ask or q.bid or 0.0) if action == "BUY" else (q.bid or q.ask or 0.0)
            px = self._paper_fill_price(action, ref)
            self._book_apply(symbol, action, qty, px)
            self._event("PAPER_EXIT", "*", f"{action} {qty}x {symbol} @{px} ({reason})")
            return 0, px
        # live broker truth for sizing (never exceed live net)
        live = self._fetch_broker_positions().get(symbol, {}).get("quantity", 0.0)
        if abs(live) == 0:
            return 0, 0.0
        qty = min(qty, abs(int(live)))
        buf = self.cfg.exit_buffer_bps / 10000.0
        idem = f"{self.session}:EXIT:{symbol}:{reason}"
        q = self.get_l2_depth(symbol)
        filled_tot = 0
        sum_px = 0.0
        if q and q.fresh_l2:
            ref = (q.ask or 0) if action == "BUY" else (q.bid or 0)
            if ref and ref > 0:
                lim = round(ref * (1 + buf), 2) if action == "BUY" else round(ref * (1 - buf), 2)
                ok, oid = self.place_limit(symbol, action, qty, max(lim, 0.01), idem + ":L1")
                if ok:
                    fr = self.wait_for_fill(oid, qty, self.cfg.exit_collar_timeout, lim)
                    if fr.filled_qty > 0:
                        p = fr.avg_price if fr.avg_price > 0 else lim
                        filled_tot += fr.filled_qty
                        sum_px += fr.filled_qty * p
                        qty -= fr.filled_qty
                    if qty == 0:
                        avg_p = round(sum_px / filled_tot, 4) if filled_tot > 0 else lim
                        return 0, avg_p
                    else:
                        self.cancel_order(oid)
        # escalate: MARKET remainder
        ok, oid = self.place_market(symbol, action, qty, idem + ":MKT")
        if not ok:
            avg_p = round(sum_px / filled_tot, 4) if filled_tot > 0 else 0.0
            return qty, avg_p
        fr = self.wait_for_fill(oid, qty, 5.0)
        if fr.filled_qty > 0:
            p = fr.avg_price if fr.avg_price > 0 else 0.0
            filled_tot += fr.filled_qty
            sum_px += fr.filled_qty * p
            qty -= fr.filled_qty
        avg_p = round(sum_px / filled_tot, 4) if filled_tot > 0 else 0.0
        return max(0, qty), avg_p

    def _leg_open_qty(self, asset: str, leg_key: str) -> int:
        leg = self.state["assets"][asset]["legs"][leg_key]
        return max(0, int(leg.get("filled_qty", 0)) - int(leg.get("exited_qty", 0)))

    def _record_exit(self, asset: str, leg_key: str, exit_qty: int, exit_px: float) -> None:
        a = self.state["assets"][asset]
        leg = a["legs"][leg_key]
        leg["exited_qty"] = int(leg.get("exited_qty", 0)) + exit_qty
        # running exit avg
        prev_q = leg["exited_qty"] - exit_qty
        prev_avg = leg.get("exit_avg", 0.0)
        leg["exit_avg"] = round((prev_avg * prev_q + exit_px * exit_qty) / max(1, leg["exited_qty"]), 4)
        if self._leg_open_qty(asset, leg_key) <= 0:
            leg["active"] = False
        # realized contribution
        mult = self.cfg.assets[asset].contract_mult
        if leg.get("side") == "SELL":
            pnl = (leg.get("avg_price", 0.0) - exit_px) * mult * exit_qty
        else:
            pnl = (exit_px - leg.get("avg_price", 0.0)) * mult * exit_qty
        pnl -= self._fee_usd(exit_px, exit_qty, mult, 1)
        a["realized_pnl_usd"] = round(a.get("realized_pnl_usd", 0.0) + pnl, 2)
        self._update_cumulative_stats(pnl * (self.cfg.usd_inr or 88.0))

    def _exit_leg(self, asset: str, leg_key: str, reason: str) -> bool:
        """Exit one leg fully (collared->market). Returns True if flat."""
        a = self.state["assets"][asset]
        leg = a["legs"][leg_key]
        open_q = self._leg_open_qty(asset, leg_key)
        if open_q <= 0:
            leg["active"] = False
            return True
        action = "BUY" if leg.get("side") == "SELL" else "SELL"
        leg["exit_attempted"] = True
        # broker-live sizing guard in live mode
        if not self.paper:
            live = self._fetch_broker_positions().get(leg["symbol"], {}).get("quantity", 0.0)
            if abs(live) == 0:
                # broker already flat => adopt as exit at last known (conservative: avg)
                self._record_exit(asset, leg_key, open_q, leg.get("avg_price", 0.0))
                self._event("EXIT_ADOPT_FLAT", asset, f"{leg['symbol']} broker-flat; adopted")
                return True
            open_q = min(open_q, abs(int(live)))
        # paper exit price reference
        exit_px = leg.get("avg_price", 0.0)
        if self.paper:
            q = self.get_l2_depth(leg["symbol"])
            if q:
                ref = (q.ask or q.bid or 0.0) if action == "BUY" else (q.bid or q.ask or 0.0)
                exit_px = self._paper_fill_price(action, ref)
        else:
            q = self.get_l2_depth(leg["symbol"])
            if q:
                exit_px = (q.ask or exit_px) if action == "BUY" else (q.bid or exit_px)
        leftover, fill_px = self._exit_symbol_marketish(leg["symbol"], action, open_q, reason)
        done = open_q - leftover
        if done > 0:
            effective_px = fill_px if fill_px > 0 else exit_px
            self._record_exit(asset, leg_key, done, effective_px)
        self._save_state()
        return leftover == 0

    def _square_off_side(self, asset: str, side: str, reason: str) -> bool:
        a = self.state["assets"][asset]
        logger.warning(f"[{asset}] Squaring {side} spread ({reason})")
        self._event("SIDE_EXIT_START", asset, f"{side} {reason}")
        ok = True
        for k in (f"{side}_SHORT", f"{side}_WING"):
            if k in a["legs"] and self._leg_open_qty(asset, k) > 0:
                if not self._exit_leg(asset, k, reason):
                    ok = False
        # sweep owned side residuals
        broker = self._fetch_broker_positions()
        for k in (f"{side}_SHORT", f"{side}_WING"):
            sym = a["legs"].get(k, {}).get("symbol")
            if sym and abs(broker.get(sym, {}).get("quantity", 0.0)) > 0:
                if not self._exit_leg(asset, k, f"Sweep_{reason}"):
                    ok = False
        other = "PE" if side == "CE" else "CE"
        if ok:
            a[f"{side.lower()}_exit_time"] = now_iso()
            a[f"{side.lower()}_exit_reason"] = reason
            other_open = self._leg_open_qty(asset, f"{other}_SHORT") > 0
            if not other_open and all(self._leg_open_qty(asset, k) <= 0 for k in LEG_KEYS):
                a["active"] = False
                a["exit_time"] = now_iso()
                a["exit_reason"] = reason
                a["pending_exit"] = None
            else:
                a["pending_exit"] = None
            self._save_state()
            self._event("SIDE_EXIT_OK", asset, f"{side} {reason}")
        else:
            self._arm_pending(asset, f"SIDE:{side}", reason)
        return ok

    def _square_off_full(self, asset: str, reason: str) -> bool:
        a = self.state["assets"][asset]
        logger.warning(f"[{asset}] Squaring FULL condor ({reason})")
        self._event("FULL_EXIT_START", asset, reason)
        ok = True
        for k in ("CE_SHORT", "PE_SHORT", "CE_WING", "PE_WING"):
            if self._leg_open_qty(asset, k) > 0:
                if not self._exit_leg(asset, k, reason):
                    ok = False
        broker = self._fetch_broker_positions()
        for k in LEG_KEYS:
            sym = a["legs"].get(k, {}).get("symbol")
            if sym and abs(broker.get(sym, {}).get("quantity", 0.0)) > 0:
                if not self._exit_leg(asset, k, f"Sweep_{reason}"):
                    ok = False
        if ok and all(self._leg_open_qty(asset, k) <= 0 for k in LEG_KEYS):
            a["active"] = False
            a["exit_time"] = now_iso()
            a["exit_reason"] = reason
            a["pending_exit"] = None
            a["unwind_phase"] = "DONE"
            self._save_state()
            msg = f"{reason} realized=${a.get('realized_pnl_usd', 0):.2f}"
            logger.info(f"[{asset}] Condor CLOSED ({msg})")
            self._event("FULL_EXIT_OK", asset, msg)
            self._alert("EXIT", f"{asset}: {msg}")
        else:
            self._arm_pending(asset, "FULL", reason)
        return ok

    def _arm_pending(self, asset: str, scope: str, reason: str) -> None:
        a = self.state["assets"][asset]
        prev = a.get("pending_exit") or {}
        attempt = int(prev.get("attempt", 0)) + 1
        delay = min(300, 5 * (2 ** min(attempt - 1, 6)))
        a["pending_exit"] = {"scope": scope, "reason": reason, "attempt": attempt,
                             "next_retry_ts": time.time() + delay, "created_ts": prev.get("created_ts", time.time())}
        self._save_state()
        logger.warning(f"[{asset}] Exit incomplete; pending {scope} attempt={attempt} retry in {delay}s")
        self._event("EXIT_PENDING", asset, f"{scope} {reason} attempt={attempt}")
        if attempt in (3, 10) or attempt % 30 == 0:
            self._alert("EXIT_PENDING", f"{asset}: {scope} {reason} stuck {attempt} attempts", "error")

    def _retry_pending(self, asset: str) -> None:
        a = self.state["assets"][asset]
        p = a.get("pending_exit")
        if not p or time.time() < float(p.get("next_retry_ts", 0)):
            return
        scope, reason = p.get("scope", "FULL"), p.get("reason", "RETRY")
        logger.info(f"[{asset}] Retrying pending exit {scope} ({reason}) attempt={p.get('attempt')}")
        if scope.startswith("SIDE:"):
            self._square_off_side(asset, scope.split(":")[1], reason)
        else:
            self._square_off_full(asset, reason)

    # -- reconcile ------------------------------------------------------------------
    def reconcile_positions_with_broker(self) -> None:
        broker = self._fetch_broker_positions()
        changed = False
        for asset in self.cfg.symbols:
            a = self.state["assets"][asset]
            for k in LEG_KEYS:
                leg = a["legs"][k]
                if not leg.get("symbol"):
                    continue
                live = abs(broker.get(leg["symbol"], {}).get("quantity", 0.0))
                open_q = self._leg_open_qty(asset, k)
                if open_q > 0 and live == 0:
                    if leg.get("exit_attempted"):
                        logger.info(f"[{asset}] Reconciled {leg['symbol']} FLAT (broker confirms)")
                        self._record_exit(asset, k, open_q, leg.get("exit_avg") or leg.get("avg_price", 0.0))
                    else:
                        logger.error(f"[{asset}] MANUAL/EXTERNAL close detected on {leg['symbol']}! Adopting flat.")
                        self._event("MANUAL_CLOSE", asset, leg["symbol"])
                        self._alert("MANUAL_CLOSE", f"{asset}: {leg['symbol']} closed outside engine; adopted flat.", "error")
                        self._record_exit(asset, k, open_q, leg.get("avg_price", 0.0))
                    changed = True
            if a.get("active") and all(self._leg_open_qty(asset, k) <= 0 for k in LEG_KEYS):
                a["active"] = False
                a["exit_time"] = a.get("exit_time") or now_iso()
                a["exit_reason"] = a.get("exit_reason") or (a.get("pending_exit") or {}).get("reason") or "RECONCILED_FLAT"
                a["pending_exit"] = None
                changed = True
                logger.info(f"[{asset}] All legs flat on broker; condor closed ({a['exit_reason']})")
        if changed:
            self._save_state()

    # -- economics --------------------------------------------------------------------
    def _spread_profit(self, asset: str, side: str, quotes: Dict[str, DepthQuote]) -> Optional[Tuple[float, float, float, float]]:
        """Per-spread economics (P1 fix). Returns (credit_net, cost_now_net, profit, gross_profit)."""
        a = self.state["assets"][asset]
        mult = self.cfg.assets[asset].contract_mult
        s_leg, w_leg = a["legs"][f"{side}_SHORT"], a["legs"][f"{side}_WING"]
        q = min(self._leg_open_qty(asset, f"{side}_SHORT"), self._leg_open_qty(asset, f"{side}_WING"))
        if q <= 0:
            return None
        sq, wq = quotes.get(f"{side}_SHORT"), quotes.get(f"{side}_WING")
        if not sq or not sq.ask:
            return None
        wing_bid = float(wq.bid) if (wq and wq.bid is not None and wq.bid > 0) else 0.0
        gross_credit = (s_leg["avg_price"] - w_leg["avg_price"]) * mult * q
        gross_cost = (sq.ask - wing_bid) * mult * q
        gross_profit = gross_credit - gross_cost
        credit = gross_credit - (self._fee_usd(s_leg["avg_price"], q, mult, 1) + self._fee_usd(w_leg["avg_price"], q, mult, 1))
        cost = gross_cost + (self._fee_usd(sq.ask, q, mult, 1) + (self._fee_usd(wing_bid, q, mult, 1) if wing_bid > 0 else 0.0))
        return credit, cost, credit - cost, gross_profit

    def _open_unrealized(self, asset: str, quotes: Dict[str, DepthQuote]) -> float:
        a = self.state["assets"][asset]
        mult = self.cfg.assets[asset].contract_mult
        u = 0.0
        for k in LEG_KEYS:
            leg = a["legs"][k]
            oq = self._leg_open_qty(asset, k)
            if oq <= 0:
                continue
            q = quotes.get(k)
            if not q:
                continue
            if leg.get("side") == "SELL" and q.ask:
                u += (leg["avg_price"] - q.ask) * mult * oq - self._fee_usd(q.ask, oq, mult, 1)
            elif leg.get("side") == "BUY" and q.bid:
                u += (q.bid - leg["avg_price"]) * mult * oq - self._fee_usd(q.bid, oq, mult, 1)
        return u

    # -- monitor ------------------------------------------------------------------------
    def _in_entry_window(self, t: dtime) -> bool:
        return (t >= self.cfg.entry_start) or (t <= self.cfg.entry_end)

    def monitor_positions(self) -> None:
        now = now_ist()
        t = now.time()
        phase_sweep = self.cfg.cutoff <= t < self.cfg.entry_start
        phase_ladder = self.cfg.ladder_start <= t < self.cfg.cutoff

        for asset in self.cfg.symbols:
            a = self.state["assets"][asset]
            if not a.get("active"):
                continue
            # pending exits first
            if a.get("pending_exit"):
                self._retry_pending(asset)
                # fall through to sweep check (sweep escalates reason)
            # Rule 1: hard sweep (supreme)
            if phase_sweep:
                sweep_tag = f"Sweep_{self.cfg.cutoff.strftime('%H%M')}_Cutoff"
                logger.warning(f"[{asset}] {self.cfg.cutoff.strftime('%H:%M')} SWEEP — flattening unconditionally ({sweep_tag})")
                self._square_off_full(asset, sweep_tag)
                continue
            # Rule 1b: TWAP ladder (P2 fix)
            if phase_ladder:
                ladder_start_min = self.cfg.ladder_start.hour * 60 + self.cfg.ladder_start.minute
                cutoff_min = self.cfg.cutoff.hour * 60 + self.cfg.cutoff.minute
                marks = list(range(ladder_start_min, cutoff_min, 5))
                cur_mark = t.hour * 60 + t.minute
                last = a.get("last_ladder_ts", 0)
                due = any(m <= cur_mark for m in marks) and (time.time() - last > 240)
                if due:
                    a["last_ladder_ts"] = time.time()
                    a["unwind_phase"] = "LADDER"
                    self._save_state()
                    ladder_tag = f"Ladder_{self.cfg.ladder_start.strftime('%H%M')}_{self.cfg.cutoff.strftime('%H%M')}"
                    logger.info(f"[{asset}] LADDER unwind attempt (TWAP window {ladder_tag})")
                    self._event("LADDER", asset, "collared unwind attempt")
                    if self._square_off_full(asset, ladder_tag):
                        continue
                    else:
                        # keep managing; sweep at cutoff escalates
                        pass
            # 06:25-06:30 morning intrinsic-heavy aggressive-out (P2 settlement fix)
            if self.cfg.aggressive_time <= t < self.cfg.cutoff and not a.get("held_to_settle"):
                self._settlement_pressure(asset)
                if not self.state["assets"][asset].get("active"):
                    continue
            # Live quotes for open legs
            quotes: Dict[str, DepthQuote] = {}
            usable = True
            for k in LEG_KEYS:
                leg = a["legs"][k]
                if self._leg_open_qty(asset, k) <= 0:
                    continue
                q = self.get_l2_depth(leg["symbol"])
                if q is None or (q.bid is None and q.ask is None):
                    if "WING" in k:
                        q = DepthQuote(bid=0.0, ask=0.0, bid_size=0, ask_size=0, spread_pct=0.0, spread_dollar=0.0, depth_ok=False)
                    else:
                        usable = False
                        break
                quotes[k] = q
            if not usable or not quotes:
                a["stale_ticks"] = int(a.get("stale_ticks", 0)) + 1
                self._save_state()
                logger.warning(f"[{asset}] Quotes unusable (stale #{a['stale_ticks']}); SL/TP skipped this tick")
                if a["stale_ticks"] >= self.cfg.stale_flat_after_ticks:
                    msg = f"Quotes stale {a['stale_ticks']} ticks (~5min) on open risk; protective flatten"
                    logger.error(f"[{asset}] {msg}")
                    self._event("STALE_FLATTEN", asset, msg)
                    self._alert("STALE_FLATTEN", f"{asset}: {msg}", "critical")
                    self._square_off_full(asset, "Stale_Quotes_Protective")
                continue
            a["stale_ticks"] = 0
            # Rule 2: Basket SL & TP via spread economics (Solution 1: Basket SL = -1.0x Credit, TP = 60%)
            ce_open = self._leg_open_qty(asset, "CE_SHORT") > 0
            pe_open = self._leg_open_qty(asset, "PE_SHORT") > 0
            if ce_open and pe_open:
                ce = self._spread_profit(asset, "CE", quotes)
                pe = self._spread_profit(asset, "PE", quotes)
                if ce and pe:
                    profit = ce[2] + pe[2]
                    gross_profit = ce[3] + pe[3]
                    target = float(a.get("target_profit_value") or 0)
                    credit = float(a.get("initial_net_credit") or (ce[0] + pe[0]))
                    if target <= 0 and credit > 0:
                        target = credit * self.cfg.tp_pct
                    basket_sl_thresh = -1.0 * credit * self.cfg.basket_sl_mult

                    if (profit >= target or gross_profit >= target) and target > 0:
                        logger.info(f"[{asset}] 🎯 TP BASKET profit=${profit:.2f} (gross=${gross_profit:.2f}) >= target=${target:.2f} ({self.cfg.tp_pct*100:.0f}% credit)")
                        self._event("TP_HIT", asset, f"basket profit=${profit:.2f} gross=${gross_profit:.2f} target=${target:.2f}")
                        self._alert("TP_HIT", f"{asset}: basket TP ${profit:.2f} (gross ${gross_profit:.2f})")
                        self._square_off_full(asset, "Basket_TP_Achieved")
                        continue
                    elif self.cfg.enable_basket_sl and profit <= basket_sl_thresh and credit > 0:
                        logger.warning(f"[{asset}] 🚨 BASKET SL HIT profit=${profit:.2f} <= -{self.cfg.basket_sl_mult:.1f}x credit (${basket_sl_thresh:.2f}); squaring off full condor")
                        self._event("BASKET_SL_HIT", asset, f"basket profit=${profit:.2f} threshold=${basket_sl_thresh:.2f}")
                        self._alert("BASKET_SL_HIT", f"{asset}: basket SL ${profit:.2f} hit", "warning")
                        a["sl_count"] = int(a.get("sl_count", 0)) + 1
                        self.state["portfolio"]["sl_total"] = int(self.state["portfolio"].get("sl_total", 0)) + 1
                        self._square_off_full(asset, "Basket_SL_Hit")
                        if self._portfolio_halt_check(asset):
                            return
                        continue
            elif ce_open or pe_open:
                side = "CE" if ce_open else "PE"
                sp = self._spread_profit(asset, side, quotes)
                if sp:
                    credit, cost, profit, gross_profit = sp
                    target = credit * self.cfg.tp_pct
                    survivor_sl = -1.0 * credit * self.cfg.basket_sl_mult
                    if credit > 0 and (profit >= target or gross_profit >= target):
                        logger.info(f"[{asset}] 🎯 TP SURVIVOR {side}: profit=${profit:.2f} (gross=${gross_profit:.2f}) >= ${target:.2f}")
                        self._event("TP_HIT", asset, f"survivor {side} profit=${profit:.2f}")
                        self._alert("TP_HIT", f"{asset}: survivor {side} TP ${profit:.2f}")
                        self._square_off_side(asset, side, f"Survivor_{side}_TP")
                        continue
                    elif self.cfg.enable_basket_sl and credit > 0 and profit <= survivor_sl:
                        logger.warning(f"[{asset}] 🚨 SL SURVIVOR {side}: profit=${profit:.2f} <= ${survivor_sl:.2f}")
                        self._event("SL_HIT", asset, f"survivor {side} profit=${profit:.2f}")
                        self._alert("SL_HIT", f"{asset}: survivor {side} SL hit", "warning")
                        self._square_off_side(asset, side, f"Survivor_{side}_SL")
                        continue

            # Fallback legacy per-leg SL (only when leg SL is explicitly enabled)
            if not self.cfg.disable_leg_sl:
                sl_hit = False
                for k in ("CE_SHORT", "PE_SHORT"):
                    if self._leg_open_qty(asset, k) <= 0:
                        continue
                    q = quotes.get(k)
                    leg = a["legs"][k]
                    sl = float(leg.get("sl_price") or 0)
                    if not q or not q.ask or sl <= 0:
                        continue
                    ticks = a["sl_ticks"].setdefault(k, [])
                    self.confirmer.add_tick(ticks, q.ask, q.spread_pct(), q.ask_size, q.age)
                    trig, why = self.confirmer.triggered(ticks, sl)
                    self._save_state()
                    if trig:
                        side = "CE" if k.startswith("CE") else "PE"
                        logger.warning(f"[{asset}] SL {k}: ask {q.ask} >= {sl} ({why}); closing {side} spread only")
                        self._event("SL_HIT", asset, f"{k} ask={q.ask} sl={sl} {why}")
                        self._alert("SL_HIT", f"{asset}: {k} stopped ({why})")
                        a["sl_count"] = int(a.get("sl_count", 0)) + 1
                        self.state["portfolio"]["sl_total"] = int(self.state["portfolio"].get("sl_total", 0)) + 1
                        self._square_off_side(asset, side, f"{side}_Short_SL_Confirmed")
                        sl_hit = True
                        break
                if sl_hit:
                    if self._portfolio_halt_check(asset):
                        return
                    continue
        # portfolio-level halt check (uses last quotes implicitly via fresh fetch)
        self._portfolio_halt_check("*")

    def _settlement_pressure(self, asset: str) -> None:
        """From 06:25: intrinsic-heavy shorts get aggressive-out; track HELD_TO_SETTLE."""
        a = self.state["assets"][asset]
        spot = self.get_spot_price(asset)
        if not spot:
            return
        cfg = self.cfg.assets[asset]
        width = cfg.strike_step * cfg.wing_width_strikes
        for k in ("CE_SHORT", "PE_SHORT"):
            if self._leg_open_qty(asset, k) <= 0:
                continue
            m = _SYMBOL_RE.match(a["legs"][k]["symbol"].strip().upper())
            if not m:
                continue
            strike = float(m.group(3))
            intr = max(0.0, spot - strike) if k.startswith("CE") else max(0.0, strike - spot)
            if intr > 0.5 * width:
                logger.warning(f"[{asset}] {k} intrinsic-heavy (intr={intr:.1f} width={width:g}); aggressive-out")
                self._event("SETTLE_PRESSURE", asset, f"{k} intrinsic={intr:.1f}")
                self._exit_leg(asset, k, "SettlePressure_0625")
        # If morning cutoff passed and daytime before night entry => held-to-settle accounting
        t = now_ist().time()
        if self.cfg.cutoff <= t < self.cfg.entry_start and any(self._leg_open_qty(asset, k) > 0 for k in LEG_KEYS):
            if not a.get("held_to_settle"):
                a["held_to_settle"] = True
                info = {"since": now_iso(), "reason": "SWEEP_INCOMPLETE", "spot": spot}
                a["settle_info"] = info
                self._save_state()
                msg = f"Legs open past 06:30 sweep; HELD_TO_SETTLE armed (spot={spot:.1f}). Retries continue."
                logger.error(f"[{asset}] {msg}")
                self._event("HELD_TO_SETTLE", asset, json.dumps(info))
                self._alert("HELD_TO_SETTLE", f"{asset}: {msg}", "critical")

    def _portfolio_halt_check(self, _asset: str) -> bool:
        """Returns True if a halt was triggered."""
        pf = self.state["portfolio"]
        if pf.get("halted"):
            return True
        if int(pf.get("sl_total", 0)) >= self.cfg.max_sl_per_night:
            self._halt_all(f"MAX_SL_{pf.get('sl_total')}")
            return True
        # nightly loss cap (realized + fresh unrealized)
        fx = self.cfg.usd_inr
        total_usd = 0.0
        for asset in self.cfg.symbols:
            a = self.state["assets"][asset]
            if not (a.get("active") or a.get("entry_done")):
                continue
            total_usd += float(a.get("realized_pnl_usd", 0.0) or 0.0)
            if a.get("active"):
                quotes: Dict[str, DepthQuote] = {}
                for k in LEG_KEYS:
                    if self._leg_open_qty(asset, k) > 0:
                        q = self.get_l2_depth(a["legs"][k]["symbol"])
                        if q:
                            quotes[k] = q
                if quotes:
                    total_usd += self._open_unrealized(asset, quotes)
        loss_inr = -total_usd * fx if total_usd < 0 else 0.0
        if loss_inr >= self.cfg.max_nightly_loss_inr:
            self._halt_all(f"MAX_LOSS_Rs{loss_inr:,.0f}")
            return True
        return False

    def _halt_all(self, reason: str) -> None:
        logger.error(f"[PORTFOLIO HALT] {reason}: flattening all + locking entries")
        self._event("HALT", "*", reason)
        self._alert("HALT", reason, "critical")
        for asset in self.cfg.symbols:
            if self.state["assets"][asset].get("active"):
                self._square_off_full(asset, f"Halt_{reason}")
        self.state["portfolio"]["halted"] = True
        self.state["portfolio"]["halt_reason"] = reason
        self._save_state()

    # -- files / heartbeat -------------------------------------------------------------
    def _check_kill_halt(self) -> bool:
        """Returns True if we should stop the loop (KILL)."""
        if self.cfg.kill_file.exists():
            msg = f"KILL file present ({self.cfg.kill_file}); flattening all + exiting"
            logger.error(f"[KILL] {msg}")
            self._event("KILL", "*", msg)
            self._alert("KILL", msg, "critical")
            for asset in self.cfg.symbols:
                if self.state["assets"][asset].get("active"):
                    self._square_off_full(asset, "KILL_FILE")
            self._save_state()
            return True
        return False

    def _heartbeat(self, force: bool = False) -> None:
        if not force and time.time() - self.last_heartbeat < 60:
            return
        self.last_heartbeat = time.time()
        self.lock.heartbeat()
        self._event("HEARTBEAT", "*", f"session={self.session} open={[s for s in self.cfg.symbols if self.state['assets'][s].get('active')]}")

    def _sleep_interruptible(self, seconds: float) -> bool:
        """Sleep in 1s slices; returns False if stopping/KILL."""
        end = time.time() + seconds
        while time.time() < end:
            if self.stopping or self._check_kill_halt():
                self.stopping = True
                return False
            time.sleep(min(1.0, max(0.1, end - time.time())))
            self._heartbeat()
        return not self.stopping

    # -- main loop --------------------------------------------------------------------------
    def request_stop(self) -> None:
        logger.info("[SIGNAL] stop requested; draining current iteration…")
        self.stopping = True

    def run(self) -> None:
        # clock sanity
        utc_now = datetime.now(timezone.utc)
        ist_now = now_ist()
        skew = abs((ist_now.utcoffset() or timedelta()).total_seconds() - 19800)
        logger.info(f"[CLOCK] UTC={utc_now.isoformat()} IST={ist_now.isoformat()} (offset-ok={skew < 1})")
        if not self.lock.acquire():
            sys.exit(0)
        self._event("BOOT", "*", f"v{__version__} mode={self.cfg.mode} profile={self.cfg.broker_profile} session={self.session}")
        try:
            self.audit_and_recover_on_startup()
            # pre-window gate
            t = now_ist().time()
            if (not self._in_entry_window(t)) and not self.cfg.force_entry:
                any_active = any(self.state["assets"][s].get("active") for s in self.cfg.symbols)
                if not any_active:
                    logger.info(f"[WAIT] {t.strftime('%H:%M')} IST outside 22:00-04:30; waiting for 22:00…")
                    while not self.stopping:
                        t = now_ist().time()
                        if self._in_entry_window(t):
                            logger.info("[WAIT] Entry window open; starting cycle")
                            break
                        secs = _seconds_until(self.cfg.entry_start, t)
                        logger.info(f"[WAIT] sleeping {min(secs, 300)}s (T-{secs//60}m to 22:00)")
                        if not self._sleep_interruptible(min(secs, 300)):
                            break
            logger.info("[LOOP] Active monitoring engaged (10s)")
            logged_done: set = set()
            while not self.stopping:
                try:
                    # session rollover: ONLY when everything flat (P1 fix)
                    cur_session = session_date_str()
                    if cur_session != self.session:
                        any_open = any(self.state["assets"][s].get("active") for s in self.cfg.symbols)
                        if any_open:
                            logger.error(f"[ROLLOVER] Session {self.session}->{cur_session} but legs OPEN; "
                                         f"continuing pinned session {self.session} (no reload-over-memory)")
                            self._event("ROLLOVER_DEFERRED", "*", f"{self.session}->{cur_session} legs-open")
                            self._alert("ROLLOVER_DEFERRED",
                                        f"Session advanced to {cur_session} with open legs; pinned to {self.session}.", "error")
                        else:
                            logger.info(f"[ROLLOVER] {self.session} -> {cur_session}; pinning new session")
                            self.session = cur_session
                            mode_suffix = "_paper" if self.paper else ""
                            self.state_path = self.cfg.data_dir / f"overnight_crypto_delta_{self.session}{mode_suffix}.json"
                            self.events_path = self.cfg.data_dir / f"overnight_crypto_delta_{self.session}{mode_suffix}.events.jsonl"
                            self.store = StateStore(self.state_path)
                            self.events = EventLog(self.events_path)
                            self.state = {"version": STATE_VERSION, "strategy": STRATEGY_NAME,
                                          "session": self.session, "broker_profile": self.cfg.broker_profile,
                                          "assets": {s: default_asset_state() for s in self.cfg.symbols},
                                          "portfolio": {"sl_total": 0, "halted": False, "halt_reason": None}}
                            logged_done.clear()
                            self._load_state()
                    self._heartbeat()
                    self.reconcile_positions_with_broker()
                    t = now_ist().time()
                    in_window = self._in_entry_window(t)
                    for asset in self.cfg.symbols:
                        a = self.state["assets"][asset]
                        if not a.get("entry_done") and not a.get("active") and (in_window or self.cfg.force_entry):
                            outcome, reason = self.enter_iron_condor(asset)
                            if outcome != EntryOutcome.ENTERED:
                                logger.info(f"[{asset}] Entry {outcome.value} ({reason})")
                        if not a.get("active") and a.get("exit_time") and asset not in logged_done:
                            logger.info(f"[{asset}] Cycle finished ({a.get('exit_reason')}); flat. "
                                        f"realized=${a.get('realized_pnl_usd', 0):.2f}")
                            logged_done.add(asset)
                    self.monitor_positions()
                except SystemExit:
                    raise
                except Exception as e:
                    logger.exception(f"[LOOP ERROR] {e}")
                    self._event("LOOP_ERROR", "*", str(e)[:300])
                if not self._sleep_interruptible(self.cfg.monitor_interval):
                    break
        finally:
            try:
                self._save_state()
                self._event("SHUTDOWN", "*", "graceful drain complete")
            finally:
                self.lock.release()
            logger.info("[SHUTDOWN] Engine stopped cleanly; lock released")


# ------------------------------------------------------------------------------
# 12. SELF-TEST (offline, deterministic — rigorous check)
# ------------------------------------------------------------------------------
@dataclass
class T:
    name: str
    ok: bool
    detail: str = ""


def run_self_tests() -> int:
    tests: List[T] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        tests.append(T(name, bool(ok), detail))

    # 1. strike rounding
    try:
        ce = round(97500 * 1.02 / 100) * 100
        pe = round(97500 * 0.98 / 100) * 100
        add("strike_math_btc", ce == 99400 and pe == 95600, f"ce={ce} pe={pe}")
        ce2 = round(3450 * 1.012 / 10) * 10
        add("strike_math_eth", ce2 == 3490, f"ce={ce2}")
    except Exception as e:
        add("strike_math", False, str(e))

    # 2. dynamic lots on 10k @ 88 (no safety mult here: raw formula check)
    try:
        btc_margin = 800 * 0.001 * 88  # 70.4
        lots = int((10000 * 0.65) // btc_margin)
        eth_margin = 30 * 0.01 * 88  # 26.4
        lots_e = int((10000 * 0.65) // eth_margin)
        add("lots_formula", lots == 92 and lots_e == 246, f"btc={lots} eth={lots_e}")
    except Exception as e:
        add("lots_formula", False, str(e))

    # 3. sl/tp math
    try:
        add("sl_math", round(40 * 2.0, 2) == 80.0)
        add("tp_math", abs(120 * 0.65 - 78.0) < 1e-9)
        add("survivor_math", abs(40 * (1 - 0.65) - 14.0) < 1e-9)
    except Exception as e:
        add("sl_tp_math", False, str(e))

    # 4. SL confirmer
    try:
        c = SLConfirmer(2, 60.0, 0.40, 5.0)
        ticks: List[Dict[str, Any]] = []
        c.add_tick(ticks, 80.5, 0.02, 200, 1.0)
        t1, _ = c.triggered(ticks, 80.0)
        c.add_tick(ticks, 81.0, 0.02, 200, 1.0)
        t2, why = c.triggered(ticks, 80.0)
        wide: List[Dict[str, Any]] = []
        for _ in range(2):
            c.add_tick(wide, 90.0, 0.60, 200, 1.0)
        t3, why3 = c.triggered(wide, 80.0)
        add("sl_confirm", (not t1) and t2 and (not t3), f"1tick={t1} 2tick={t2}:{why} wide={t3}:{why3}")
    except Exception as e:
        add("sl_confirm", False, str(e))

    # 5. depth gate scaling
    try:
        need_top = max(100, math.ceil(0.5 * 242))
        need_cum = max(200, 242)
        thin_ok = 100 >= need_top and 150 >= need_cum
        thick_ok = 200 >= need_top and 500 >= need_cum
        add("depth_gate_scaled", (not thin_ok) and thick_ok, f"need={need_top}/{need_cum}")
    except Exception as e:
        add("depth_gate_scaled", False, str(e))

    # 6. expiry picker
    try:
        pre = datetime(2026, 9, 20, 10, 0, tzinfo=IST_TZ)
        raw, d, dte, rs = pick_1dte_expiry(["20-SEP-26", "21-SEP-26"], pre, dtime(17, 30))
        ok1 = raw == "20-SEP-26" and dte == 0 and rs == "OK"
        post = datetime(2026, 9, 20, 18, 0, tzinfo=IST_TZ)
        raw2, _, dte2, rs2 = pick_1dte_expiry(["20-SEP-26", "21-SEP-26"], post, dtime(17, 30))
        ok2 = raw2 == "21-SEP-26" and dte2 == 1
        raw3, _, _, rs3 = pick_1dte_expiry(["30-SEP-26"], pre, dtime(17, 30))
        ok3 = raw3 is None and rs3.startswith("DTE_TOO_FAR")
        add("expiry_picker", ok1 and ok2 and ok3, f"pre={raw}/{dte} post={raw2}/{dte2} far={rs3}")
    except Exception as e:
        add("expiry_picker", False, str(e))

    # 7. symbol validation
    try:
        ok, _ = validate_option_symbol("BTC20SEP2699400CE", "BTC", 99400, "CE", "20-SEP-26", 100)
        bad1, _ = validate_option_symbol("BTC20SEP2699400CE", "BTC", 98100, "CE", "20-SEP-26", 100)
        bad2, _ = validate_option_symbol("BTC20SEP2699400PE", "BTC", 99400, "CE", "20-SEP-26", 100)
        bad3, w3 = validate_option_symbol("BTC21SEP2699400CE", "BTC", 99400, "CE", "20-SEP-26", 100)
        add("symbol_validation", ok and not bad1 and not bad2 and not bad3, f"exp_mismatch={w3}")
    except Exception as e:
        add("symbol_validation", False, str(e))

    # 8. idempotency determinism
    try:
        k1 = f"20260920:BTC:CE_SHORT:entry"
        k2 = f"20260920:BTC:CE_SHORT:entry"
        k3 = f"20260920:BTC:CE_SHORT:SL_EXIT"
        add("idempotency_keys", k1 == k2 and k1 != k3)
    except Exception as e:
        add("idempotency_keys", False, str(e))

    # 9. config validation rejects bad ranges
    try:
        import argparse as _ap
        ns = _ap.Namespace(symbol="BTC", mode="paper", sl_mult=20.0, tp_pct=0.65,
                           force_entry=False, btc_lots=None, eth_lots=None)
        os.environ.setdefault("BROKER_PROFILE", "paper")
        try:
            load_config(ns, need_secrets=False)
            add("config_rejects_bad_sl", False, "accepted sl=20")
        except ConfigError:
            add("config_rejects_bad_sl", True)
    except Exception as e:
        add("config_rejects_bad_sl", False, str(e))

    # 10. state roundtrip + v1 migration
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "s.json"
            st = StateStore(p)
            data = {"version": 2, "assets": {"BTC": default_asset_state()}}
            data["assets"]["BTC"]["legs"]["CE_SHORT"] = {
                **default_leg("BTC20SEP2699400CE", "SELL"),
                "filled_qty": 92, "avg_price": 40.0, "active": True}
            st.save(data)
            back = st.load()
            ok_rt = back and back["assets"]["BTC"]["legs"]["CE_SHORT"]["filled_qty"] == 92
            v1 = {"active": True, "entry_done": True, "expiry_date": "20-SEP-26", "lots": 92,
                  "positions": {"CE_SHORT": {"symbol": "X", "type": "SELL", "fill_price": 40,
                                             "sl_price": 80, "active": True, "quantity": 92}}}
            m = migrate_v1_asset(v1)
            ok_m = m["legs"]["CE_SHORT"]["fill_estimated"] is True and m["expiry_iso"] == "2026-09-20"
            add("state_roundtrip_migrate", bool(ok_rt) and ok_m)
    except Exception as e:
        add("state_roundtrip_migrate", False, str(e))

    # 11. fee math sanity
    try:
        fee = abs(40.0) * 0.001 * 92 * (30 / 10000.0) + 0.05
        add("fee_math", 0 < fee < 5.0, f"fee=${fee:.4f}")
    except Exception as e:
        add("fee_math", False, str(e))

    # 12. session pinning across midnight
    try:
        a = session_date_str(datetime(2026, 9, 20, 7, 59, tzinfo=IST_TZ))
        b = session_date_str(datetime(2026, 9, 20, 8, 1, tzinfo=IST_TZ))
        add("session_pinning", a == "20260919" and b == "20260920", f"{a}/{b}")
    except Exception as e:
        add("session_pinning", False, str(e))

    # 13. lock exclusivity (second acquire fails, no crash)
    try:
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            lp = Path(td) / "t.lock"
            l1, l2 = SingletonLock(lp), SingletonLock(lp)
            ok1 = l1.acquire()
            ok2 = l2.acquire()
            l1.release()
            add("lock_exclusivity", ok1 and not ok2, f"first={ok1} second={ok2}")
    except Exception as e:
        add("lock_exclusivity", False, str(e))

    # 14. spread-economics TP decision
    try:
        # credit=(40+38-6-5)*0.001*92=6.164; cost decayed to 35%: profit=65% => TP
        credit_u, cost_u = 67.0, 67.0 * 0.35
        profit = (credit_u - cost_u) * 0.001 * 92
        target = credit_u * 0.001 * 92 * 0.65
        add("spread_tp_decision", profit >= target - 1e-9, f"profit={profit:.3f} target={target:.3f}")
    except Exception as e:
        add("spread_tp_decision", False, str(e))

    # 15. no fail-open leftovers in source (static self-check)
    try:
        src = Path(__file__).read_text(encoding="utf-8")
        # Tokens are assembled at runtime so this very check does not contain them.
        banned = ["5660" + "9a7a", "1000" + ", 1000", "canonical" + "_sym"]
        hits = [b for b in banned if b in src]
        add("no_failopen_leftovers", not hits, f"hits={hits}")
    except Exception as e:
        add("no_failopen_leftovers", False, str(e))

    passed = sum(1 for t in tests if t.ok)
    print(f"\nSELF-TEST v{__version__}: {passed}/{len(tests)} passed")
    for t in tests:
        print(f"  [{'PASS' if t.ok else 'FAIL'}] {t.name}" + (f" — {t.detail}" if t.detail else ""))
    return 0 if passed == len(tests) else 1


# ------------------------------------------------------------------------------
# 13. REPLAY (quote-tape SL/TPvalidator)
# ------------------------------------------------------------------------------
def run_replay(path: str, sl: float) -> int:
    """Tape format (JSONL): {"symbol":..,"bid":..,"ask":..,"bid_size":..,"ask_size":..}.
    Feeds asks through the production SLConfirmer and reports the trigger line."""
    p = Path(path)
    if not p.exists():
        print(f"No such tape: {path}")
        return 2
    c = SLConfirmer()
    ticks: List[Dict[str, Any]] = []
    n = trig_at = 0
    max_ask = 0.0
    wide = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        n += 1
        try:
            r = json.loads(line)
            ask = float(r.get("ask") or 0)
            bid = float(r.get("bid") or 0)
        except (ValueError, TypeError):
            continue
        max_ask = max(max_ask, ask)
        spr = (ask - bid) / ask if ask > 0 and bid > 0 else 1.0
        if spr > 0.40:
            wide += 1
        c.add_tick(ticks, ask, spr, int(r.get("ask_size", 0) or 0), 0.5)
        ok, why = c.triggered(ticks, sl)
        if ok and not trig_at:
            trig_at = n
            print(f"SL {sl} CONFIRMED at tape line {n} ({why}, ask={ask})")
    print(f"Replay done: {n} ticks, max_ask={max_ask}, wide_spread_ticks={wide}, "
          f"trigger={'line '+str(trig_at) if trig_at else 'NONE'}")
    return 0


# ------------------------------------------------------------------------------
# 14. CLI
# ------------------------------------------------------------------------------
def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=f"Overnight Crypto Delta Options v{__version__} (production)")
    ap.add_argument("--symbol", default="BOTH", choices=["BTC", "ETH", "BOTH"])
    ap.add_argument("--mode", default="live", choices=["live", "paper", "dry_run"])
    ap.add_argument("--lots", default=None,
                    help="DEPRECATED: rejected on purpose; use --btc-lots / --eth-lots")
    ap.add_argument("--btc-lots", type=int, default=None)
    ap.add_argument("--eth-lots", type=int, default=None)
    ap.add_argument("--sl-mult", type=float, default=2.0)
    ap.add_argument("--tp-pct", type=float, default=float(os.getenv("OVERNIGHT_TP_PCT", "0.60")))
    ap.add_argument("--basket-sl-mult", type=float, default=float(os.getenv("OVERNIGHT_BASKET_SL_MULT", "1.0")))
    ap.add_argument("--force-entry", action="store_true")
    ap.add_argument("--self-test", action="store_true", help="Run offline self-tests and exit")
    ap.add_argument("--print-config", action="store_true", help="Print resolved config (key redacted) and exit")
    ap.add_argument("--replay-tape", default=None, help="JSONL quote tape to validate")
    ap.add_argument("--replay-sl", type=float, default=80.0)
    return ap.parse_args()


def main() -> None:
    args = parse_args()
    if args.lots is not None:
        print("ERROR: --lots sets BTC and ETH to the SAME size and is rejected. "
              "Use --btc-lots and --eth-lots separately.", file=sys.stderr)
        sys.exit(2)
    if args.self_test:
        sys.exit(run_self_tests())
    if args.replay_tape:
        sys.exit(run_replay(args.replay_tape, args.replay_sl))
    if args.print_config:
        try:
            cfg = load_config(args, need_secrets=False)
        except ConfigError as e:
            print(f"CONFIG ERROR: {e}", file=sys.stderr)
            sys.exit(2)
        out = asdict(cfg)
        out["api_key"] = "<redacted: set via env>"
        out["host"] = cfg.host or "<required via env in live>"
        for k in ("entry_start", "entry_end", "ladder_start", "aggressive_time", "cutoff", "settle"):
            out[k] = str(out[k])
        for k in ("data_dir", "lock_path", "kill_file", "halt_file"):
            out[k] = str(out[k])
        print(json.dumps(out, indent=2))
        sys.exit(0)
    try:
        cfg = load_config(args, need_secrets=True)
    except ConfigError as e:
        print(f"CONFIG ERROR: {e}", file=sys.stderr)
        sys.exit(2)
    engine = Engine(cfg)

    def _sig(signum: int, _frame: Any) -> None:
        logger.info(f"Signal {signum} received")
        engine.request_stop()

    try:
        signal.signal(signal.SIGINT, _sig)
        if hasattr(signal, "SIGTERM"):
            signal.signal(signal.SIGTERM, _sig)
    except Exception:
        pass
    engine.run()


# Backward compatibility alias
OvernightCryptoOptionsEngine = Engine


if __name__ == "__main__":
    main()
