import sys
import importlib
from unittest.mock import MagicMock
sys.path.insert(0, '/app')

import unittest

# Dynamic import for files starting with digits
bn_mod = importlib.import_module("strategies.scripts.3Min_ORB_Quant_20260801205330")
BankNiftyORBStrategy = getattr(bn_mod, "BankNiftyORBStrategy")
load_bn_cfg = getattr(bn_mod, "load_config")

sx_mod = importlib.import_module("strategies.scripts.Sensex_3Min_ORB_Quant_20260831230000")
SensexORBStrategy = getattr(sx_mod, "SensexORBStrategy")
load_sx_cfg = getattr(sx_mod, "load_config")

class TestDynamicRatchetExecution(unittest.TestCase):
    def test_banknifty_ratchet_lifecycle(self):
        print("\n--- Testing BankNifty Dynamic Ratchet Lifecycle ---")
        cfg = load_bn_cfg()
        strat = BankNiftyORBStrategy(cfg)
        strat.order_manager = MagicMock()
        
        # Simulate active trade state
        entry_p = 500.0
        atr = 50.0
        strat.state.trade_active = True
        strat.state.option_symbol = "BANKNIFTY26SEP57000CE"
        strat.state.entry_premium = entry_p
        strat.state.option_atr = atr
        strat.state.current_sl = entry_p - (atr * 1.0) # 450.0
        strat.state.target_premium = 700.0 # High target to test dynamic ratchet
        strat.state.highest_opt = entry_p
        strat.state.sl_activated = False
        strat.state.tp1_hit = False
        strat.state.tp2_hit = False
        strat.state.last_step_trigger = None

        # 1. Price moves to 550 (+1.0 ATR) -> Under TP1 (1.8 ATR = 590), SL should NOT activate yet
        strat._manage_position(550.0, strat.state.option_symbol)
        self.assertFalse(strat.state.tp1_hit)
        self.assertEqual(strat.state.current_sl, 450.0)
        print("  [Pass] Under TP1: SL preserved at initial SL (450.0)")

        # 2. Price reaches 592 (+1.84 ATR) -> Triggers TP1! SL moves to Breakeven+ (500 + 0.15*50 = 507.5)
        strat._manage_position(592.0, strat.state.option_symbol)
        self.assertTrue(strat.state.tp1_hit)
        self.assertTrue(strat.state.sl_activated)
        self.assertGreaterEqual(strat.state.current_sl, 507.5)
        print(f"  [Pass] TP1 Hit: SL ratcheted to Breakeven+ ({strat.state.current_sl})")

        # 3. Price reaches 615 (+2.3 ATR) -> Triggers TP2! SL locks in profit (500 + 0.75*90 = 567.5)
        strat._manage_position(615.0, strat.state.option_symbol)
        self.assertTrue(strat.state.tp2_hit)
        self.assertGreaterEqual(strat.state.current_sl, 567.5)
        print(f"  [Pass] TP2 Hit: SL ratcheted to Lock Profit ({strat.state.current_sl})")

        # 4. Tight breather (0.6 ATR = 30 pts): High is 615, tight trail should be 615 - 30 = 585.0
        self.assertGreaterEqual(strat.state.current_sl, 585.0)
        print(f"  [Pass] Tight Breather (0.6 ATR): SL dynamically trailed to {strat.state.current_sl}")

        # 5. Price pulls back to 584 -> Triggers Trailing SL exit!
        strat._manage_position(584.0, strat.state.option_symbol)
        strat.order_manager.place_order.assert_called()
        print("  [Pass] Pullback below tight breather triggered trailing SL exit successfully.")

    def test_sensex_ratchet_lifecycle(self):
        print("\n--- Testing Sensex Dynamic Ratchet Lifecycle ---")
        cfg = load_sx_cfg()
        strat = SensexORBStrategy(cfg)
        strat.order_manager = MagicMock()

        entry_p = 400.0
        atr = 60.0
        strat.state.trade_active = True
        strat.state.option_symbol = "SENSEX26SEP80000CE"
        strat.state.entry_premium = entry_p
        strat.state.option_atr = atr
        strat.state.current_sl = entry_p - (atr * 1.2) # 328.0
        strat.state.target_premium = 700.0 # High target to test dynamic ratchet
        strat.state.highest_opt = entry_p
        strat.state.sl_activated = False
        strat.state.tp1_hit = False
        strat.state.tp2_hit = False
        strat.state.last_step_trigger = None

        # 1. Price moves to 480 (+1.33 ATR) -> Under TP1 (1.8 ATR = 508), SL preserved
        strat._manage_position(480.0)
        self.assertFalse(strat.state.tp1_hit)
        self.assertEqual(strat.state.current_sl, 328.0)
        print("  [Pass] Under TP1: SL preserved at initial SL (328.0)")

        # 2. Price reaches 510 (+1.83 ATR) -> Triggers TP1! SL moves to Breakeven+ (400 + 0.15*60 = 409.0)
        strat._manage_position(510.0)
        self.assertTrue(strat.state.tp1_hit)
        self.assertTrue(strat.state.sl_activated)
        self.assertGreaterEqual(strat.state.current_sl, 409.0)
        print(f"  [Pass] TP1 Hit: SL ratcheted to Breakeven+ ({strat.state.current_sl})")

        # 3. Price reaches 555 (+2.58 ATR) -> Triggers TP2! SL locks in profit (400 + 0.75*108 = 481.0)
        strat._manage_position(555.0)
        self.assertTrue(strat.state.tp2_hit)
        self.assertGreaterEqual(strat.state.current_sl, 481.0)
        print(f"  [Pass] TP2 Hit: SL ratcheted to Lock Profit ({strat.state.current_sl})")

        # 4. Tight breather (0.6 ATR = 36 pts): High is 555, tight trail should be 555 - 36 = 519.0
        self.assertGreaterEqual(strat.state.current_sl, 519.0)
        print(f"  [Pass] Tight Breather (0.6 ATR): SL dynamically trailed to {strat.state.current_sl}")

        # 5. Price pulls back to 518 -> Triggers Trailing SL exit!
        strat._manage_position(518.0)
        strat.order_manager.close_market_order.assert_called()
        print("  [Pass] Pullback below tight breather triggered trailing SL exit successfully.")

if __name__ == '__main__':
    unittest.main()
