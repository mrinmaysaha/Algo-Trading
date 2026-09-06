"""
================================================================================
PORTFOLIO-LEVEL CIRCUIT BREAKER SUPERVISOR
================================================================================
Manages two independent daily loss caps:

  NSE session  09:15 - 15:30 IST  ->  Rs.8,000 realized loss cap
               Strategies: Post10_Institutional_OB_VWAP_Production_V4,
                           SMC_FVG_ZeroLag_Options,
                           3Min_ORB_2Lot_Quant_V2,
                           NSE_LiquiditySweepScalper_V43,
                           Prime_Indicator_Scalper_Options

  MCX session  16:00 - 23:30 IST  ->  Rs.7,000 realized loss cap
               Strategies: MCX_Institutional_MIS_V3.0,
                           MCX_GOLDM_FVG_Options_Scalper

Usage in each strategy (3 lines):
  from strategies.portfolio_supervisor import report_session_loss, is_session_halted

  # Before any new entry order:
  halted, reason = is_session_halted("nse")   # or "mcx"
  if halted:
      logger.warning("[PORTFOLIO CB] %s", reason)
      return

  # After every realized trade exit:
  report_session_loss("nse", strategy_name, realized_pnl)
================================================================================
"""
import datetime
import time
import json
import logging
import os
import sys
from typing import Dict, Tuple, Optional

logger = logging.getLogger("portfolio_supervisor")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [portfolio_supervisor] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(_h)
    logger.setLevel(logging.INFO)

# State file lives next to this module
_STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio_cb_state.json")

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

SESSIONS: Dict[str, Dict] = {
    "nse": {
        "label": "NSE",
        "start": datetime.time(9, 15),
        "end": datetime.time(15, 30),
        "loss_cap_inr": 8000.0,
        "strategies": {
            "Post10_Institutional_OB_VWAP",
            "Post10_Institutional_OB_VWAP_Production",
            "Post10_Institutional_OB_VWAP_Production_V4",
            "Post10_Institutional_OB_VWAP_Production_V5",
            "SMC_FVG_ZeroLag_Options",
            "3Min_ORB_2Lot_Quant_V2",
            "3Min_ORB_Quant",
            "NSE_LiquiditySweepScalper_V43",
            "Liquid_sweep_options",
            "Prime_Indicator_Scalper_Options",
            "Prime Indicator Scalper Options",
            "Index_Options_StepTrailing_Quant",
            "Multi-Index Step-Trailing Options Quant",
        },
    },
    "bse": {
        "label": "BSE",
        "start": datetime.time(9, 15),
        "end": datetime.time(15, 30),
        "loss_cap_inr": 5000.0,
        "strategies": {
            "Sensex_3Min_ORB_Quant",
            "Sensex_5Min_ORB_Quant",
            "Bankex_5Min_ORB_Quant",
        },
    },
    "mcx": {
        "label": "MCX",
        "start": datetime.time(16, 0),
        "end": datetime.time(23, 30),
        "loss_cap_inr": 5000.0,
        "strategies": {
            "MCX_Institutional_MIS_V3.0",
            "MCX_GOLDM_FVG_Options_Scalper",
        },
    },
}


def _ist_now() -> datetime.datetime:
    return datetime.datetime.now(IST)


def _today_str() -> str:
    return _ist_now().date().isoformat()


def _empty_state() -> dict:
    return {
        "date": _today_str(),
        "nse": {"session_pnl": 0.0, "halted": False, "halt_reason": "", "strategies": {}},
        "bse": {"session_pnl": 0.0, "halted": False, "halt_reason": "", "strategies": {}},
        "mcx": {"session_pnl": 0.0, "halted": False, "halt_reason": "", "strategies": {}},
        "active_positions": {},
    }


def get_underlying_root(symbol: str) -> str:
    """Normalize any derivative or spot symbol to its underlying root."""
    if not symbol:
        return ""
    s = str(symbol).upper().strip()
    for root in [
        "BANKNIFTY", "MIDCPNIFTY", "FINNIFTY", "NIFTY",
        "SENSEX", "BANKEX",
        "CRUDEOILM", "NATGASMINI", "NATURALGAS", "GOLDM", "SILVERMIC", "SILVERM", "CRUDEOIL", "GOLD", "SILVER"
    ]:
        if s.startswith(root):
            return root
    return s


def _get_live_db_active_positions(root: str) -> int:
    """Check sandbox_positions in sandbox.db for open positions with quantity != 0."""
    db_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "db", "sandbox.db"))
    if not os.path.exists(db_path):
        return 0
    try:
        import sqlite3
        conn = sqlite3.connect(db_path, timeout=1.0)
        cur = conn.cursor()
        rows = cur.execute("SELECT symbol FROM sandbox_positions WHERE quantity != 0").fetchall()
        conn.close()
        return sum(1 for (sym,) in rows if get_underlying_root(sym) == root)
    except Exception:
        return 0


def register_symbol_position(strategy_name: str, symbol: str) -> None:
    """Register an active trade position for an underlying symbol."""
    root = get_underlying_root(symbol)
    if not root:
        return
    state = _load_state()
    active = state.setdefault("active_positions", {})
    root_map = active.setdefault(root, {})
    root_map[strategy_name] = {
        "symbol": symbol,
        "registered_at": time.time()
    }
    _save_state(state)
    logger.info("[PORTFOLIO SUPERVISOR] Registered active position on %s for %s (%s). Total active: %d",
                root, strategy_name, symbol, len(root_map))


def _normalize_name(name: str) -> str:
    return (name or "").lower().replace("_", "").replace(" ", "").replace("-", "")


def _names_match(a: str, b: str) -> bool:
    if not a or not b:
        return False
    na, nb = _normalize_name(a), _normalize_name(b)
    return na == nb or na in nb or nb in na


def deregister_symbol_position(strategy_name: str, symbol: str = None) -> None:
    """Deregister an active trade position when exited."""
    state = _load_state()
    active = state.setdefault("active_positions", {})
    modified = False
    if symbol:
        root = get_underlying_root(symbol)
        if root in active:
            for k in list(active[root].keys()):
                if _names_match(k, strategy_name):
                    del active[root][k]
                    modified = True
                    logger.info("[PORTFOLIO SUPERVISOR] Deregistered position on %s for %s. Remaining active: %d",
                                root, strategy_name, len(active[root]))
    else:
        for r, strat_map in list(active.items()):
            for k in list(strat_map.keys()):
                if _names_match(k, strategy_name):
                    del strat_map[k]
                    modified = True
                    logger.info("[PORTFOLIO SUPERVISOR] Deregistered position on %s for %s. Remaining active: %d",
                                r, strategy_name, len(strat_map))
    if modified:
        _save_state(state)


def _load_state() -> dict:
    try:
        if os.path.exists(_STATE_FILE):
            with open(_STATE_FILE, "r", encoding="utf-8") as f:
                state = json.load(f)
            if state.get("date") == _today_str():
                return state
    except (json.JSONDecodeError, OSError):
        pass
    return _empty_state()


def _save_state(state: dict) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
    except OSError as exc:
        logger.error("[CB STATE WRITE ERROR] %s", exc)


def report_session_loss(session: str, strategy_name: str, realized_pnl: float, symbol: str = None) -> None:
    """Report realized PnL after any trade exit. Triggers halt if cap exceeded and clears active position."""
    deregister_symbol_position(strategy_name, symbol)
    session = session.lower()
    if session not in SESSIONS:
        return
    cfg = SESSIONS[session]
    state = _load_state()
    sess = state[session]
    strats = sess.setdefault("strategies", {})
    strats[strategy_name] = strats.get(strategy_name, 0.0) + realized_pnl
    sess["session_pnl"] = sum(strats.values())
    if not sess["halted"] and sess["session_pnl"] <= -cfg["loss_cap_inr"]:
        sess["halted"] = True
        sess["halt_reason"] = (
            f"{cfg['label']} portfolio loss cap Rs.{cfg['loss_cap_inr']:,.0f} breached "
            f"(session PnL: Rs.{sess['session_pnl']:,.2f}). All {cfg['label']} entries halted."
        )
        logger.warning(
            "PORTFOLIO CIRCUIT BREAKER TRIGGERED — %s session halted. Loss: Rs.%.2f / Cap: Rs.%.2f",
            cfg["label"], -sess["session_pnl"], cfg["loss_cap_inr"]
        )
    _save_state(state)
    logger.info(
        "[CB] %s | %s | Trade PnL: Rs.%.2f | Session: Rs.%.2f | Halted: %s",
        cfg["label"], strategy_name, realized_pnl, sess["session_pnl"], sess["halted"]
    )


def is_session_halted(session: str, symbol: str = None, strategy_name: str = None, max_symbol_active: Optional[int] = None) -> Tuple[bool, str]:
    """
    Returns (True, reason) if:
      1. Session realized loss cap has been breached.
      2. Max concurrent positions on the underlying symbol (default 1: Mutual Exclusion) is already reached.
    """
    session = session.lower()
    if session not in SESSIONS:
        return False, ""
    cfg = SESSIONS[session]
    state = _load_state()
    sess = state.get(session, {})
    if sess.get("halted"):
        return True, sess.get("halt_reason", f"{cfg['label']} circuit breaker active.")

    if symbol:
        if max_symbol_active is None:
            max_symbol_active = int(os.getenv("MAX_CONCURRENT_SYMBOL_POSITIONS", "1"))

        root = get_underlying_root(symbol)
        active_map = state.get("active_positions", {}).get(root, {})
        db_count = _get_live_db_active_positions(root)

        # Prune only stale entries (> 120s old without DB confirmation)
        now = time.time()
        stale_keys = []
        for s_key, s_val in active_map.items():
            reg_time = s_val.get("registered_at", 0) if isinstance(s_val, dict) else 0
            if reg_time > 0 and (now - reg_time) > 120 and db_count == 0:
                stale_keys.append(s_key)
        for s_key in stale_keys:
            del active_map[s_key]
        if stale_keys:
            _save_state(state)

        other_strats = [s for s in active_map.keys() if not _names_match(s, strategy_name)]
        state_count = len(other_strats)
        active_count = max(state_count, db_count)
        if active_count >= max_symbol_active:
            active_info = f"({', '.join(other_strats)})" if other_strats else ""
            msg = f"Mutual exclusion symbol lock active: {active_count} active position on {root} {active_info}. Entry blocked to prevent multi-strategy risk stacking."
            logger.warning("[PORTFOLIO SYMBOL LOCK] %s", msg)
            return True, msg

    return False, ""



def is_symbol_locked(symbol: str, strategy_name: str = None) -> Tuple[bool, str]:
    """Returns True if the underlying symbol is currently locked by another active strategy."""
    return is_session_halted("nse", symbol=symbol, strategy_name=strategy_name, max_symbol_active=1)



def get_session_status(session: str) -> dict:
    """Returns diagnostic dict for the session."""
    session = session.lower()
    cfg = SESSIONS.get(session, {})
    state = _load_state()
    sess = state.get(session, {})
    pnl = sess.get("session_pnl", 0.0)
    cap = cfg.get("loss_cap_inr", 0.0)
    return {
        "session": session,
        "session_pnl": pnl,
        "loss_cap": cap,
        "remaining_buffer": cap + pnl,
        "halted": sess.get("halted", False),
        "halt_reason": sess.get("halt_reason", ""),
        "strategies": sess.get("strategies", {}),
    }


def reset_daily_state() -> None:
    _save_state(_empty_state())
    logger.info("[CB RESET] State reset for %s.", _today_str())
