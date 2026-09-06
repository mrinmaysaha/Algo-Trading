import os
import sys
import importlib
from unittest.mock import MagicMock
sys.path.insert(0, '/app')

import unittest

os.environ.setdefault("OPENALGO_API_KEY", "mock_test_key")

# Dynamic import for files starting with digits
bn_mod = importlib.import_module("strategies.scripts.3Min_ORB_Quant_20260801205330")
BankNiftyORBStrategy = getattr(bn_mod, "BankNiftyORBStrategy")
load_bn_cfg = getattr(bn_mod, "load_config")

sx_mod = importlib.import_module("strategies.scripts.Sensex_3Min_ORB_Quant_20260831230000")
UnifiedBSEORBStrategy = getattr(sx_mod, "UnifiedBSEORBStrategy")
load_sx_cfg = getattr(sx_mod, "load_config")


class TestDynamicRatchetExecution(unittest.TestCase):
    def test_banknifty_ratchet_lifecycle(self):
        print("\n--- Testing BankNifty 50% Milestone Risk Halver & Scaling ---")
        cfg = load_bn_cfg()
        strat = BankNiftyORBStrategy(cfg)
        strat.order_manager = MagicMock()
        strat.order_manager.place_order.return_value = {"status": "success", "orderid": "MOCK_ORD_1"}

        # Simulate active trade state
        entry_p = 500.0
        risk_pts = 50.0
        strat.state.trade_active = True
        strat.state.option_symbol = "BANKNIFTY26SEP57000CE"
        strat.state.entry_premium = entry_p
        strat.state.initial_risk_pts = risk_pts
        strat.state.remaining_quantity = cfg.get("quantity", 30)
        strat.state.current_sl = entry_p - risk_pts  # 450.0 (-1.0R)
        strat.state.tp1_premium = entry_p + (risk_pts * 2.0)  # 600.0 (+2.0R)
        strat.state.tp2_premium = entry_p + (risk_pts * 3.0)  # 650.0 (+3.0R)
        strat.state.target_premium = strat.state.tp2_premium
        strat.state.risk_halved = False
        strat.state.tp1_hit = False
        strat.state.tp2_hit = False

        # 1. Under 50% distance to TP1 (Trigger is 500 + 0.5 * 100 = 550)
        strat._manage_position(540.0, strat.state.option_symbol)
        self.assertFalse(strat.state.risk_halved)
        self.assertEqual(strat.state.current_sl, 450.0)

        # 2. Reaches 50% distance to TP1 (>= 550.0) -> Halves initial risk to -0.5R (475.0)
        strat._manage_position(552.0, strat.state.option_symbol)
        self.assertTrue(strat.state.risk_halved)
        self.assertEqual(strat.state.current_sl, 475.0)

        # 3. Price reaches TP1 (600.0) -> Triggers TP1 partial lot close & moves SL to Breakeven
        strat._manage_position(602.0, strat.state.option_symbol)
        self.assertTrue(strat.state.tp1_hit)
        self.assertEqual(strat.state.current_sl, entry_p)

        # 4. Price reaches TP2 (650.0) -> Triggers runner close
        strat._manage_position(652.0, strat.state.option_symbol)
        strat.order_manager.place_order.assert_called()

    def test_bse_sensex_ratchet_lifecycle(self):
        print("\n--- Testing BSE Sensex 50% Milestone Risk Halver & Scaling ---")
        cfg = load_sx_cfg()
        strat = UnifiedBSEORBStrategy(cfg)
        strat.order_manager = MagicMock()
        strat.order_manager.close_market_order.return_value = True

        inst = strat.instruments["SENSEX"]
        entry_p = 400.0
        risk_pts = 60.0
        inst.trade_active = True
        inst.option_symbol = "SENSEX26SEP80000CE"
        inst.entry_premium = entry_p
        inst.initial_risk_pts = risk_pts
        inst.remaining_qty = inst.total_quantity
        inst.current_sl = entry_p - risk_pts  # 340.0 (-1.0R)
        inst.tp1_premium = entry_p + (risk_pts * inst.spec["rr_ratio_tp1"])  # 400 + 60*1.5 = 490.0
        inst.tp2_premium = entry_p + (risk_pts * inst.spec["rr_ratio_tp2"])  # 400 + 60*2.5 = 550.0
        inst.risk_halved = False
        inst.tp1_hit = False
        inst.tp2_hit = False

        # 1. Price moves to 430 -> Under 50% to TP1 (Trigger: 400 + 0.5*90 = 445), SL preserved
        strat._manage_position(inst, 430.0)
        self.assertFalse(inst.risk_halved)
        self.assertEqual(inst.current_sl, 340.0)

        # 2. Price reaches 446 (>= 445) -> Triggers 50% Milestone Risk Halver to -0.5R (370.0)
        strat._manage_position(inst, 446.0)
        self.assertTrue(inst.risk_halved)
        self.assertEqual(inst.current_sl, 370.0)

        # 3. Price reaches TP1 (490.0) -> Triggers TP1 partial lot close & moves SL to Breakeven (400.0)
        strat._manage_position(inst, 492.0)
        self.assertTrue(inst.tp1_hit)
        self.assertEqual(inst.current_sl, entry_p)

        # 4. Price reaches TP2 (550.0) -> Triggers runner close
        strat._manage_position(inst, 552.0)
        self.assertTrue(inst.tp2_hit)


if __name__ == '__main__':
    unittest.main()
