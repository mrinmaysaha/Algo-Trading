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
import json
import logging
import os
import sys
from typing import Dict, Tuple

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
            "Post10_Institutional_OB_VWAP_Production_V4",
            "SMC_FVG_ZeroLag_Options",
            "3Min_ORB_2Lot_Quant_V2",
            "NSE_LiquiditySweepScalper_V43",
            "Prime_Indicator_Scalper_Options",
            "Index_Options_StepTrailing_Quant",
        },
    },
    "mcx": {
        "label": "MCX",
        "start": datetime.time(16, 0),
        "end": datetime.time(23, 30),
        "loss_cap_inr": 7000.0,
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
        "mcx": {"session_pnl": 0.0, "halted": False, "halt_reason": "", "strategies": {}},
    }


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


def report_session_loss(session: str, strategy_name: str, realized_pnl: float) -> None:
    """Report realized PnL after any trade exit. Triggers halt if cap exceeded."""
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


def is_session_halted(session: str) -> Tuple[bool, str]:
    """Returns (True, reason) if circuit breaker is active for this session."""
    session = session.lower()
    if session not in SESSIONS:
        return False, ""
    cfg = SESSIONS[session]
    state = _load_state()
    sess = state[session]
    if sess.get("halted"):
        return True, sess.get("halt_reason", f"{cfg['label']} circuit breaker active.")
    return False, ""


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
