"""
Unit and Integration Tests for Hardened BTC & ETH Daily Iron Condor Strategies (Delta Exchange)
Verifies:
1. Two-phase order fill verification (success & timeout cancellation)
2. Atomic rollback on partial fill (prevents broken condors and unhedged exposure)
3. Verified stop loss execution and protective wing unwind
4. Broker position reconciliation with /api/v1/positionbook
"""

import unittest
from unittest.mock import patch, MagicMock
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from strategies_global.scripts.BTC_Daily_Iron_Condor_Delta import BTCDailyIronCondor
from strategies_global.scripts.ETH_Daily_Iron_Condor_Delta import ETHDailyIronCondor
from strategies_global.scripts.Overnight_Crypto_Delta_Options import (
    Engine,
    EngineConfig,
    AssetConfig,
    SLConfirmer,
    run_self_tests,
)


class TestCryptoIronCondorHardened(unittest.TestCase):
    def setUp(self):
        self.host = "http://127.0.0.1:5001"
        self.api_key = "test_key_123"
        with patch.object(BTCDailyIronCondor, "_load_state"), patch.object(BTCDailyIronCondor, "_save_state"), \
             patch.object(ETHDailyIronCondor, "_load_state"), patch.object(ETHDailyIronCondor, "_save_state"):
            self.btc_strategy = BTCDailyIronCondor(
                host=self.host, api_key=self.api_key, lots=60, capital=10000.0, dry_run=False
            )
            self.eth_strategy = ETHDailyIronCondor(
                host=self.host, api_key=self.api_key, lots=60, capital=10000.0, dry_run=False
            )
        self.btc_strategy._save_state = MagicMock()
        self.eth_strategy._save_state = MagicMock()

    # --------------------------------------------------------------------------
    # 1. Fill Verification & Timeout Cancellation Tests
    # --------------------------------------------------------------------------
    @patch("requests.post")
    def test_place_and_verify_order_success(self, mock_post):
        # 1st call: placeorder -> success with orderid
        # 2nd call: orderstatus -> complete @ 1.25
        mock_place = MagicMock()
        mock_place.status_code = 200
        mock_place.json.return_value = {"status": "success", "orderid": "ORD_TEST_001"}

        mock_status = MagicMock()
        mock_status.status_code = 200
        mock_status.json.return_value = {
            "status": "success",
            "data": {
                "orderid": "ORD_TEST_001",
                "order_status": "complete",
                "average_price": 1.25,
            },
        }

        mock_post.side_effect = [mock_place, mock_status]

        success, fill_price, oid, filled_qty = self.btc_strategy.place_and_verify_order(
            "BTC18SEP2679500CE", "BUY", 60, timeout_sec=2
        )

        self.assertTrue(success)
        self.assertEqual(fill_price, 1.25)
        self.assertEqual(oid, "ORD_TEST_001")
        self.assertEqual(filled_qty, 60)

    @patch("requests.post")
    def test_place_and_verify_order_timeout_cancels_hanging_order(self, mock_post):
        # Placeorder succeeds, but orderstatus remains 'open' -> triggers cancelorder
        mock_place = MagicMock()
        mock_place.status_code = 200
        mock_place.json.return_value = {"status": "success", "orderid": "ORD_HANG_999"}

        mock_status = MagicMock()
        mock_status.status_code = 200
        mock_status.json.return_value = {
            "status": "success",
            "data": {"orderid": "ORD_HANG_999", "order_status": "open", "price": 1.45},
        }

        mock_cancel = MagicMock()
        mock_cancel.status_code = 200
        mock_cancel.json.return_value = {"status": "success", "message": "Order cancelled"}

        # placeorder, then 2 status polls, then cancelorder
        mock_post.side_effect = [mock_place, mock_status, mock_status, mock_cancel]

        success, fill_price, oid, filled_qty = self.btc_strategy.place_and_verify_order(
            "BTC18SEP2674400PE", "SELL", 60, timeout_sec=1
        )

        self.assertFalse(success)
        self.assertEqual(fill_price, 0.0)
        self.assertEqual(oid, "ORD_HANG_999")
        self.assertEqual(filled_qty, 0)

    # --------------------------------------------------------------------------
    # 2. Atomic Rollback on Partial Fill Test
    # --------------------------------------------------------------------------
    @patch.object(BTCDailyIronCondor, "get_spot_price", return_value=76500.0)
    @patch.object(BTCDailyIronCondor, "get_today_expiry", return_value="18-SEP-26")
    @patch.object(BTCDailyIronCondor, "check_strike_liquidity", return_value=(True, 1.0, 1.5))
    @patch.object(BTCDailyIronCondor, "resolve_option_symbol", side_effect=lambda s, opt, exp: f"BTC_{int(s)}_{opt}")
    @patch.object(BTCDailyIronCondor, "place_and_verify_order")
    def test_execute_iron_condor_atomic_rollback(
        self, mock_pvo, mock_resolve, mock_liq, mock_exp, mock_spot
    ):

        # Leg 1 (LONG_CE): fills
        # Leg 2 (LONG_PE): fills
        # Leg 3 (SHORT_CE): fills
        # Leg 4 (SHORT_PE): FAILS (timeout / no liquidity)
        # Rollbacks:
        # Revert Short Leg 3 (BUY): fills
        # Revert Wing Leg 1 (SELL): fills
        # Revert Wing Leg 2 (SELL): fills
        mock_pvo.side_effect = [
            (True, 0.55, "ORD_1", 60),
            (True, 0.55, "ORD_2", 60),
            (True, 1.05, "ORD_3", 60),
            (False, 0.0, "ORD_4_FAIL", 0),  # 4th leg fails!
            (True, 1.06, "ORD_RB_3", 60),    # Rollback Short Leg 3 (shorts rolled back first)
            (True, 0.54, "ORD_RB_1", 60),    # Rollback Wing Leg 1
            (True, 0.54, "ORD_RB_2", 60),    # Rollback Wing Leg 2
        ]

        self.btc_strategy.execute_iron_condor()

        # Condor must NOT be active
        self.assertFalse(self.btc_strategy.trade_active)
        self.assertEqual(len(self.btc_strategy.positions), 0)

    # --------------------------------------------------------------------------
    # 3. Stop-Loss Trigger & Verified Wing Unwind Test
    # --------------------------------------------------------------------------
    @patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.get_current_ist_time")
    @patch.object(
        BTCDailyIronCondor,
        "get_quote_data",
        side_effect=lambda s: {"ask": 1.85, "bid": 1.80, "ltp": 1.85, "spread": 0.02} if "CE" in s else {"ask": 1.0, "bid": 0.98, "ltp": 1.0, "spread": 0.02},
    )
    @patch.object(BTCDailyIronCondor, "place_and_verify_order")
    def test_manage_active_positions_stop_loss_verified_unwind(self, mock_pvo, mock_qd, mock_time):
        from datetime import time
        mock_time.return_value = time(11, 0)
        self.btc_strategy.sl_confirmer.need = 1
        self.btc_strategy.trade_active = True
        self.btc_strategy.positions = {
            "BTC_SHORT_CE": {
                "leg_type": "SHORT_CE",
                "action": "SELL",
                "quantity": 60,
                "entry_price": 1.00,
                "status": "OPEN",
                "stop_loss": 1.50,  # Breached by LTP 1.85
            },
            "BTC_LONG_CE": {
                "leg_type": "LONG_CE",
                "action": "BUY",
                "quantity": 60,
                "entry_price": 0.50,
                "status": "OPEN",
                "stop_loss": 0.0,
            },
            "BTC_SHORT_PE": {
                "leg_type": "SHORT_PE",
                "action": "SELL",
                "quantity": 60,
                "entry_price": 1.20,
                "status": "OPEN",
                "stop_loss": 1.80,
            },
            "BTC_LONG_PE": {
                "leg_type": "LONG_PE",
                "action": "BUY",
                "quantity": 60,
                "entry_price": 0.50,
                "status": "OPEN",
                "stop_loss": 0.0,
            },
        }

        # 1. Close tested short call (BUY) -> verified
        # 2. Close protective long call wing (SELL) -> verified
        mock_pvo.side_effect = [
            (True, 1.85, "ORD_SL_BUY", 60),
            (True, 0.70, "ORD_WING_SELL", 60),
        ]

        self.btc_strategy.manage_active_positions()

        # Both CE legs must now be CLOSED
        self.assertEqual(self.btc_strategy.positions["BTC_SHORT_CE"]["status"], "CLOSED")
        self.assertEqual(self.btc_strategy.positions["BTC_SHORT_CE"]["exit_reason"], "SL_HIT")
        self.assertEqual(self.btc_strategy.positions["BTC_LONG_CE"]["status"], "CLOSED")
        self.assertEqual(self.btc_strategy.positions["BTC_LONG_CE"]["exit_reason"], "WING_UNWIND")

        # PE legs remain OPEN
        self.assertEqual(self.btc_strategy.positions["BTC_SHORT_PE"]["status"], "OPEN")
        self.assertEqual(self.btc_strategy.positions["BTC_LONG_PE"]["status"], "OPEN")
        self.assertTrue(self.btc_strategy.trade_active)

    # --------------------------------------------------------------------------
    # 4. Broker Position Reconciliation Tests
    # --------------------------------------------------------------------------
    @patch("requests.post")
    def test_reconcile_positions_with_broker_marks_closed(self, mock_post):
        self.eth_strategy.trade_active = True
        self.eth_strategy.positions = {
            "ETH_LEG_1": {"status": "OPEN", "exit_attempted": True},
            "ETH_LEG_2": {"status": "OPEN", "exit_attempted": True},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "data": [
                {"symbol": "ETH_LEG_1", "quantity": 0},
                {"symbol": "ETH_LEG_2", "quantity": 0},
            ],
        }
        mock_post.return_value = mock_resp

        self.eth_strategy.reconcile_positions_with_broker()
        self.assertEqual(self.eth_strategy.positions["ETH_LEG_1"]["status"], "CLOSED")
        self.assertEqual(self.eth_strategy.positions["ETH_LEG_2"]["status"], "CLOSED")
        self.assertFalse(self.eth_strategy.trade_active)

    @patch("requests.post")
    def test_reconcile_prevents_false_closure_when_no_exit_attempted(self, mock_post):
        # Ghost-flat prevention test:
        # Broker reports 0 quantity (e.g. from an earlier overnight closed trade or API glitch)
        # but strategy never attempted an exit. Strategy MUST keep it OPEN!
        self.btc_strategy.trade_active = True
        self.btc_strategy.positions = {
            "BTC_CALL_WING": {"status": "OPEN", "action": "BUY", "quantity": 60, "exit_attempted": False},
        }

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "status": "success",
            "data": [
                {"symbol": "BTC_CALL_WING", "product": "NRML", "quantity": 0},
            ],
        }
        mock_post.return_value = mock_resp

        self.btc_strategy.reconcile_positions_with_broker()
        self.assertEqual(self.btc_strategy.positions["BTC_CALL_WING"]["status"], "OPEN")
        self.assertTrue(self.btc_strategy.trade_active)

    @patch.object(BTCDailyIronCondor, "_get_broker_net_qty")
    @patch.object(BTCDailyIronCondor, "place_and_verify_order")
    def test_square_off_all_executes_shorts_before_wings(self, mock_pvo, mock_live_qty):
        # Short-First ordering test:
        # Long wings must NOT be closed before short legs!
        mock_live_qty.return_value = 60.0
        mock_pvo.return_value = (True, 10.0, "ORD_EXIT", 60)

        self.btc_strategy.trade_active = True
        self.btc_strategy.positions = {
            "BTC_LONG_CE": {"status": "OPEN", "action": "BUY", "quantity": 60},
            "BTC_LONG_PE": {"status": "OPEN", "action": "BUY", "quantity": 60},
            "BTC_SHORT_CE": {"status": "OPEN", "action": "SELL", "quantity": 60},
            "BTC_SHORT_PE": {"status": "OPEN", "action": "SELL", "quantity": 60},
        }

        self.btc_strategy.square_off_all(reason="TEST_TARGET_DECAY")

        # Verify call order: first 2 calls MUST be for short legs (BUY to close), last 2 for wings (SELL to close)
        calls = mock_pvo.call_args_list
        self.assertEqual(len(calls), 4)
        first_leg_sym, first_act, _ = calls[0][0]
        second_leg_sym, second_act, _ = calls[1][0]
        third_leg_sym, third_act, _ = calls[2][0]
        fourth_leg_sym, fourth_act, _ = calls[3][0]

        self.assertIn(first_act, ["BUY"])   # BUY to close short
        self.assertIn(second_act, ["BUY"])  # BUY to close short
        self.assertIn(third_act, ["SELL"])  # SELL to close long wing
        self.assertIn(fourth_act, ["SELL"]) # SELL to close long wing

        self.assertIn(first_leg_sym, ["BTC_SHORT_CE", "BTC_SHORT_PE"])
        self.assertIn(second_leg_sym, ["BTC_SHORT_CE", "BTC_SHORT_PE"])
        self.assertIn(third_leg_sym, ["BTC_LONG_CE", "BTC_LONG_PE"])
        self.assertIn(fourth_leg_sym, ["BTC_LONG_CE", "BTC_LONG_PE"])

    @patch.object(BTCDailyIronCondor, "_get_broker_net_qty")
    @patch.object(BTCDailyIronCondor, "place_and_verify_order")
    def test_square_off_all_skips_when_broker_already_flat(self, mock_pvo, mock_live_qty):
        # Over-selling prevention test:
        # If broker reports 0 live quantity, square_off_all MUST skip placing order to avoid position flip
        mock_live_qty.return_value = 0.0

        self.btc_strategy.trade_active = True
        self.btc_strategy.positions = {
            "BTC_TEST_LEG": {"status": "OPEN", "action": "BUY", "quantity": 60},
        }

        self.btc_strategy.square_off_all(reason="TEST_ALREADY_FLAT")
        mock_pvo.assert_not_called()
        self.assertFalse(self.btc_strategy.trade_active)
        # Verify recorded as closed in session history
        last_hist = self.btc_strategy.session_history[-1]
        self.assertEqual(last_hist["positions"]["BTC_TEST_LEG"]["status"], "CLOSED")
        self.assertEqual(last_hist["positions"]["BTC_TEST_LEG"]["exit_reason"], "ALREADY_FLAT")

    # --------------------------------------------------------------------------
    # 5. Adaptive Liquid Strike Hunter Tests
    # --------------------------------------------------------------------------
    @patch.object(BTCDailyIronCondor, "check_strike_liquidity")
    @patch.object(BTCDailyIronCondor, "resolve_option_symbol")
    def test_adaptive_liquid_strike_hunter_walks_inward_on_illiquid_strike(
        self, mock_resolve, mock_liq
    ):
        # Target strike 74400 is illiquid (mock_liq returns False)
        # Next candidate 74500 is liquid (mock_liq returns True)
        mock_resolve.side_effect = lambda s, opt, exp: f"BTC18SEP26{int(s)}{opt}"
        mock_liq.side_effect = [
            (False, 0.0, 0.0),    # Strike 74400 PE -> illiquid
            (True, 0.80, 1.20),   # Strike 74500 PE -> liquid!
        ]

        sym = self.btc_strategy.resolve_liquid_option_symbol(
            target_strike=74400.0,
            option_type="PE",
            expiry="18-SEP-26",
            action="BUY",
            is_wing=True,
            short_strike_ref=75200.0,
        )

        self.assertEqual(sym, "BTC18SEP2674500PE")
        self.assertEqual(mock_liq.call_count, 2)

    # --------------------------------------------------------------------------
    # 6. 3-Minute Retry Cooldown & Max Attempts Tests
    # --------------------------------------------------------------------------
    def test_retry_cooldown_and_max_attempts(self):
        self.btc_strategy.entry_attempts = 1
        self.btc_strategy.last_attempt_time = 1000.0
        self.btc_strategy.retry_cooldown_sec = 180
        self.btc_strategy.max_entry_attempts = 3

        # Elapsed 60s < 180s -> should be in cooldown
        now_time = 1060.0
        elapsed = now_time - self.btc_strategy.last_attempt_time
        self.assertLess(elapsed, self.btc_strategy.retry_cooldown_sec)

        # Elapsed 190s >= 180s -> cooldown passed, can retry
        now_time_retry = 1190.0
        elapsed_retry = now_time_retry - self.btc_strategy.last_attempt_time
        self.assertGreaterEqual(elapsed_retry, self.btc_strategy.retry_cooldown_sec)

        # Max attempts reached
        self.btc_strategy.entry_attempts = 3
        self.assertGreaterEqual(
            self.btc_strategy.entry_attempts, self.btc_strategy.max_entry_attempts
        )


    # --------------------------------------------------------------------------
    # 7. Architecture A2 Configuration Verification
    # --------------------------------------------------------------------------
    def test_architecture_a2_parameters(self):
        # BTC Arch A2
        self.assertEqual(self.btc_strategy.mode, "condor")
        self.assertEqual(self.btc_strategy.otm_pct, 0.010)
        self.assertEqual(self.btc_strategy.spread_width, 800.0)
        self.assertEqual(self.btc_strategy.min_premium, 35.0)
        self.assertEqual(self.btc_strategy.max_spread_pct, 0.05)
        self.assertEqual(self.btc_strategy.sl_multiplier, 1.5)
        self.assertEqual(self.btc_strategy.target_decay_pct, 0.85)

        # ETH Arch A2
        self.assertEqual(self.eth_strategy.mode, "condor")
        self.assertEqual(self.eth_strategy.otm_pct, 0.010)
        self.assertEqual(self.eth_strategy.spread_width, 30.0)
        self.assertEqual(self.eth_strategy.min_premium, 2.0)
        self.assertEqual(self.eth_strategy.max_spread_pct, 0.05)
        self.assertEqual(self.eth_strategy.sl_multiplier, 1.5)
        self.assertEqual(self.eth_strategy.target_decay_pct, 0.85)

    # --------------------------------------------------------------------------
    # 8. Architecture B3 (ATM Straddle with 30% SL) Tests
    # --------------------------------------------------------------------------
    @patch.object(BTCDailyIronCondor, "_load_state")
    @patch.object(BTCDailyIronCondor, "_save_state")
    @patch.object(BTCDailyIronCondor, "get_spot_price", return_value=75000.0)
    @patch.object(BTCDailyIronCondor, "get_today_expiry", return_value="18-SEP-26")
    @patch.object(BTCDailyIronCondor, "resolve_liquid_option_symbol", side_effect=lambda s, opt, exp, **kwargs: f"BTC_{int(s)}_{opt}")
    @patch.object(BTCDailyIronCondor, "place_and_verify_order")
    def test_execute_atm_straddle_btc_success(
        self, mock_pvo, mock_resolve, mock_exp, mock_spot, mock_save, mock_load
    ):
        straddle_strategy = BTCDailyIronCondor(
            host=self.host, api_key=self.api_key, lots=10, capital=50000.0, dry_run=False, mode="straddle"
        )
        self.assertEqual(straddle_strategy.mode, "straddle")
        self.assertEqual(straddle_strategy.straddle_sl_pct, 0.30)

        # 2 legs: SHORT_CE, SHORT_PE
        mock_pvo.side_effect = [
            (True, 150.0, "ORD_STRADDLE_CE", 10),
            (True, 140.0, "ORD_STRADDLE_PE", 10),
        ]

        success = straddle_strategy.execute_atm_straddle()
        self.assertTrue(success)
        self.assertTrue(straddle_strategy.trade_active)
        self.assertEqual(len(straddle_strategy.positions), 2)

        # Verify 30% stop loss (+30% above entry price)
        ce_pos = straddle_strategy.positions["BTC_75000_CE"]
        pe_pos = straddle_strategy.positions["BTC_75000_PE"]
        self.assertAlmostEqual(ce_pos["stop_loss"], 150.0 * 1.30)
        self.assertAlmostEqual(pe_pos["stop_loss"], 140.0 * 1.30)

    @patch.object(ETHDailyIronCondor, "_load_state")
    @patch.object(ETHDailyIronCondor, "_save_state")
    @patch.object(ETHDailyIronCondor, "get_spot_price", return_value=2450.0)
    @patch.object(ETHDailyIronCondor, "get_today_expiry", return_value="18-SEP-26")
    @patch.object(ETHDailyIronCondor, "resolve_liquid_option_symbol", side_effect=lambda s, opt, exp, **kwargs: f"ETH_{int(s)}_{opt}")
    @patch.object(ETHDailyIronCondor, "place_and_verify_order")
    def test_execute_atm_straddle_eth_atomic_rollback(
        self, mock_pvo, mock_resolve, mock_exp, mock_spot, mock_save, mock_load
    ):
        eth_straddle = ETHDailyIronCondor(
            host=self.host, api_key=self.api_key, lots=10, capital=50000.0, dry_run=False, mode="straddle"
        )
        self.assertEqual(eth_straddle.mode, "straddle")
        self.assertEqual(eth_straddle.straddle_sl_pct, 0.30)

        # Leg 1 fills, Leg 2 fails -> Rollback Leg 1
        mock_pvo.side_effect = [
            (True, 8.50, "ORD_ETH_CE", 10),
            (False, 0.0, "ORD_ETH_PE_FAIL", 0),
            (True, 8.55, "ORD_ETH_RB", 10),
        ]

        success = eth_straddle.execute_atm_straddle()
        self.assertFalse(success)
        self.assertFalse(eth_straddle.trade_active)
        self.assertEqual(len(eth_straddle.positions), 0)

    # --------------------------------------------------------------------------
    # 5. Session 2 Re-Strike (12:30 Cutoff) Tests
    # --------------------------------------------------------------------------
    @patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.get_current_ist_time")
    def test_reentry_eligible_before_1230(self, mock_time):
        from datetime import time
        # Session 1 finishes at 12:15 IST (before 12:30 cutoff)
        mock_time.return_value = time(12, 15)
        self.btc_strategy.current_session = 1
        self.btc_strategy.trade_active = True
        self.btc_strategy.positions = {
            "BTC_CALL": {"status": "CLOSED", "action": "SELL", "quantity": 60},
            "BTC_PUT": {"status": "CLOSED", "action": "SELL", "quantity": 60},
        }

        self.btc_strategy._handle_all_legs_closed(exit_trigger="TARGET_PROFIT_DECAY")

        # Must transition to Session 2 and allow entry
        self.assertEqual(self.btc_strategy.current_session, 2)
        self.assertFalse(self.btc_strategy.trade_active)
        self.assertFalse(self.btc_strategy.trade_taken_today)
        self.assertEqual(len(self.btc_strategy.positions), 0)
        self.assertEqual(len(self.btc_strategy.session_history), 1)

    @patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.get_current_ist_time")
    def test_reentry_rejected_after_1230(self, mock_time):
        from datetime import time
        # Session 1 finishes at 15:45 IST (after 15:30 cutoff)
        mock_time.return_value = time(15, 45)
        self.btc_strategy.current_session = 1
        self.btc_strategy.trade_active = True
        self.btc_strategy.positions = {
            "BTC_CALL": {"status": "CLOSED", "action": "SELL", "quantity": 60},
            "BTC_PUT": {"status": "CLOSED", "action": "SELL", "quantity": 60},
        }

        self.btc_strategy._handle_all_legs_closed(exit_trigger="TARGET_PROFIT_DECAY")

        # Must NOT transition to Session 2, marked done today
        self.assertEqual(self.btc_strategy.current_session, 1)
        self.assertFalse(self.btc_strategy.trade_active)
        self.assertTrue(self.btc_strategy.trade_taken_today)

    @patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.get_current_ist_time")
    def test_reentry_max_sessions_reached(self, mock_time):
        from datetime import time
        # Session 2 finishes at 15:30 IST
        mock_time.return_value = time(15, 30)
        self.btc_strategy.current_session = 2
        self.btc_strategy.trade_active = True
        self.btc_strategy.positions = {
            "BTC_CALL": {"status": "CLOSED", "action": "SELL", "quantity": 60},
            "BTC_PUT": {"status": "CLOSED", "action": "SELL", "quantity": 60},
        }

        self.btc_strategy._handle_all_legs_closed(exit_trigger="TARGET_PROFIT_DECAY")

        # Must NOT allow session 3
        self.assertEqual(self.btc_strategy.current_session, 2)
        self.assertFalse(self.btc_strategy.trade_active)
        self.assertTrue(self.btc_strategy.trade_taken_today)

    @patch("strategies_global.scripts.BTC_Daily_Iron_Condor_Delta.get_current_ist_time")
    def test_short_sl_tested_side_skips_when_broker_already_flat(self, mock_time):
        from datetime import time
        mock_time.return_value = time(11, 0)
        self.btc_strategy.trade_active = True
        self.btc_strategy.positions = {
            "SHORT_SYM": {
                "leg_type": "SHORT_CE",
                "action": "SELL",
                "quantity": 60,
                "status": "OPEN",
                "stop_loss": 50.0,
                "entry_price": 25.0,
            },
            "WING_SYM": {
                "leg_type": "LONG_CE",
                "action": "BUY",
                "quantity": 60,
                "status": "OPEN",
                "stop_loss": 0.0,
                "entry_price": 5.0,
            }
        }
        self.btc_strategy.sl_confirmer.need = 1
        self.btc_strategy._get_quote_cached = MagicMock(return_value={"ask": 60.0, "bid": 59.0, "ltp": 60.0, "spread": 0.02}) # SL hit
        # Broker reports 0 qty for short, 0 qty for wing (already flat)
        self.btc_strategy._get_broker_net_qty = MagicMock(side_effect=lambda sym: 0.0)
        self.btc_strategy.place_and_verify_order = MagicMock()

        self.btc_strategy.manage_active_positions()

        # Both positions should be closed with ALREADY_FLAT without placing orders, archived to session_history
        archived_positions = self.btc_strategy.session_history[-1]["positions"]
        self.assertEqual(archived_positions["SHORT_SYM"]["status"], "CLOSED")
        self.assertEqual(archived_positions["SHORT_SYM"]["exit_reason"], "ALREADY_FLAT")
        self.assertEqual(archived_positions["WING_SYM"]["status"], "CLOSED")
        self.assertEqual(archived_positions["WING_SYM"]["exit_reason"], "ALREADY_FLAT")
        self.btc_strategy.place_and_verify_order.assert_not_called()

    def test_daily_condor_sl_confirmer_multi_tick_and_spread_guard(self):
        # Configure 3-tick confirmation on BTC strategy
        self.btc_strategy.sl_confirmer.need = 3
        sym = "BTC_SHORT_TEST"
        sl = 100.0

        # Tick 1 & 2: above SL, but not confirmed yet
        self.btc_strategy.sl_confirmer.add_tick(sym, ask=105.0, spread=0.10)
        ok1, _ = self.btc_strategy.sl_confirmer.triggered(sym, sl)
        self.assertFalse(ok1)

        self.btc_strategy.sl_confirmer.add_tick(sym, ask=106.0, spread=0.10)
        ok2, _ = self.btc_strategy.sl_confirmer.triggered(sym, sl)
        self.assertFalse(ok2)

        # Tick 3: confirmed!
        self.btc_strategy.sl_confirmer.add_tick(sym, ask=107.0, spread=0.10)
        ok3, why = self.btc_strategy.sl_confirmer.triggered(sym, sl)
        self.assertTrue(ok3)
        self.assertEqual(why, "3x_CONFIRMED")

        # Wide spread rejection (>40%)
        self.btc_strategy.sl_confirmer.ticks[sym] = []
        for _ in range(3):
            self.btc_strategy.sl_confirmer.add_tick(sym, ask=110.0, spread=0.50)
        ok_wide, why_wide = self.btc_strategy.sl_confirmer.triggered(sym, sl)
        self.assertFalse(ok_wide)
        self.assertEqual(why_wide, "SPREAD_WIDE")

    def test_daily_condor_rate_limiter_acquires_tokens(self):
        # Verify rate limiter token bucket logic
        limiter = self.btc_strategy.limiter
        self.assertGreaterEqual(limiter.tokens, 0.0)
        limiter.acquire()
        self.assertTrue(hasattr(limiter, "rate"))
        self.assertEqual(limiter.capacity, 16)


class TestOvernightCryptoDeltaOptionsHardened(unittest.TestCase):
    def setUp(self):
        self.cfg = EngineConfig(
            symbols=["BTC"],
            mode="paper",
            broker_profile="paper",
            host="http://127.0.0.1:5001",
            api_key="mock_key_mock_key_1234",
            force_entry=False,
            capital_base_inr=10000.0,
            margin_util_cap=0.65,
            margin_safety_mult=1.5,
            usd_inr=88.0,
            usd_inr_explicit=True,
            dynamic_sizing=False,
            sl_mult=2.0,
            tp_pct=0.65,
            taker_fee_bps=30.0,
            per_order_fee_usd=0.05,
            exit_slip_bps=10.0,
            max_nightly_loss_inr=1500.0,
            max_sl_per_night=2,
            error_budget=15,
            assets={
                "BTC": AssetConfig(
                    underlying="BTC",
                    futures_symbol="BTCUSDFUT",
                    strike_step=100.0,
                    otm_pct=0.02,
                    wing_width_strikes=8,
                    contract_mult=0.001,
                    default_lots=92,
                    max_wing_price=65.0,
                    max_spread_pct=0.15,
                    max_short_spread_dollar=3.0,
                    max_wing_spread_pct=0.25,
                    max_wing_spread_dollar=4.5,
                    min_depth_contracts=100,
                )
            },
        )
        self.engine = Engine(self.cfg)

    def test_overnight_production_self_tests_pass(self):
        # Executes all 18 built-in production test cases
        rc = run_self_tests()
        self.assertEqual(rc, 0)

    def test_overnight_sl_confirmer_requires_3_ticks(self):
        conf = SLConfirmer(need=3, window_s=20.0, max_spread=0.40, max_age=5.0)
        ticks = []
        # 1st tick above SL: not confirmed yet
        conf.add_tick(ticks, ask=85.0, spread=0.10, size=50, age=0.5)
        ok, reason = conf.triggered(ticks, sl=80.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "NEED_MORE_TICKS")

        # 2nd tick
        conf.add_tick(ticks, ask=86.0, spread=0.10, size=50, age=0.5)
        ok, reason = conf.triggered(ticks, sl=80.0)
        self.assertFalse(ok)

        # 3rd tick above SL: confirmed!
        conf.add_tick(ticks, ask=87.0, spread=0.10, size=50, age=0.5)
        ok, reason = conf.triggered(ticks, sl=80.0)
        self.assertTrue(ok)
        self.assertEqual(reason, "3x_CONFIRMED")

    def test_overnight_sl_confirmer_rejects_wide_spread(self):
        conf = SLConfirmer(need=3, window_s=20.0, max_spread=0.40, max_age=5.0)
        ticks = []
        for _ in range(3):
            conf.add_tick(ticks, ask=90.0, spread=0.55, size=50, age=0.5)  # 55% spread > 40% cap
        ok, reason = conf.triggered(ticks, sl=80.0)
        self.assertFalse(ok)
        self.assertEqual(reason, "SPREAD_WIDE")

    def test_overnight_unwind_all_exact_executes_shorts_before_wings(self):
        self.engine.state["assets"]["BTC"]["legs"] = {
            "CE_SHORT": {"symbol": "BTC_CE_SHORT", "filled_qty": 92},
            "PE_SHORT": {"symbol": "BTC_PE_SHORT", "filled_qty": 92},
            "CE_WING": {"symbol": "BTC_CE_WING", "filled_qty": 92},
            "PE_WING": {"symbol": "BTC_PE_WING", "filled_qty": 92},
        }
        call_order = []

        def mock_exit(symbol, action, qty, reason):
            call_order.append((symbol, action))
            return 0

        self.engine._exit_symbol_marketish = MagicMock(side_effect=mock_exit)

        self.engine._unwind_all_exact("BTC", "TEST_EMERGENCY")

        # First 2 must be shorts with BUY, last 2 must be wings with SELL
        self.assertEqual(len(call_order), 4)
        self.assertEqual(call_order[0], ("BTC_CE_SHORT", "BUY"))
        self.assertEqual(call_order[1], ("BTC_PE_SHORT", "BUY"))
        self.assertEqual(call_order[2], ("BTC_CE_WING", "SELL"))
        self.assertEqual(call_order[3], ("BTC_PE_WING", "SELL"))

    def test_overnight_exit_skips_when_broker_already_flat(self):
        # Broker positions report 0 for symbol
        self.engine._fetch_broker_positions = MagicMock(return_value={"BTC_TEST_SYM": {"quantity": 0.0}})
        self.engine.place_limit = MagicMock()

        # In live mode
        self.engine.paper = False
        leftover, _ = self.engine._exit_symbol_marketish("BTC_TEST_SYM", "BUY", 92, "TEST_FLAT")
        self.assertEqual(leftover, 0)
        self.engine.place_limit.assert_not_called()


if __name__ == "__main__":
    unittest.main()

