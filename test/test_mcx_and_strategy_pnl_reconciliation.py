import os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
from database.symbol import SymToken, db_session, engine, init_db
from services.option_symbol_service import get_available_strikes, find_option_in_database, clear_strikes_cache

def _seed_test_tokens():
    init_db()
    db_session.remove()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM symtoken WHERE symbol LIKE 'SILVER%28AUG26%'"))
        conn.execute(text(
            "INSERT INTO symtoken (symbol, brsymbol, name, exchange, brexchange, token, expiry, strike, lotsize, instrumenttype, tick_size) "
            "VALUES ('SILVERM28AUG26240000PE', 'SILVERM28AUG26240000PE', 'SILVERM', 'MCX', 'MCX', '900001', '28-AUG-26', 240000.0, 1, 'PE', 1.0)"
        ))
        conn.execute(text(
            "INSERT INTO symtoken (symbol, brsymbol, name, exchange, brexchange, token, expiry, strike, lotsize, instrumenttype, tick_size) "
            "VALUES ('SILVERM28AUG26242000PE', 'SILVERM28AUG26242000PE', 'SILVERM', 'MCX', 'MCX', '900002', '28-AUG-2026', 242000.0, 1, 'OPTFUT', 1.0)"
        ))
        conn.execute(text(
            "INSERT INTO symtoken (symbol, brsymbol, name, exchange, brexchange, token, expiry, strike, lotsize, instrumenttype, tick_size) "
            "VALUES ('SILVER28AUG26245000CE', 'SILVER28AUG26245000CE', 'SILVER', 'MCX', 'MCX', '900003', '28-AUG-26', 245000.0, 1, 'CE', 1.0)"
        ))


def _teardown_test_tokens():
    try:
        from database.strategy_book_db import db_session as strat_session
        strat_session.remove()
    except Exception:
        pass
    try:
        from database.sandbox_db import db_session as sandbox_session
        sandbox_session.remove()
    except Exception:
        pass
    db_session.remove()
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM symtoken WHERE symbol LIKE 'SILVER%28AUG26%'"))
    db_session.remove()


@pytest.fixture(scope="module", autouse=True)
def setup_test_data():
    _seed_test_tokens()
    yield
    _teardown_test_tokens()


def test_mcx_silverm_available_strikes():
    clear_strikes_cache()
    # Query SILVERM PE with 28AUG26
    strikes = get_available_strikes("SILVERM", "28AUG26", "PE", "MCX")
    assert 240000.0 in strikes
    assert 242000.0 in strikes  # Discovered even with OPTFUT and 4-digit year in expiry column


def test_mcx_silver_no_macro_variant_fallback():
    clear_strikes_cache()
    # Query SILVERM CE when contracts are listed under SILVER - must return empty (no fallback to big SILVER)
    strikes = get_available_strikes("SILVERM", "28AUG26", "CE", "MCX")
    assert strikes == []


def test_mcx_find_option_in_database_strict_mini():
    # Exact match
    res1 = find_option_in_database("SILVERM28AUG26240000PE", "MCX")
    assert res1 is not None
    assert res1["strike"] == 240000.0

    # No fallback: SILVERM requested but only big SILVER exists -> must return None
    res2 = find_option_in_database("SILVERM28AUG26245000CE", "MCX")
    assert res2 is None


def test_strategy_pnl_reconciles_tradebook(monkeypatch):
    from services.strategy_pnl_service import get_multi_timeframe_strategy_analytics

    # Mock tradebook response with the 20 real session trades
    mock_trades = [
        {"symbol": "FINNIFTY25AUG2626200CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 120, "price": 24.00},
        {"symbol": "NIFTY25AUG2624200CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 130, "price": 69.60},
        {"symbol": "FINNIFTY25AUG2626200CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 120, "price": 32.40},
        {"symbol": "NIFTY25AUG2624200CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 130, "price": 32.00},
        {"symbol": "MIDCPNIFTY25AUG2614925CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 240, "price": 21.75},
        {"symbol": "MIDCPNIFTY25AUG2614925CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 240, "price": 19.30},
        {"symbol": "BANKNIFTY25AUG2657300PE", "strategy": "Prime Indicator Scalper Options", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 60, "price": 35.05},
        {"symbol": "MIDCPNIFTY25AUG2614900CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 240, "price": 51.40},
        {"symbol": "MIDCPNIFTY25AUG2614900CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 240, "price": 24.20},
        {"symbol": "BANKNIFTY25AUG2657300PE", "strategy": "Prime Indicator Scalper Options", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 60, "price": 81.00},
        {"symbol": "FINNIFTY25AUG2626100PE", "strategy": "SMC_FVG_ZeroLag_Options", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 120, "price": 25.50},
        {"symbol": "NIFTY25AUG2624200CE", "strategy": "SMC_FVG_ZeroLag_Options", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 130, "price": 23.15},
        {"symbol": "FINNIFTY25AUG2626100PE", "strategy": "SMC_FVG_ZeroLag_Options", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 120, "price": 42.85},
        {"symbol": "BANKNIFTY25AUG2657600CE", "strategy": "SMC_FVG_ZeroLag_Options", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 60, "price": 89.55},
        {"symbol": "BANKNIFTY25AUG2657600CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 60, "price": 89.55},
        {"symbol": "BANKNIFTY25AUG2657600CE", "strategy": "Post10_Institutional_OB_VWAP_Production_V4", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 60, "price": 141.15},
        {"symbol": "BANKNIFTY25AUG2657600CE", "strategy": "SMC_FVG_ZeroLag_Options", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 60, "price": 131.10},
        {"symbol": "NIFTY25AUG2624200CE", "strategy": "SMC_FVG_ZeroLag_Options", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 130, "price": 38.80},
        {"symbol": "BANKNIFTY25AUG2657400CE", "strategy": "3Min_ORB_2Lot_Quant_V2", "exchange": "NFO", "product": "MIS", "action": "SELL", "qty": 60, "price": 209.20},
        {"symbol": "BANKNIFTY25AUG2657400CE", "strategy": "3Min_ORB_2Lot_Quant_V2", "exchange": "NFO", "product": "MIS", "action": "BUY", "qty": 60, "price": 134.50},
    ]

    import services.tradebook_service
    monkeypatch.setattr(services.tradebook_service, "get_tradebook", lambda *args, **kwargs: (True, {"status": "success", "data": mock_trades}, 200))

    analytics = get_multi_timeframe_strategy_analytics(timeframe="1D")
    assert analytics["status"] == "success"

    summary = analytics["portfolio_summary"]
    assert summary["gross_profit"] == 3015.50
    assert summary["net_realized_profit"] == 2410.53
    assert summary["total_deductions"] == 604.97
    assert summary["total_trades"] == 10
    assert summary["winning_strategies_count"] == 2
    assert summary["losing_strategies_count"] == 2

    strats = analytics["strategies"]
    assert strats["Post10_Institutional_OB_VWAP"]["gross_pnl"] == 7900.00
    assert strats["Post10_Institutional_OB_VWAP"]["total_trades"] == 5

    assert strats["3Min_ORB_Quant"]["gross_pnl"] == 4482.00
    assert strats["3Min_ORB_Quant"]["total_trades"] == 1

    assert strats["Prime Indicator Scalper Options"]["gross_pnl"] == -2757.00
    assert strats["Prime Indicator Scalper Options"]["total_trades"] == 1

    assert strats["SMC_FVG_ZeroLag_Options"]["gross_pnl"] == -6609.50
    assert strats["SMC_FVG_ZeroLag_Options"]["total_trades"] == 3


if __name__ == "__main__":
    _seed_test_tokens()
    try:
        print("Running test_mcx_silverm_available_strikes...")
        test_mcx_silverm_available_strikes()
        print("PASSED test_mcx_silverm_available_strikes")

        print("Running test_mcx_silver_variant_discovery...")
        test_mcx_silver_variant_discovery()
        print("PASSED test_mcx_silver_variant_discovery")

        print("Running test_mcx_find_option_in_database_variant...")
        test_mcx_find_option_in_database_variant()
        print("PASSED test_mcx_find_option_in_database_variant")

        class MockMonkeypatch:
            def setattr(self, target, name, value):
                setattr(target, name, value)

        print("Running test_strategy_pnl_reconciles_tradebook...")
        test_strategy_pnl_reconciles_tradebook(MockMonkeypatch())
        print("PASSED test_strategy_pnl_reconciles_tradebook")
        print("\nALL 4 TESTS COMPLETED SUCCESSFULLY!")
    finally:
        _teardown_test_tokens()
