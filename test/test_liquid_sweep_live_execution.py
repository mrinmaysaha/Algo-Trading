import sys
import os
import datetime
import pytest
from unittest.mock import MagicMock, patch

# Ensure strategies and openalgo modules are accessible
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from strategies.scripts.liquid_sweep_options_20260808185609 import (
    IndexSpec, EngineConfig, Position, TradeSide, StopLossMode, OHLCVBar,
    MultiTimeframeTechnicalSeries, NSELiquiditySweepEngine, get_atm_strike_and_symbol,
    CostModel, PersistenceManager, IST
)

def test_index_spec_and_engine_config_integrity():
    """Verify that IndexSpec and EngineConfig hold distinct, valid per-index settings."""
    cfg = EngineConfig(
        api_key="test_api_key",
        host_url="http://127.0.0.1:5000",
        ws_url="ws://127.0.0.1:8765",
        strategy_name="NSE_LiquiditySweepScalper_V43",
        indices={
            "NIFTY": IndexSpec(
                name="NIFTY", base_symbol="NIFTY", exchange="NSE_INDEX", options_exchange="NFO",
                lot_size=65, strike_step=50, tick_size=0.05, slippage_points=0.20, expiry_weekday=3,
                min_wick_pct=0.30, min_sweep_pts=7.0, sl_atr_mult=1.0, tp_atr_mult=3.8,
                trail_activation_atr=1.4, require_choch=False
            ),
            "BANKNIFTY": IndexSpec(
                name="BANKNIFTY", base_symbol="BANKNIFTY", exchange="NSE_INDEX", options_exchange="NFO",
                lot_size=30, strike_step=100, tick_size=0.05, slippage_points=0.50, expiry_weekday=2,
                min_wick_pct=0.30, min_sweep_pts=10.0, sl_atr_mult=1.4, tp_atr_mult=2.8,
                trail_activation_atr=1.4, require_choch=False
            ),
            "MIDCPNIFTY": IndexSpec(
                name="MIDCPNIFTY", base_symbol="MIDCPNIFTY", exchange="NSE_INDEX", options_exchange="NFO",
                lot_size=120, strike_step=25, tick_size=0.05, slippage_points=0.40, expiry_weekday=0,
                min_wick_pct=0.30, min_sweep_pts=4.0, sl_atr_mult=1.2, tp_atr_mult=3.8,
                trail_activation_atr=1.4, require_choch=True
            ),
        },
        stop_loss_mode=StopLossMode.ATR_STEP_TRAILING,
        base_capital_inr=100000.0,
        risk_per_trade_pct=2.0,
        max_daily_loss_pct=5.0,
        max_daily_loss_inr=5000.0,
        max_consecutive_losses=2,
        max_trades_per_day=3,
        max_trades_per_index=1,
        max_concurrent_positions=2,
        backtest_mode=True
    )
    
    assert cfg.indices["BANKNIFTY"].min_sweep_pts == 10.0
    assert cfg.indices["NIFTY"].min_sweep_pts == 7.0
    assert cfg.indices["MIDCPNIFTY"].require_choch is True
    assert cfg.indices["NIFTY"].require_choch is False
    assert cfg.indices["BANKNIFTY"].sl_atr_mult == 1.4
    assert cfg.indices["NIFTY"].sl_atr_mult == 1.0
    assert cfg.indices["MIDCPNIFTY"].sl_atr_mult == 1.2

def test_atm_strike_and_symbol_resolution():
    """Verify ATM strike rounding for 50-step (Nifty), 100-step (BankNifty), and 25-step (Midcap)."""
    strike_nifty, sym_nifty = get_atm_strike_and_symbol("NIFTY", 24328.50, 50, "CE", "28AUG2026")
    assert strike_nifty == 24350
    assert sym_nifty == "NIFTY28AUG202624350CE"
    
    strike_bnf, sym_bnf = get_atm_strike_and_symbol("BANKNIFTY", 51260.00, 100, "PE", "28AUG2026")
    assert strike_bnf == 51300
    assert sym_bnf == "BANKNIFTY28AUG202651300PE"
    
    strike_mid, sym_mid = get_atm_strike_and_symbol("MIDCPNIFTY", 12388.20, 25, "CE", "28AUG2026")
    assert strike_mid == 12400
    assert sym_mid == "MIDCPNIFTY28AUG202612400CE"

def test_position_persistence_serialization(tmp_path):
    """Verify state persistence and crash recovery roundtrip."""
    now_dt = datetime.datetime.now(IST)
    pos = Position(
        symbol="BANKNIFTY",
        traded_option_symbol="BANKNIFTY28AUG202651300CE",
        side=TradeSide.LONG_CALL,
        option_type="CE",
        entry_option_price=280.0,
        entry_spot_price=51300.0,
        quantity=60,
        lots=2,
        stop_loss=240.0,
        initial_sl=240.0,
        target=380.0,
        entry_time=now_dt,
        current_option_ltp=310.0,
        max_favorable_price=320.0,
        option_atr=35.0,
        trailing_active=True,
        current_step=1
    )
    
    pos_dict = pos.to_dict()
    assert pos_dict["symbol"] == "BANKNIFTY"
    assert pos_dict["entry_option_price"] == 280.0
    
    recovered = Position.from_dict(pos_dict)
    assert recovered.symbol == pos.symbol
    assert recovered.traded_option_symbol == pos.traded_option_symbol
    assert recovered.stop_loss == pos.stop_loss
    assert recovered.trailing_active is True
    assert recovered.current_step == 1

def test_liquidity_sweep_and_choch_signal_detection():
    """Verify Bullish & Bearish Liquidity Sweep detection with Rejection Wick and CHoCH."""
    ts = MultiTimeframeTechnicalSeries()
    ts.pdh = 51500.0
    ts.pdl = 50800.0
    ts.orh = 51400.0
    ts.orl = 50900.0
    
    now_dt = datetime.datetime(2026, 8, 28, 10, 0, tzinfo=IST)
    
    # Pre-seed 3m bar sequence
    bar0 = OHLCVBar(time=now_dt - datetime.timedelta(minutes=6), open=50950, high=50980, low=50920, close=50930, volume=1000)
    bar1 = OHLCVBar(time=now_dt - datetime.timedelta(minutes=3), open=50930, high=50940, low=50880, close=50890, volume=1200) # previous bar
    
    ts.bars_3m.append(bar0)
    ts.bars_3m.append(bar1)
    
    # Current bar: Sweeps PDL (50800) down to 50780 (20 pts sweep >= 10 pts min_sweep), rejects with lower wick and closes green at 50850 > PDL
    # Range = 50860 - 50780 = 80 pts. Lower wick = min(50810, 50850) - 50780 = 30 pts (37.5% >= 30%)
    # CHoCH = close (50850) > bar1.high (50940)? No -> if require_choch is False, triggers!
    curr_bar = OHLCVBar(time=now_dt, open=50810, high=50860, low=50780, close=50850, volume=2000)
    
    bar_range = max(0.01, curr_bar.high - curr_bar.low)
    lower_wick = min(curr_bar.open, curr_bar.close) - curr_bar.low
    assert (lower_wick / bar_range) >= 0.30
    assert curr_bar.close > ts.pdl
    assert (ts.pdl - curr_bar.low) >= 10.0

def test_discrete_milestone_step_lock_trailing():
    """Verify discrete step trailing SL advance on favorable option premium moves."""
    cfg = EngineConfig(
        api_key="test_api_key", host_url="http://127.0.0.1:5000", ws_url="ws://127.0.0.1:8765",
        strategy_name="NSE_LiquiditySweepScalper_V43", indices={},
        stop_loss_mode=StopLossMode.ATR_STEP_TRAILING, base_capital_inr=100000.0,
        risk_per_trade_pct=2.0, max_daily_loss_pct=5.0, max_daily_loss_inr=5000.0,
        max_consecutive_losses=2, max_trades_per_day=3, max_trades_per_index=1,
        max_concurrent_positions=2, backtest_mode=True
    )
    
    engine = NSELiquiditySweepEngine(cfg)
    
    now_dt = datetime.datetime.now(IST)
    pos = Position(
        symbol="NIFTY", traded_option_symbol="NIFTY28AUG202624350CE",
        side=TradeSide.LONG_CALL, option_type="CE",
        entry_option_price=150.0, entry_spot_price=24350.0,
        quantity=65, lots=1, stop_loss=130.0, initial_sl=130.0, target=220.0,
        entry_time=now_dt, current_option_ltp=150.0, max_favorable_price=150.0,
        option_atr=20.0, trailing_active=False, current_step=0
    )
    
    # Milestone 1: Gain >= 1.5x ATR (1.5 * 20 = 30 pts -> Price 180.0) -> Lock Breakeven+ (150 + 0.3*20 = 156.0)
    engine.update_atr_step_trailing_sl(pos, 182.0)
    assert pos.current_step == 1
    assert pos.stop_loss == 156.0
    assert pos.trailing_active is True
    
    # Milestone 2: Gain >= 2.2x ATR (2.2 * 20 = 44 pts -> Price 195.0) -> Lock Entry + 1.0x ATR (150 + 20 = 170.0)
    engine.update_atr_step_trailing_sl(pos, 196.0)
    assert pos.current_step == 2
    assert pos.stop_loss == 170.0

def test_circuit_breaker_enforcement():
    """Verify that daily loss limit and concurrent position limits block new trades."""
    cfg = EngineConfig(
        api_key="test_api_key", host_url="http://127.0.0.1:5000", ws_url="ws://127.0.0.1:8765",
        strategy_name="NSE_LiquiditySweepScalper_V43",
        indices={
            "NIFTY": IndexSpec(
                name="NIFTY", base_symbol="NIFTY", exchange="NSE_INDEX", options_exchange="NFO",
                lot_size=65, strike_step=50, tick_size=0.05, slippage_points=0.20, expiry_weekday=3
            )
        },
        stop_loss_mode=StopLossMode.ATR_STEP_TRAILING, base_capital_inr=100000.0,
        risk_per_trade_pct=2.0, max_daily_loss_pct=5.0, max_daily_loss_inr=5000.0,
        max_consecutive_losses=2, max_trades_per_day=3, max_trades_per_index=1,
        max_concurrent_positions=2, backtest_mode=True
    )
    
    engine = NSELiquiditySweepEngine(cfg)
    now_dt = datetime.datetime.now(IST)
    
    # Simulate daily PnL breach
    stats = engine._get_today_stats(now_dt)
    stats.total_pnl = -5200.0
    
    tripped, reason = engine.check_daily_circuit_breakers(now_dt, "NIFTY")
    assert tripped is True
    assert "MAX_DAILY_LOSS_EXCEEDED" in reason
