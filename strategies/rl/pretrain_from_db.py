# =============================================================================
# OpenAlgo RL Pre-training: Seeds Policy Memory from Historical Sandbox Trades
# Reads db/sandbox.db to give the RL agent immediate memory before live markets
# =============================================================================

import sqlite3
import os
import sys
import numpy as np

_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_dir, "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

from strategies.rl.adaptive_policy import AdaptiveRLPolicy
from strategies.rl.reward_engine import RLRewardEngine

def pretrain_from_sandbox(db_path: str = "db/sandbox.db"):
    if not os.path.exists(db_path):
        print(f"Database {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Query completed/tracked positions from sandbox_positions
    cur.execute("""
        SELECT symbol, exchange, accumulated_realized_pnl
        FROM sandbox_positions
        WHERE accumulated_realized_pnl != 0
    """)
    rows = cur.fetchall()
    conn.close()

    if not rows:
        print("No completed positions found with realized PnL in sandbox_positions.")
        return

    reward_engine = RLRewardEngine()
    mcx_policy = AdaptiveRLPolicy(weights_file="strategies/rl/policy_weights.json", learning_rate=0.003)
    nse_policy = AdaptiveRLPolicy(weights_file="strategies/rl/nse_policy_weights.json", learning_rate=0.003)

    print(f"Found {len(rows)} historical completed positions. Seeding RL policy memory...")

    mcx_count, nse_count = 0, 0
    for sym, exch, pnl in rows:
        pnl_val = float(pnl or 0.0)
        is_loss = (pnl_val < 0)
        adx_val = 0.14 if is_loss else 0.32
        rel_vol = 0.6 if is_loss else 1.8
        upper_wick = 1.6 if is_loss else 0.3

        synthetic_state = np.array([
            0.5 if not is_loss else 0.1, 0.015, adx_val, 0.55,
            upper_wick, 0.2, 0.5, rel_vol, 0.0, 0.0, 0.0, 0.5
        ], dtype=np.float32)

        reward = reward_engine.compute_step_reward(
            delta_pnl_inr=pnl_val,
            max_adverse_excursion_inr=abs(pnl_val) * 0.8 if is_loss else 0.0,
            is_terminal_trade=True,
            traded_lot_size=1
        )

        act_code = 1
        if "MCX" in str(exch).upper() or any(c in str(sym) for c in ["GOLD", "SILVER", "CRUDE", "NATGAS"]):
            mcx_policy.learn_from_trade(synthetic_state, act_code, reward, symbol=sym)
            mcx_count += 1
        else:
            nse_policy.learn_from_trade(synthetic_state, act_code, reward, symbol=sym)
            nse_count += 1

    print(f"Pre-training complete! Seeded {mcx_count} MCX trades and {nse_count} NSE trades.")

if __name__ == "__main__":
    pretrain_from_sandbox()
