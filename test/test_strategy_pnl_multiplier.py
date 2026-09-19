import os, sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.symbol_utils import get_contract_multiplier
from database.strategy_book_db import (
    init_strategy_book_db,
    resolve_active_strategy_for_symbol,
)

def test_multiplier_mcx():
    assert get_contract_multiplier("GOLDM28AUG26163000CE", "MCX") == 0.1
    assert get_contract_multiplier("SILVERM24AUG26249000CE", "MCX") == 1.0
    assert get_contract_multiplier("NIFTY25AUG2624200CE", "NFO") == 1.0

def test_multiplier_crypto():
    assert get_contract_multiplier("C-BTC-65000-260918", "DELTA") == 0.001
    assert get_contract_multiplier("P-ETH-2400-260918", "DELTA") == 0.01
    assert get_contract_multiplier("C-SOL-140-260918", "DELTA") == 0.1

def test_crypto_accounting_engine():
    from services.accounting_engine import CryptoAccountingEngine, get_accounting_engine

    assert get_accounting_engine("DELTA") == CryptoAccountingEngine
    assert get_accounting_engine("CRYPTO") == CryptoAccountingEngine

    # Short trade: sold at $53, closed at $17, 100 contracts of BTC (mult 0.001)
    res_closed = CryptoAccountingEngine.calculate_closed_trade_pnl(
        entry_price=53.0,
        exit_price=17.0,
        qty=100,
        direction="SELL",
        contract_multiplier=0.001
    )
    # Gross: (53 - 17) * 100 * 0.001 = $3.60
    assert abs(res_closed["gross_pnl"] - 3.60) < 1e-4
    # Turnover: (53 + 17) * 100 * 0.001 = $7.00
    # Fee: 7.00 * 0.0003 = 0.0021
    assert abs(res_closed["exchange_charges"] - 0.0021) < 1e-4
    # GST: 18% on exchange fee = 0.0021 * 0.18 = 0.000378 -> 0.0004
    assert abs(res_closed["gst"] - 0.0004) < 1e-4
    assert abs(res_closed["net_pnl"] - 3.5975) < 1e-4
    # No Indian domestic securities taxes
    assert res_closed["stt"] == 0.0
    assert res_closed["brokerage"] == 0.0

    # Long position MTM: bought at $9, current LTP $13, 100 contracts of BTC (mult 0.001)
    res_mtm = CryptoAccountingEngine.calculate_open_position_mtm(
        entry_price=9.0,
        current_ltp=13.0,
        qty=100,
        direction="BUY",
        contract_multiplier=0.001
    )
    # Gross: (13 - 9) * 100 * 0.001 = $0.40
    assert abs(res_mtm["gross_mtm"] - 0.40) < 1e-4
    # Turnover: (9 + 13) * 100 * 0.001 = $2.20
    # Fee: 2.20 * 0.0003 = 0.00066 -> rounded to 0.0007; GST = 0.0001; Total = 0.0008
    assert abs(res_mtm["accrued_and_exit_charges"] - 0.0008) < 1e-4
    assert abs(res_mtm["net_mtm"] - (0.40 - 0.0008)) < 1e-4

def test_resolve_active_strategy():
    init_strategy_book_db()
    # Test strategy resolution runs without exception
    res = resolve_active_strategy_for_symbol(None, "BANKNIFTY25AUG2657500CE", "NFO", "MIS")
    assert res is None or isinstance(res, str)
