# =============================================================================
# OpenAlgo Reinforcement Learning: Policy Inspector & Diagnostics
# Run anytime via: python strategies/rl/inspect_policy.py
# Provides full transparency into what the RL agent has learned from past trades
# =============================================================================

import os
import sys
import json
import csv
import numpy as np

# Adjust path for imports
_dir = os.path.dirname(os.path.abspath(__file__))
_root = os.path.abspath(os.path.join(_dir, "..", ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

from strategies.rl.adaptive_policy import AdaptiveRLPolicy

def inspect_policy(name: str, weights_path: str):
    print("=" * 80)
    print(f"  RL POLICY AUDIT: {name.upper()}")
    print(f"  Weights File: {weights_path}")
    print("=" * 80)

    if not os.path.exists(weights_path):
        print(f"  ⚠️  Weights file not found yet. The model starts at baseline (neutral).")
        policy = AdaptiveRLPolicy(weights_file=weights_path)
    else:
        policy = AdaptiveRLPolicy(weights_file=weights_path)
        print(f"  • Total Trade Experiences Learned : {policy.total_experiences}")
        print(f"  • Winning Trades Reinforced        : {policy.win_count}")
        print(f"  • Losing / Choppy Regimes Penalized : {policy.loss_count}")
        loss_ratio = (policy.loss_count / max(1, policy.total_experiences)) * 100
        print(f"  • Regime Penalty Frequency         : {loss_ratio:.1f}% of trades prompted risk tightening")

    print("\n" + "-" * 80)
    print("  SIMULATING MARKET REGIMES (HOW THE RL BRAIN DECIDES)")
    print("-" * 80)

    # 1. Regime A: Clean High-Volume Morning Trend (e.g. 09:45 AM / 18:30 IST)
    # Features: [EMA_dist=1.2, Volatility=0.015, ADX=0.38, RSI=0.62, UpperWick=0.1, LowerWick=0.4, Session=0.25, RelVol=2.2, Pos=0, PnL=0, Holding=0, Proximity=1.5]
    regime_trend = np.array([1.2, 0.015, 0.38, 0.62, 0.1, 0.4, 0.25, 2.2, 0.0, 0.0, 0.0, 1.5], dtype=np.float32)
    conv_trend, sl_trend, tp_trend = policy.predict(regime_trend)
    status_trend = "[APPROVED]" if conv_trend > 0.20 else "[VETOED]"

    # 2. Regime B: Midday Low-Volume Chop / Trap (e.g. 13:30 IST)
    # Features: [EMA_dist=0.1, Volatility=0.008, ADX=0.12, RSI=0.51, UpperWick=1.8, LowerWick=0.2, Session=0.60, RelVol=0.45, Pos=0, PnL=0, Holding=0, Proximity=0.2]
    regime_chop = np.array([0.1, 0.008, 0.12, 0.51, 1.8, 0.2, 0.60, 0.45, 0.0, 0.0, 0.0, 0.2], dtype=np.float32)
    conv_chop, sl_chop, tp_chop = policy.predict(regime_chop)
    status_chop = "[APPROVED]" if conv_chop > 0.20 else "[VETOED]"

    # 3. Regime C: Post-Squeeze Reversal Sweep with Rejection Wick
    regime_sweep = np.array([-0.8, 0.022, 0.29, 0.35, 0.2, 1.6, 0.45, 1.8, 0.0, 0.0, 0.0, 0.1], dtype=np.float32)
    conv_sweep, sl_sweep, tp_sweep = policy.predict(regime_sweep)
    status_sweep = "[APPROVED]" if conv_sweep > 0.20 else "[VETOED]"

    print(f"  1. Strong Morning Trend (ADX=38, Vol=2.2x)    : Conviction = {conv_trend:+.3f} | SL = {sl_trend:.1f}x ATR -> {status_trend}")
    print(f"  2. Midday Low-Volume Chop (ADX=12, Vol=0.4x)  : Conviction = {conv_chop:+.3f} | SL = {sl_chop:.1f}x ATR -> {status_chop}")
    print(f"  3. Reversal Liquidity Sweep (Lower Wick >35%) : Conviction = {conv_sweep:+.3f} | SL = {sl_sweep:.1f}x ATR -> {status_sweep}")

    print("\n" + "=" * 80 + "\n")

def show_recent_audit_trail(audit_file: str = "logs/rl_learning_audit.csv"):
    if not os.path.exists(audit_file):
        print("  [INFO] No audit logs recorded yet. Trades executed by the live bot will appear here.")
        return

    print("=" * 80)
    print("  LIVE LEARNING AUDIT TRAIL (LAST 5 EXECUTED ADJUSTMENTS)")
    print("=" * 80)
    with open(audit_file, "r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        if len(reader) <= 1:
            print("  No learning events logged yet.")
            return

        headers = reader[0]
        rows = reader[1:][-5:]
        print(f"  {headers[0]:<20} | {headers[1]:<10} | {headers[2]:<8} | {headers[3]:<12} | {headers[4]:<12} | {headers[5]}")
        print("  " + "-" * 76)
        for r in rows:
            print(f"  {r[0]:<20} | {r[1]:<10} | {r[2]:<8} | {r[3]:<12} | {r[4]:<12} | {r[5]}")
    print("=" * 80 + "\n")

def main():
    print("\n" + "#" * 80)
    print("       OPENALGO REINFORCEMENT LEARNING AUDIT & DIAGNOSTIC SYSTEM")
    print("#" * 80 + "\n")

    # 1. Inspect MCX Commodity Policy
    inspect_policy("MCX Commodities (GOLDM, SILVERM, CRUDEOILM, NATGASMINI)", "strategies/rl/policy_weights.json")

    # 2. Inspect NSE / BSE Index Policy
    inspect_policy("NSE / BSE Indices (NIFTY, BANKNIFTY, SENSEX)", "strategies/rl/nse_policy_weights.json")

    # 3. Show Audit Trail Log
    show_recent_audit_trail()

if __name__ == "__main__":
    main()
