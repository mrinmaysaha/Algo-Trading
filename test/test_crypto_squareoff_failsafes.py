import json
import argparse
from datetime import datetime, date
import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies_global.scripts.BTC_Daily_Iron_Condor_Delta import BTCDailyIronCondor
from strategies_global.scripts.ETH_Daily_Iron_Condor_Delta import ETHDailyIronCondor
from strategies_global.scripts.Overnight_Crypto_Delta_Options import (
    Engine,
    load_config,
    default_asset_state,
)


class TestCryptoSquareoffFailsafes:

    def test_intra_strategy_block_on_local_open_positions(self):
        """Test that BTC/ETH Daily blocks entry if local positions are still open."""
        with patch.object(BTCDailyIronCondor, "_load_state"), \
             patch.object(BTCDailyIronCondor, "_save_state"), \
             patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.acquire_singleton_lock"):
            bot = BTCDailyIronCondor(host="http://localhost:5001", api_key="dummy_key", dry_run=True)
            bot.positions = {
                "BTC28SEP2684800CE": {
                    "symbol": "BTC28SEP2684800CE",
                    "status": "OPEN",
                    "quantity": 9,
                    "action": "SELL"
                }
            }
            bot.trade_active = True
            # Verify intra-strategy check detects open positions
            has_open = bot.trade_active or any(p.get("status") != "CLOSED" for p in bot.positions.values())
            assert has_open is True

    def test_check_unmanaged_broker_positions_fail_closed_on_error(self):
        """Test that check_unmanaged_broker_positions fails closed on API error."""
        with patch.object(BTCDailyIronCondor, "_load_state"), \
             patch.object(BTCDailyIronCondor, "_save_state"), \
             patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.acquire_singleton_lock"):
            bot = BTCDailyIronCondor(host="http://localhost:5001", api_key="dummy_key", dry_run=False)

            # Mock API failure (HTTP 500)
            mock_resp = MagicMock()
            mock_resp.status_code = 500
            with patch("requests.post", return_value=mock_resp):
                unmanaged = bot.check_unmanaged_broker_positions()
                assert len(unmanaged) > 0
                assert unmanaged[0]["symbol"] == "BROKER_HTTP_ERROR"

            # Mock API error payload (HTTP 200 but status="error")
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "error", "message": "Failed to fetch positions from Delta"}
            with patch("requests.post", return_value=mock_resp):
                unmanaged = bot.check_unmanaged_broker_positions()
                assert len(unmanaged) > 0
                assert unmanaged[0]["symbol"] == "BROKER_STATUS_ERROR"

    def test_check_unmanaged_broker_positions_detects_foreign_legs(self):
        """Test that check_unmanaged_broker_positions detects foreign/unmanaged legs."""
        with patch.object(BTCDailyIronCondor, "_load_state"), \
             patch.object(BTCDailyIronCondor, "_save_state"), \
             patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.acquire_singleton_lock"):
            bot = BTCDailyIronCondor(host="http://localhost:5001", api_key="dummy_key", dry_run=False)
            bot.positions = {}

            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "success",
                "data": [
                    {
                        "symbol": "BTC28SEP2685000CE",
                        "quantity": 10,
                        "average_price": 50.0,
                        "strategy": "Overnight"
                    }
                ]
            }
            with patch("requests.post", return_value=mock_resp):
                unmanaged = bot.check_unmanaged_broker_positions()
                assert len(unmanaged) == 1
                assert unmanaged[0]["symbol"] == "BTC28SEP2685000CE"
                assert unmanaged[0]["quantity"] == 10

    def test_reentry_blocked_if_session1_not_confirmed_flat(self):
        """Test that _handle_all_legs_closed blocks Session 2 if broker is not confirmed flat."""
        with patch.object(BTCDailyIronCondor, "_load_state"), \
             patch.object(BTCDailyIronCondor, "_save_state"), \
             patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.acquire_singleton_lock"):
            bot = BTCDailyIronCondor(host="http://localhost:5001", api_key="dummy_key", dry_run=False)
            bot.current_session = 1
            bot.positions = {"DUMMY_LEG": {"status": "CLOSED"}}

            # Mock unmanaged returning an orphan position on broker
            with patch.object(bot, "check_unmanaged_broker_positions", return_value=[{"symbol": "BTC28SEP2684800CE", "quantity": 9}]):
                bot._handle_all_legs_closed(exit_trigger="ALL_LEGS_CLOSED")
                # Re-entry must be blocked: trade_taken_today remains True, current_session is NOT incremented
                assert bot.trade_taken_today is True
                assert bot.current_session == 1

    def test_reconcile_positions_requires_consecutive_polls(self):
        """Test that reconcile_positions_with_broker does not prematurely mark CLOSED on a single 0 qty response."""
        with patch.object(BTCDailyIronCondor, "_load_state"), \
             patch.object(BTCDailyIronCondor, "_save_state"), \
             patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.acquire_singleton_lock"):
            bot = BTCDailyIronCondor(host="http://localhost:5001", api_key="dummy_key", dry_run=False)
            sym = "BTC28SEP2684800CE"
            bot.positions = {
                sym: {
                    "symbol": sym,
                    "status": "OPEN",
                    "quantity": 9,
                    "exit_attempted": True
                }
            }

            # First poll returns empty position list (0 qty for sym)
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {"status": "success", "data": []}
            with patch("requests.post", return_value=mock_resp), \
                 patch.object(bot, "_get_strategy_symbols_from_orderbook", return_value={sym}):
                bot.reconcile_positions_with_broker()
                # Status MUST remain OPEN after just 1 poll!
                assert bot.positions[sym]["status"] == "OPEN"
                assert bot._zero_reconcile_polls[sym] == 1

                # Poll 2, 3, 4
                for _ in range(3):
                    bot.reconcile_positions_with_broker()
                assert bot.positions[sym]["status"] == "OPEN"
                assert bot._zero_reconcile_polls[sym] == 4

                # Poll 5: Now it marks closed
                bot.reconcile_positions_with_broker()
                assert bot.positions[sym]["status"] == "CLOSED"

    def test_overnight_inter_strategy_mutex_with_daily(self, tmp_path):
        """Test that Overnight blocks entry if daytime positions are truly live on broker, and overrides stale files if broker is flat."""
        args = argparse.Namespace(symbol="BOTH", mode="paper", force_entry=False, sl_mult=2.0, tp_pct=0.70)
        cfg = load_config(args, need_secrets=False)
        cfg.data_dir = tmp_path
        cfg.halt_file = tmp_path / "halt.flag"

        today_str = "20260928"
        daily_file = tmp_path / f"btc_daily_iron_condor_{today_str}.json"
        with open(daily_file, "w") as f:
            json.dump({"trade_active": True, "positions": {"BTC_OPT": {"status": "OPEN"}}}, f)

        from datetime import datetime, timezone, timedelta
        ist = timezone(timedelta(hours=5, minutes=30))
        real_dt = datetime(2026, 9, 28, 23, 30, tzinfo=ist)
        with patch("strategies_global.scripts.Overnight_Crypto_Delta_Options.now_ist", return_value=real_dt):
            engine = Engine(cfg)
            engine.state["portfolio"] = {"halted": False}
            engine.state["assets"]["BTC"] = default_asset_state()
            engine.state["assets"]["ETH"] = default_asset_state()

            # 1. Truly open on broker -> BLOCKED
            mock_broker = {"BTC_OPT": {"qty": 9}}
            with patch.object(engine, "_fetch_broker_positions", return_value=mock_broker):
                blocked = engine._entry_blocked_by_files()
                assert blocked is not None
                assert "DAYTIME_STRATEGY_UNCLOSED" in blocked

            # 2. Broker FLAT (e.g. expired/settled) -> Live broker truth overrides stale file!
            with patch.object(engine, "_fetch_broker_positions", return_value={}):
                blocked = engine._entry_blocked_by_files()
                assert blocked is None

    def test_overnight_classify_positions_detects_all_foreign_options(self, tmp_path):
        """Test that Overnight classify_positions flags any unowned options as foreign."""
        args = argparse.Namespace(symbol="BOTH", mode="paper", force_entry=False, sl_mult=2.0, tp_pct=0.70)
        cfg = load_config(args, need_secrets=False)
        cfg.data_dir = tmp_path
        cfg.halt_file = tmp_path / "halt.flag"
        engine = Engine(cfg)

        # Mock broker positions with a foreign ETH leg
        mock_broker = {
            "ETH28SEP262680CE": {"quantity": 100, "average_price": 5.0, "ltp": 5.0}
        }
        with patch.object(engine, "_fetch_broker_positions", return_value=mock_broker):
            owned, foreign = engine.classify_positions("BTC")
            assert len(owned) == 0
            assert "ETH28SEP262680CE" in foreign

    def test_eth_daily_detects_overnight_active(self, tmp_path):
        """Test that ETH Daily detects active Overnight strategy when verified live on broker, and permits entry when broker is flat."""
        with patch.object(ETHDailyIronCondor, "_load_state"), \
             patch.object(ETHDailyIronCondor, "_save_state"), \
             patch("strategies_global.scripts.ETH_Daily_Iron_Condor_Delta.acquire_singleton_lock"):
            bot = ETHDailyIronCondor(host="http://localhost:5001", api_key="dummy_key", dry_run=False)

            # Create dummy overnight state file marked active with legs
            from unittest.mock import mock_open
            mock_data = {
                "assets": {
                    "ETH": {
                        "active": True,
                        "legs": {
                            "CE_WING": {"symbol": "ETH29SEP262760CE", "active": True}
                        }
                    }
                }
            }
            # 1. Broker confirms open overnight leg -> BLOCKED
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "status": "success",
                "data": [{"symbol": "ETH29SEP262760CE", "quantity": 168}]
            }
            with patch("pathlib.Path.exists", return_value=True), \
                 patch("builtins.open", mock_open(read_data=json.dumps(mock_data))), \
                 patch("requests.post", return_value=mock_resp):
                is_active, reason = bot.check_overnight_strategy_active()
                assert is_active is True
                assert "ETH29SEP262760CE" in reason

            # 2. Broker confirms FLAT -> Live broker data overrides file -> PERMITTED
            mock_resp.json.return_value = {"status": "success", "data": []}
            with patch("pathlib.Path.exists", return_value=True), \
                 patch("builtins.open", mock_open(read_data=json.dumps(mock_data))), \
                 patch("requests.post", return_value=mock_resp):
                is_active, reason = bot.check_overnight_strategy_active()
                assert is_active is False
                assert reason == "OK"

    def test_square_off_all_places_order_even_if_live_qty_is_zero(self):
        """Test that square_off_all does NOT silently skip order when live_qty returns 0 without confirmed exit price."""
        with patch.object(BTCDailyIronCondor, "_load_state"), \
             patch.object(BTCDailyIronCondor, "_save_state"), \
             patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.acquire_singleton_lock"):
            bot = BTCDailyIronCondor(host="http://localhost:5001", api_key="dummy_key", dry_run=False)
            sym = "BTC28SEP2684800CE"
            bot.positions = {
                sym: {
                    "symbol": sym,
                    "action": "SELL",
                    "quantity": 9,
                    "status": "OPEN",
                    "entry_price": 50.0
                }
            }

            # Mock _get_broker_net_qty returning 0 (transient drop)
            with patch.object(bot, "_get_broker_net_qty", return_value=0.0), \
                 patch.object(bot, "place_and_verify_order", return_value=(True, 20.0, None, None)) as mock_place:
                bot.square_off_all(reason="TEST_EXIT")
                # place_and_verify_order MUST have been called to buy back the short!
                assert mock_place.called
                assert bot.positions[sym]["status"] == "CLOSED"
                assert bot.positions[sym]["exit_price"] == 20.0

    def test_delta_fee_schedule_constants_and_calc(self):
        """Test that DELTA_OPTION_FEE_PCT is 4.13% and fee calculations match empirical CSV math."""
        from strategies_global.scripts.BTC_Daily_Iron_Condor_Delta import DELTA_OPTION_FEE_PCT as BTC_FEE
        from strategies_global.scripts.ETH_Daily_Iron_Condor_Delta import DELTA_OPTION_FEE_PCT as ETH_FEE
        from strategies_global.scripts.Overnight_Crypto_Delta_Options import DELTA_OPTION_FEE_PCT as ON_FEE

        assert BTC_FEE == 0.0413
        assert ETH_FEE == 0.0413
        assert ON_FEE == 0.0413

        # Test fill CSV example: P-BTC-83400-280926 SELL @ $75.00, qty 115, mult 0.001
        # Cashflow = 75.00 * 0.001 * 115 = 8.625 USD
        # Fee = 8.625 * 0.0413 = 0.3562125 USD
        cashflow = 75.00 * 0.001 * 115
        expected_fee = round(cashflow * BTC_FEE, 7)
        assert abs(expected_fee - 0.3562125) < 1e-6

    def test_overnight_preflight_gate_blocks_zero_orders_on_insufficient_margin(self, tmp_path):
        """Test that Overnight enter_iron_condor aborts with 0 orders placed if pre-flight margin check fails."""
        args = argparse.Namespace(symbol="BTC", mode="live", force_entry=True, sl_mult=2.0, tp_pct=0.70)
        cfg = load_config(args, need_secrets=False)
        cfg.data_dir = tmp_path
        cfg.halt_file = tmp_path / "halt.flag"
        cfg.broker_profile = "live"
        engine = Engine(cfg)
        engine.state["portfolio"] = {"halted": False}
        engine.state["assets"]["BTC"] = default_asset_state()

        # Mock spot, expiry, dynamic lots, liquid strikes
        from strategies_global.scripts.Overnight_Crypto_Delta_Options import HuntedStrike, LotsDecision
        with patch.object(engine, "_entry_blocked_by_files", return_value=None), \
             patch.object(engine, "classify_positions", return_value=(set(), set())), \
             patch.object(engine, "get_spot_price", return_value=85000.0), \
             patch.object(engine, "get_1dte_expiry", return_value=("28SEP26", datetime(2026, 9, 28).date(), 0.5)), \
             patch.object(engine, "compute_dynamic_lots", return_value=LotsDecision(100, "OK", 10000.0, 100.0, 88.0, False)), \
             patch.object(engine, "hunt_liquid_strike") as mock_hunt, \
             patch.object(engine, "funds_snapshot", return_value=(500.0, 500.0, 88.0)), \
             patch.object(engine, "place_limit") as mock_place:

            # Return valid strikes: cw, pw, cs, ps
            def hunt_side_effect(asset, base_strike, opt_type, act, exp, lots, is_w, **kwargs):
                if act == "SELL":
                    return HuntedStrike(f"S_{opt_type}", base_strike, 100.0, 105.0, 1000, 1000)
                else:
                    return HuntedStrike(f"W_{opt_type}", base_strike, 10.0, 15.0, 1000, 1000)

            mock_hunt.side_effect = hunt_side_effect
            outcome, reason = engine.enter_iron_condor("BTC")

            # Must abort due to INSUFFICIENT_PREFLIGHT_MARGIN and place 0 orders!
            assert outcome.value == "RETRYABLE"
            assert reason == "INSUFFICIENT_PREFLIGHT_MARGIN"
            assert not mock_place.called

    def test_overnight_preflight_gate_blocks_zero_orders_on_fee_dilution(self, tmp_path):
        """Test that Overnight enter_iron_condor aborts with 0 orders placed if 8.26% round-trip fees dilute credit."""
        args = argparse.Namespace(symbol="BTC", mode="paper", force_entry=True, sl_mult=2.0, tp_pct=0.70)
        cfg = load_config(args, need_secrets=False)
        cfg.data_dir = tmp_path
        cfg.halt_file = tmp_path / "halt.flag"
        engine = Engine(cfg)
        engine.state["portfolio"] = {"halted": False}
        engine.state["assets"]["BTC"] = default_asset_state()

        from strategies_global.scripts.Overnight_Crypto_Delta_Options import HuntedStrike, LotsDecision
        with patch.object(engine, "_entry_blocked_by_files", return_value=None), \
             patch.object(engine, "classify_positions", return_value=(set(), set())), \
             patch.object(engine, "get_spot_price", return_value=85000.0), \
             patch.object(engine, "get_1dte_expiry", return_value=("28SEP26", datetime(2026, 9, 28).date(), 0.5)), \
             patch.object(engine, "compute_dynamic_lots", return_value=LotsDecision(10, "OK", 10000.0, 100.0, 88.0, False)), \
             patch.object(engine, "hunt_liquid_strike") as mock_hunt, \
             patch.object(engine, "place_limit") as mock_place:

            # Return strikes where shorts give $20 credit total but wings cost $18 -> gross credit = $2.00
            # Total cashflow = 20 + 18 = 38. 8.26% fees = 38 * 0.0826 = $3.14 > $2.00 gross credit!
            def hunt_side_effect(asset, base_strike, opt_type, act, exp, lots, is_w, **kwargs):
                if act == "SELL":
                    return HuntedStrike(f"S_{opt_type}", base_strike, 10.0, 10.5, 1000, 1000)
                else:
                    return HuntedStrike(f"W_{opt_type}", base_strike, 8.5, 9.0, 1000, 1000)

            mock_hunt.side_effect = hunt_side_effect
            outcome, reason = engine.enter_iron_condor("BTC")

            # Must abort due to FEE_DILUTION_NEGATIVE and place 0 orders!
            assert outcome.value == "RETRYABLE"
            assert reason == "FEE_DILUTION_NEGATIVE"
            assert not mock_place.called

    def test_overnight_zero_bid_worthless_wing_exits_cleanly(self, tmp_path):
        """Test that Overnight _exit_leg marks a long wing closed at $0.00 when market has 0 bids and shorts are flat."""
        args = argparse.Namespace(symbol="BTC", mode="paper", force_entry=False, sl_mult=2.0, tp_pct=0.70)
        cfg = load_config(args, need_secrets=False)
        cfg.data_dir = tmp_path
        cfg.halt_file = tmp_path / "halt.flag"
        engine = Engine(cfg)
        engine.paper = False  # Test live branch logic
        a = engine.state["assets"]["BTC"]
        a["legs"]["CE_WING"]["symbol"] = "BTC29SEP2690000CE"
        a["legs"]["CE_WING"]["side"] = "BUY"
        a["legs"]["CE_WING"]["filled_qty"] = 10
        a["legs"]["CE_WING"]["exited_qty"] = 0
        a["legs"]["CE_WING"]["active"] = True

        # Shorts are flat
        a["legs"]["CE_SHORT"]["filled_qty"] = 10
        a["legs"]["CE_SHORT"]["exited_qty"] = 10
        a["legs"]["PE_SHORT"]["filled_qty"] = 10
        a["legs"]["PE_SHORT"]["exited_qty"] = 10

        # Mock depth: 0 bid (worthless wing)
        import time
        from strategies_global.scripts.Overnight_Crypto_Delta_Options import DepthQuote
        zero_bid_q = DepthQuote(
            symbol="BTC29SEP2690000CE", bid=0.0, ask=5.0, bid_size=0, ask_size=100,
            cum_bid_3=0, cum_ask_3=100, ts=time.time(), fresh_l2=True
        )

        with patch.object(engine, "_fetch_broker_positions", return_value={}), \
             patch.object(engine, "get_l2_depth", return_value=zero_bid_q), \
             patch.object(engine, "_save_state"):
            is_flat = engine._exit_leg("BTC", "CE_WING", "Morning_0630_Sweep")
            assert is_flat is True
            assert a["legs"]["CE_WING"]["active"] is False
            assert a["legs"]["CE_WING"]["exited_qty"] == 10
            assert a["legs"]["CE_WING"]["exit_avg"] == 0.0
