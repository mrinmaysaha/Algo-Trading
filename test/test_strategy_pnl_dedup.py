import os
import sys
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from services.strategy_pnl_service import get_multi_timeframe_strategy_analytics


def test_strategy_analytics_deduplication():
    with app.app_context():
        res = get_multi_timeframe_strategy_analytics(timeframe="1D", user_id="Mrinmay49")
        assert res.get("status") == "success"
        strategies = res.get("strategies", {})

        # Ensure no duplicate strategy names exist for Multi-commodity or Liquid sweep
        keys = list(strategies.keys())
        multi_commodity_keys = [k for k in keys if "multi-commodity" in k.lower() or "multicommodity" in k.lower()]
        assert len(multi_commodity_keys) <= 1, f"Found multiple commodity strategy keys: {multi_commodity_keys}"

        liquid_sweep_keys = [k for k in keys if "liquid" in k.lower() and "sweep" in k.lower()]
        assert len(liquid_sweep_keys) <= 1, f"Found multiple liquid sweep keys: {liquid_sweep_keys}"

        # Verify portfolio summary metrics
        summary = res.get("portfolio_summary", {})
        active_strats = [s for s in strategies.values() if s.get("has_activity")]
        assert summary.get("active_strategies_count") == len(active_strats)
        assert summary.get("total_strategies_count") == len(strategies)

        # Verify trade count matches sum of active strategy trades
        summed_trades = sum(s.get("total_trades", 0) for s in active_strats)
        assert summary.get("total_trades") == summed_trades
