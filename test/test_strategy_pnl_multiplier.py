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

def test_resolve_active_strategy():
    init_strategy_book_db()
    # Test strategy resolution runs without exception
    res = resolve_active_strategy_for_symbol(None, "BANKNIFTY25AUG2657500CE", "NFO", "MIS")
    assert res is None or isinstance(res, str)
