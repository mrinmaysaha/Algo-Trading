import os
import sys
import json
import pytest
import datetime

# Ensure project root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.portfolio_supervisor import (
    report_session_loss,
    is_session_halted,
    get_session_status,
    reset_daily_state,
    _load_state,
    _save_state,
    _empty_state,
    _STATE_FILE,
    SESSIONS,
)

@pytest.fixture(autouse=True)
def clean_cb_state():
    """Ensure every test starts with a clean circuit breaker state."""
    reset_daily_state()
    yield
    reset_daily_state()


def test_initial_state_clean():
    status_nse = get_session_status("nse")
    status_mcx = get_session_status("mcx")

    assert status_nse["session_pnl"] == 0.0
    assert status_nse["loss_cap"] == 8000.0
    assert status_nse["remaining_buffer"] == 8000.0
    assert status_nse["halted"] is False

    assert status_mcx["session_pnl"] == 0.0
    assert status_mcx["loss_cap"] == 7000.0
    assert status_mcx["remaining_buffer"] == 7000.0
    assert status_mcx["halted"] is False


def test_nse_loss_accumulation_and_halt():
    # Strategy 1 (Post10) loses 3,000
    report_session_loss("nse", "Post10_Institutional_OB_VWAP_Production_V4", -3000.0)
    status = get_session_status("nse")
    assert status["session_pnl"] == -3000.0
    assert status["remaining_buffer"] == 5000.0
    assert status["halted"] is False

    # Strategy 2 (SMC FVG) loses 3,000
    report_session_loss("nse", "SMC_FVG_ZeroLag_Options", -3000.0)
    status = get_session_status("nse")
    assert status["session_pnl"] == -6000.0
    assert status["remaining_buffer"] == 2000.0
    assert status["halted"] is False

    # Strategy 3 (3Min ORB) loses 2,500 -> Total -8,500 <= -8,000 cap -> HALT
    report_session_loss("nse", "3Min_ORB_2Lot_Quant_V2", -2500.0)
    status = get_session_status("nse")
    assert status["session_pnl"] == -8500.0
    assert status["halted"] is True
    assert "breached" in status["halt_reason"].lower() or "halt" in status["halt_reason"].lower()

    # MCX session should be completely unaffected
    status_mcx = get_session_status("mcx")
    assert status_mcx["session_pnl"] == 0.0
    assert status_mcx["halted"] is False


def test_mcx_loss_accumulation_and_halt():
    # MCX strategy loses 4,000
    report_session_loss("mcx", "MCX_Institutional_MIS_V3.0", -4000.0)
    status = get_session_status("mcx")
    assert status["session_pnl"] == -4000.0
    assert status["remaining_buffer"] == 3000.0
    assert status["halted"] is False

    # MCX GOLDM strategy loses 3,500 -> Total -7,500 <= -7,000 cap -> HALT
    report_session_loss("mcx", "MCX_GOLDM_FVG_Options_Scalper", -3500.0)
    status = get_session_status("mcx")
    assert status["session_pnl"] == -7500.0
    assert status["halted"] is True
    assert "breached" in status["halt_reason"].lower() or "halt" in status["halt_reason"].lower()

    # NSE session should remain completely unaffected
    status_nse = get_session_status("nse")
    assert status_nse["session_pnl"] == 0.0
    assert status_nse["halted"] is False


def test_profit_offsets_losses():
    # Loss of 4,000
    report_session_loss("nse", "Post10_Institutional_OB_VWAP_Production_V4", -4000.0)
    # Profit of 2,000 on SMC
    report_session_loss("nse", "SMC_FVG_ZeroLag_Options", 2000.0)
    
    status = get_session_status("nse")
    assert status["session_pnl"] == -2000.0
    assert status["remaining_buffer"] == 6000.0
    assert status["halted"] is False


def test_date_reset_clears_halt():
    report_session_loss("nse", "Post10_Institutional_OB_VWAP_Production_V4", -10000.0)
    assert get_session_status("nse")["halted"] is True

    # Simulate date change by writing yesterday's date into the state file
    raw_state = _load_state()
    raw_state["date"] = "2020-01-01"
    _save_state(raw_state)

    # Next read should automatically reset to fresh state
    fresh_state = _load_state()
    assert fresh_state["nse"]["session_pnl"] == 0.0
    assert fresh_state["nse"]["halted"] is False
