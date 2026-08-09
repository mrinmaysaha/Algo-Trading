"""
Unit tests for get_contract_multiplier in symbol_utils.py
Tests MCX Gold/GoldM quotation unit multiplier (0.1) vs standard contracts (1.0).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from utils.symbol_utils import get_contract_multiplier


class TestContractMultiplier:
    def test_mcx_gold_and_goldm_options_and_futures(self):
        """Both Mini (GOLDM) and Normal (GOLD) Futures & Options use 0.1 multiplier (price quoted per 10g)."""
        # GOLDM Mini Options & Futures
        assert get_contract_multiplier("GOLDM28AUG26150500PE", "MCX") == 0.1
        assert get_contract_multiplier("GOLDM28AUG26150500CE", "MCX") == 0.1
        assert get_contract_multiplier("GOLDM04SEP26FUT", "MCX") == 0.1
        assert get_contract_multiplier("GOLDM05OCT26FUT", "MCX") == 0.1

        # GOLD Normal Options & Futures
        assert get_contract_multiplier("GOLD28AUG26150500PE", "MCX") == 0.1
        assert get_contract_multiplier("GOLD28AUG26150500CE", "MCX") == 0.1
        assert get_contract_multiplier("GOLD05AUG26FUT", "MCX") == 0.1
        assert get_contract_multiplier("GOLD05OCT26FUT", "MCX") == 0.1


    def test_mcx_goldguinea_and_goldpetal_return_one(self):
        """GOLDGUINEA (8g) and GOLDPETAL (1g) are quoted per unit, so multiplier is 1.0."""
        assert get_contract_multiplier("GOLDGUINEA31AUG26FUT", "MCX") == 1.0
        assert get_contract_multiplier("GOLDPETAL31AUG26FUT", "MCX") == 1.0

    def test_mcx_silver_and_crude_return_one(self):
        """SILVER and CRUDEOIL contracts return 1.0."""
        assert get_contract_multiplier("SILVER30NOV26FUT", "MCX") == 1.0
        assert get_contract_multiplier("CRUDEOIL19AUG26FUT", "MCX") == 1.0

    def test_nfo_equity_returns_one(self):
        """NSE and NFO contracts return 1.0."""
        assert get_contract_multiplier("NIFTY28MAR2420800CE", "NFO") == 1.0
        assert get_contract_multiplier("RELIANCE", "NSE") == 1.0
