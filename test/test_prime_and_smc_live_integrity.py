import sys
import os
import datetime
import pytest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.scripts.Prime_Indicator_Scalper_Options import (
    IndexSpec as PrimeIndexSpec, EngineConfig as PrimeConfig,
    PrimeIndicatorEngine, Position as PrimePosition, TradeSide as PrimeSide, IST
)
from strategies.scripts.SMC_FVG_ZeroLag_Options_20260817232106 import (
    IndexSpec as SMCIndexSpec, EngineConfig as SMCConfig,
    NSOptionsSMCEngine, Position as SMCPosition, TradeSide as SMCSide
)

def test_prime_indicator_scalper_live_integrity():
    cfg = PrimeConfig.from_environment(resolve_network=False)
    assert "BANKNIFTY" in cfg.indices
    assert "NIFTY" in cfg.indices
    assert "FINNIFTY" in cfg.indices
    assert cfg.indices["BANKNIFTY"].sl_atr_mult == 1.5
    assert cfg.indices["NIFTY"].sl_atr_mult == 1.0
    assert cfg.indices["FINNIFTY"].tp_atr_mult == 5.0
    
    # Check position serialization
    now_dt = datetime.datetime.now(IST)
    pos = PrimePosition(
        symbol="BANKNIFTY", traded_option_symbol="BANKNIFTY28AUG202651300CE",
        side=PrimeSide.LONG_CALL, option_type="CE",
        entry_option_price=300.0, entry_spot_price=51300.0,
        quantity=60, lots=2, spot_stop_loss=51150.0, initial_spot_sl=51150.0,
        spot_tp1=51500.0, spot_tp2=51800.0, spot_tp3=52000.0,
        entry_time=now_dt, spot_atr=100.0
    )
    p_dict = pos.to_dict()
    recovered = PrimePosition.from_dict(p_dict)
    assert recovered.symbol == "BANKNIFTY"
    assert recovered.spot_stop_loss == 51150.0

def test_smc_fvg_zerolag_live_integrity():
    cfg = SMCConfig.from_environment()
    assert "BANKNIFTY" in cfg.indices
    assert "NIFTY" in cfg.indices
    assert "FINNIFTY" in cfg.indices
    assert cfg.indices["BANKNIFTY"].require_mtf is True
    assert cfg.indices["NIFTY"].require_mtf is False
    assert cfg.indices["BANKNIFTY"].sl_atr_mult == 0.8
    assert cfg.indices["NIFTY"].sl_atr_mult == 1.5
    
    now_dt = datetime.datetime.now(IST)
    pos = SMCPosition(
        symbol="NIFTY", traded_option_symbol="NIFTY28AUG202624350PE",
        side=SMCSide.LONG_PUT, option_type="PE",
        entry_option_price=120.0, entry_spot_price=24350.0,
        quantity=130, lots=2, spot_stop_loss=24450.0, initial_spot_sl=24450.0,
        spot_target=24150.0, entry_time=now_dt, spot_atr=70.0
    )
    p_dict = pos.to_dict()
    recovered = SMCPosition.from_dict(p_dict)
    assert recovered.symbol == "NIFTY"
    assert recovered.spot_target == 24150.0
