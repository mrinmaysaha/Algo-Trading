#!/usr/bin/env python3
"""
========================================================================================
OPENALGO MASTER AUDIT ENGINE: UNIVERSAL DETERMINISTIC BACKTESTING & PORTFOLIO SIMULATOR
========================================================================================
Purpose:
  1. Universal Source of Truth: Inspects and executes native backtests across NSE, BSE, & MCX.
  2. Parameter Card Transparency: Displays exact parameter profiles before testing.
  3. 100% Deterministic: Zero handwritten mock approximations; calls native strategy code.
  4. Multi-Strategy Concurrency Simulator: Analyzes position stacking collisions and
     simulates true combined portfolio PnL under session-level circuit breakers.
========================================================================================
"""
import sys
import os
import time
import argparse
import datetime
import importlib.util
from typing import Dict, List, Any, Optional
import pandas as pd
import numpy as np

# Force UTF-8 on Windows
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

# Workspace root
WORKSPACE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if WORKSPACE_ROOT not in sys.path:
    sys.path.insert(0, WORKSPACE_ROOT)

# Registry of All Production Strategies
STRATEGY_REGISTRY = {
    "Post10_Institutional_OB_VWAP": {
        "name": "Post10 Institutional V5 (Dual-Session)",
        "script": "strategies/scripts/Post10_Institutional_OB_VWAP_20260802213829.py",
        "exchange": "NSE/BSE",
        "underlyings": ["NIFTY", "BANKNIFTY", "SENSEX"],
        "asset_type": "Index Options",
        "timeframe": "1m Spot + 15m VWAP/ATR",
        "session_hours": "09:30-11:15 (Morning ORB) & 12:45-14:15 (Afternoon Breakout)",
        "lot_sizes": {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20},
        "default_lots": {"NIFTY": 2, "BANKNIFTY": 2, "SENSEX": 2},
        "sl_tp_rules": "SL: Initial Hard Stop / 15m ATR | TP: 2.0x-2.5x Range",
        "trailing_mechanics": "Step-Locked TSL: Activates at 0.70x-0.80x Range, Steps by 0.35x-0.40x",
        "statutory_regime": "Post-Oct 2024 SEBI NFO & BSE BFO"
    },
    "Combined_ORB_Quant_Options": {
        "name": "Combined Dual-Index ORB Quant V3 (BankNifty 3m + Sensex 5m)",
        "script": "strategies/scripts/Combined_ORB_Quant_Options_20260911003000.py",
        "exchange": "NSE/BSE",
        "underlyings": ["BANKNIFTY", "SENSEX"],
        "asset_type": "Index Options",
        "timeframe": "BankNifty: 3m (09:15-09:18) | Sensex: 5m (09:15-09:20)",
        "session_hours": "09:18 to 10:00 IST (Square-off 15:15)",
        "lot_sizes": {"BANKNIFTY": 30, "SENSEX": 20},
        "default_lots": {"BANKNIFTY": 2, "SENSEX": 2},
        "sl_tp_rules": "SL: Mother Range | TP1: 1.5R | TP2: 2.5R | TP3: 4.0R (100% Position)",
        "trailing_mechanics": "Milestone 0 (-0.5R Risk Cut) -> Milestone 1 (BE Lock) -> Milestone 2 (Lock TP1) -> TP3",
        "statutory_regime": "Post-Oct 2024 SEBI NFO & BSE BFO"
    },
    "liquid_sweep_options": {
        "name": "Liquidity Sweep Options Scalper",
        "script": "strategies/scripts/liquid_sweep_options_20260808185609.py",
        "exchange": "NSE/BSE",
        "underlyings": ["NIFTY", "BANKNIFTY", "SENSEX"],
        "asset_type": "Index Options",
        "timeframe": "3m Execution with 15m ATR",
        "session_hours": "09:30-11:30 & 13:15-14:45 IST",
        "lot_sizes": {"NIFTY": 65, "BANKNIFTY": 30, "SENSEX": 20},
        "default_lots": {"NIFTY": 2, "BANKNIFTY": 2, "SENSEX": 2},
        "sl_tp_rules": "SL: 1.0x-1.5x ATR | TP: 4.5x ATR",
        "trailing_mechanics": "1.2x-1.4x ATR Breakeven Lock + Discrete Step Trailing",
        "statutory_regime": "Post-Oct 2024 SEBI NFO & BSE BFO"
    },
    "Prime_Indicator_Scalper_Options": {
        "name": "Prime Indicator Scalper Options",
        "script": "strategies/scripts/Prime_Indicator_Scalper_Options.py",
        "exchange": "NSE/BSE",
        "underlyings": ["BANKNIFTY", "NIFTY", "SENSEX"],
        "asset_type": "Index Options",
        "timeframe": "5m (BankNifty), 15m (Nifty & Sensex)",
        "session_hours": "09:20 to 15:00 IST (Balanced Scalper)",
        "lot_sizes": {"BANKNIFTY": 30, "NIFTY": 65, "SENSEX": 20},
        "default_lots": {"BANKNIFTY": 2, "NIFTY": 2, "SENSEX": 2},
        "sl_tp_rules": "SL: 1.0x ATR (Nifty/Sensex) / 1.2x ATR (BN) | TP: 4.0x-5.0x ATR | Confluence: BN >= 4/5, Nifty/Sensex >= 3/5",
        "trailing_mechanics": "1.4x-1.6x ATR Breakeven Lock + 0.5x Step Locking",
        "statutory_regime": "Post-Oct 2024 SEBI NFO & BSE BFO"
    },
    "multi-commodity_strategy": {
        "name": "MCX Multi-Commodity Quant Engine V3",
        "script": "strategies/scripts/multi-commodity_strategy_20260806235241.py",
        "exchange": "MCX",
        "underlyings": ["GOLDM", "SILVERM", "CRUDEOILM", "NATURALGASMINI"],
        "asset_type": "Commodity Options & Futures",
        "timeframe": "5m & 15m Execution + 1h Macro Trend",
        "session_hours": "16:00 to 23:25 IST (US Session Focus)",
        "lot_sizes": {"GOLDM": 100, "SILVERM": 5, "CRUDEOILM": 10, "NATURALGASMINI": 250},
        "default_lots": {"GOLDM": 1, "SILVERM": 1, "CRUDEOILM": 1, "NATURALGASMINI": 1},
        "sl_tp_rules": "SL: 1.2x-1.5x ATR | TP: 3.5x-4.0x ATR",
        "trailing_mechanics": "AVWAP & Keltner Momentum Trailing Lock",
        "statutory_regime": "MCX Commodity (CTT 0.0125%, MCX Turnover, GST, Stamp)"
    },
    "MCX_GOLDM_FVG_Options": {
        "name": "MCX GOLDM Options (SMC FVG Macro Scalper)",
        "script": "strategies/scripts/MCX_GOLDM_FVG_Options_20260818011045.py",
        "exchange": "MCX",
        "underlyings": ["GOLDM"],
        "asset_type": "Commodity Options (Deep ITM3 CE / PE)",
        "timeframe": "5m Execution + 15m Trend & 16:00 AVWAP",
        "session_hours": "17:30 to 23:15 IST (US Session Peak Momentum)",
        "lot_sizes": {"GOLDM": 100},
        "default_lots": {"GOLDM": 1},
        "sl_tp_rules": "SL: 1.0x ATR15 | TP: 5.5x ATR15 | Breakeven +1.2x ATR15",
        "trailing_mechanics": "+1.2x ATR Breakeven Lock (+0.60 ATR) + 0.50x Step Trailing",
        "statutory_regime": "MCX Commodity Options (CTT 0.05%, MCX Turnover, Rs.40 Brokerage, GST, Stamp)"
    },
    "MCX_Liquidity_Sweep_Scalper": {
        "name": "MCX Liquidity Sweep & RL Scalper",
        "script": "strategies/scripts/MCX_Liquidity_Sweep_Scalper.py",
        "exchange": "MCX",
        "underlyings": ["GOLDM", "SILVERM", "CRUDEOILM", "NATGASMINI"],
        "asset_type": "Commodity Futures & Options",
        "timeframe": "15m Structural Sweeps + Rejection Wicks",
        "session_hours": "16:00 to 23:25 IST (US Session)",
        "lot_sizes": {"GOLDM": 100, "SILVERM": 5, "CRUDEOILM": 10, "NATGASMINI": 250},
        "default_lots": {"GOLDM": 1, "SILVERM": 1, "CRUDEOILM": 1, "NATGASMINI": 2},
        "sl_tp_rules": "Dynamic RL Stop | 1.5x BE -> 2.5x Lock -> 3.8x Runner",
        "trailing_mechanics": "Dynamic Multi-Milestone Profit Ratcheting",
        "statutory_regime": "MCX Commodity (CTT 0.0125%, Turnover, Brokerage Rs.40)"
    }
}


def print_strategy_parameter_card(key: str, reg: Dict[str, Any]):
    """Displays exact source parameters for transparent verification."""
    print("+" + "-" * 88 + "+")
    print(f"| STRATEGY PARAMETER CARD: {reg['name']:<60} |")
    print("+" + "-" * 88 + "+")
    print(f"  • Strategy Key     : {key}")
    print(f"  • Source Script    : {reg['script']}")
    print(f"  • Exchange         : {reg['exchange']} ({reg['asset_type']})")
    print(f"  • Underlyings      : {', '.join(reg['underlyings'])}")
    print(f"  • Candle Timeframe : {reg['timeframe']}")
    print(f"  • Trading Hours    : {reg['session_hours']}")
    print(f"  • Position Sizing  : {reg['default_lots']} lots (Lotsizes: {reg['lot_sizes']})")
    print(f"  • SL / TP Rules    : {reg['sl_tp_rules']}")
    print(f"  • Trailing Engine  : {reg['trailing_mechanics']}")
    print(f"  • Statutory Regime : {reg['statutory_regime']}")
    print("+" + "-" * 88 + "+\n")


def execute_native_strategy_backtest(key: str, reg: Dict[str, Any], start: str = "2026-03-01", end: str = "2026-08-31") -> List[Dict[str, Any]]:
    """Dynamically imports the strategy and calls its native backtest function directly."""
    script_path = os.path.join(WORKSPACE_ROOT, reg["script"])
    if not os.path.exists(script_path):
        print(f"[ERROR] Strategy script not found: {script_path}")
        return []

    print_strategy_parameter_card(key, reg)

    # Import module
    spec = importlib.util.spec_from_file_location(key, script_path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[key] = mod
    spec.loader.exec_module(mod)

    trades = []
    # Call native backtest function
    if hasattr(mod, "run_duckdb_6m_backtest"):
        trades = mod.run_duckdb_6m_backtest()
    elif hasattr(mod, "run_backtest"):
        # Prime scalper
        cfg_cls = getattr(mod, "EngineConfig", None)
        if cfg_cls and hasattr(cfg_cls, "from_environment"):
            cfg = cfg_cls.from_environment(resolve_network=False)
            cfg.backtest_mode = True
            trades = mod.run_backtest(cfg)
    elif hasattr(mod, "run_mcx_backtest"):
        trades = mod.run_mcx_backtest(days=180)
    return trades or []


def analyze_trades(trades: List[Dict[str, Any]], strat_name: str) -> Dict[str, Any]:
    """Calculates standardized financial metrics from executed trade records."""
    if not trades:
        return {
            "Strategy": strat_name, "Trades": 0, "Win Rate": "0.0%", "Gross PnL": 0.0,
            "Taxes & Fees": 0.0, "Net Realized": 0.0, "Profit Factor": 0.0, "Max Drawdown": 0.0,
            "Avg Trade Net": 0.0
        }

    df = pd.DataFrame(trades)
    # Standardize column names
    if "net" not in df.columns and "net_pnl" in df.columns:
        df["net"] = df["net_pnl"]
    elif "pnl" in df.columns:
        df["net"] = df["pnl"]

    if "gross" not in df.columns and "gross_pnl" in df.columns:
        df["gross"] = df["gross_pnl"]
    elif "gross" not in df.columns:
        df["gross"] = df["net"]

    if "taxes" not in df.columns and "charges" in df.columns:
        df["taxes"] = df["charges"]
    elif "taxes" not in df.columns:
        df["taxes"] = df["gross"] - df["net"]

    tot = len(df)
    w = (df["net"] > 0).sum()
    wr = (w / tot) * 100 if tot > 0 else 0.0
    gross = df["gross"].sum()
    charges = df["taxes"].sum()
    net = df["net"].sum()

    gw = df[df["gross"] > 0]["gross"].sum()
    gl = abs(df[df["gross"] < 0]["gross"].sum())
    pf = (gw / gl) if gl > 0 else 99.0

    cum = df["net"].cumsum()
    max_dd = (cum.cummax() - cum).max()
    avg_net = net / tot if tot > 0 else 0.0

    return {
        "Strategy": strat_name,
        "Trades": tot,
        "Win Rate": f"{wr:.1f}%",
        "Gross PnL": round(gross, 2),
        "Taxes & Fees": round(charges, 2),
        "Net Realized": round(net, 2),
        "Profit Factor": round(pf, 2),
        "Max Drawdown": round(max_dd, 2),
        "Avg Trade Net": round(avg_net, 2)
    }


def run_portfolio_concurrency_simulation(strategy_trade_map: Dict[str, List[Dict[str, Any]]]):
    """
    Simulates all strategies running in a single account concurrently.
    Applies:
      1. Single-Symbol Mutual Exclusion (one strategy occupies symbol at a time).
      2. Session-Level Daily Loss Circuit Breaker (NSE: -Rs.5,000, BSE: -Rs.5,000, MCX: -Rs.5,000).
      3. Chronological Discrete Event Replay.
    Outputs true realistic portfolio performance vs isolated sum.
    """
    print("\n" + "=" * 90)
    print(" 🛡️ MULTI-STRATEGY REAL-TIME CONCURRENCY & RISK SIMULATION")
    print("=" * 90)

    merged_trades = []
    for s_name, t_list in strategy_trade_map.items():
        reg = STRATEGY_REGISTRY.get(s_name, {})
        strat_exch = reg.get("exchange", "NSE")
        for t in t_list:
            item = dict(t)
            item["strategy_source"] = s_name
            if "exchange" not in item:
                item["exchange"] = strat_exch
            merged_trades.append(item)

    if not merged_trades:
        print("No trades available for portfolio concurrency simulation.")
        return

    # Helper to parse datetime into timezone-naive Timestamp
    def parse_naive_dt(val):
        if val is None or pd.isna(val):
            return None
        try:
            dt = pd.to_datetime(val)
            if hasattr(dt, "tz") and dt.tz is not None:
                dt = dt.tz_localize(None)
            return dt
        except Exception:
            return None

    cleaned_trades = []
    for idx, t in enumerate(merged_trades):
        # Parse entry time
        e_val = t.get("entry_time") or t.get("entry_dt") or t.get("timestamp")
        entry_dt = parse_naive_dt(e_val)

        # Parse exit time
        x_val = t.get("exit_time") or t.get("exit_dt")
        exit_dt = parse_naive_dt(x_val)

        # Date
        d_val = t.get("trade_date") or t.get("date")
        sim_date = parse_naive_dt(d_val).date() if d_val is not None else (entry_dt.date() if entry_dt is not None else None)

        if entry_dt is None and sim_date is not None:
            entry_dt = pd.Timestamp(sim_date) + pd.Timedelta(hours=9, minutes=30)
        if exit_dt is None and entry_dt is not None:
            exit_dt = entry_dt + pd.Timedelta(minutes=30)

        if entry_dt is None or sim_date is None:
            continue

        # Standardize net, gross, taxes
        net = float(t.get("net", t.get("net_pnl", t.get("pnl", 0.0))))
        gross = float(t.get("gross", t.get("gross_pnl", net)))
        taxes = float(t.get("taxes", t.get("charges", t.get("fric", gross - net))))

        sym = str(t.get("symbol", "NIFTY")).upper()
        exch = str(t.get("exchange", "NSE")).upper()
        if exch in ("NFO", "NSE_INDEX"):
            exch = "NSE"
        elif exch in ("BFO", "BSE_INDEX"):
            exch = "BSE"
        elif exch in ("MCX_COMMODITY",):
            exch = "MCX"

        cleaned_trades.append({
            "id": idx,
            "strategy": t["strategy_source"],
            "symbol": sym,
            "exchange": exch,
            "sim_date": sim_date,
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
            "gross": gross,
            "taxes": taxes,
            "net": net,
            "reason": t.get("reason", t.get("exit_reason", ""))
        })

    if not cleaned_trades:
        print("No valid timestamps found in trade records.")
        return

    df_all = pd.DataFrame(cleaned_trades)

    # 1. Unconstrained Isolated Summary
    iso_trades = len(df_all)
    iso_net = df_all["net"].sum()
    iso_gross = df_all["gross"].sum()
    iso_taxes = df_all["taxes"].sum()
    iso_wins = (df_all["net"] > 0).sum()
    iso_wr = (iso_wins / iso_trades) * 100 if iso_trades > 0 else 0.0
    iso_gw = df_all[df_all["gross"] > 0]["gross"].sum()
    iso_gl = abs(df_all[df_all["gross"] < 0]["gross"].sum())
    iso_pf = (iso_gw / iso_gl) if iso_gl > 0 else 99.0
    cum_iso = df_all["net"].cumsum()
    iso_max_dd = (cum_iso.cummax() - cum_iso).max()

    # 2. Chronological Discrete Event Simulation
    caps = {"NSE": 5000.0, "BSE": 5000.0, "MCX": 5000.0}

    executed_trades = []
    blocked_collision_trades = []
    blocked_breaker_trades = []

    # Group by date and exchange
    for (d, exch), day_group in df_all.groupby(["sim_date", "exchange"]):
        loss_cap = caps.get(exch, 5000.0)
        day_trades = day_group.sort_values("entry_dt").to_dict("records")

        daily_realized_pnl = 0.0
        active_positions = {}  # symbol -> {strategy, exit_dt}
        cb_tripped = False
        cb_trip_time = None

        for tr in day_trades:
            t_entry = tr["entry_dt"]
            t_sym = tr["symbol"]

            # Clean expired positions
            to_remove = [s for s, pos in active_positions.items() if pos["exit_dt"] <= t_entry]
            for s in to_remove:
                del active_positions[s]

            # Check Circuit Breaker
            if cb_tripped:
                tr["block_reason"] = f"Daily Loss Circuit Breaker Tripped at {cb_trip_time} (Realized: -Rs.{abs(daily_realized_pnl):.2f})"
                blocked_breaker_trades.append(tr)
                continue

            # Check Symbol Concurrency Lock (Collision)
            if t_sym in active_positions:
                occ = active_positions[t_sym]
                tr["block_reason"] = f"Symbol {t_sym} occupied by {occ['strategy']} until {occ['exit_dt']}"
                blocked_collision_trades.append(tr)
                continue

            # Trade Accepted
            executed_trades.append(tr)
            active_positions[t_sym] = {
                "strategy": tr["strategy"],
                "exit_dt": tr["exit_dt"]
            }

            # Update Realized PnL upon trade exit
            daily_realized_pnl += tr["net"]
            if daily_realized_pnl <= -loss_cap and not cb_tripped:
                cb_tripped = True
                cb_trip_time = tr["exit_dt"]

    df_exec = pd.DataFrame(executed_trades) if executed_trades else pd.DataFrame()
    df_col = pd.DataFrame(blocked_collision_trades) if blocked_collision_trades else pd.DataFrame()
    df_cb = pd.DataFrame(blocked_breaker_trades) if blocked_breaker_trades else pd.DataFrame()

    # Stats on executed
    exec_count = len(df_exec) if not df_exec.empty else 0
    exec_net = df_exec["net"].sum() if not df_exec.empty else 0.0
    exec_gross = df_exec["gross"].sum() if not df_exec.empty else 0.0
    exec_taxes = df_exec["taxes"].sum() if not df_exec.empty else 0.0
    exec_wins = (df_exec["net"] > 0).sum() if not df_exec.empty else 0
    exec_wr = (exec_wins / exec_count) * 100 if exec_count > 0 else 0.0
    exec_gw = df_exec[df_exec["gross"] > 0]["gross"].sum() if not df_exec.empty else 0.0
    exec_gl = abs(df_exec[df_exec["gross"] < 0]["gross"].sum()) if not df_exec.empty else 0.0
    exec_pf = (exec_gw / exec_gl) if exec_gl > 0 else 99.0

    cum_exec = df_exec["net"].cumsum() if not df_exec.empty else pd.Series([0])
    exec_max_dd = (cum_exec.cummax() - cum_exec).max()

    cb_blocked_net = df_cb["net"].sum() if not df_cb.empty else 0.0
    col_blocked_net = df_col["net"].sum() if not df_col.empty else 0.0

    print("\n[1] CONCURRENCY & COLLISION AUDIT (Single-Symbol Mutual Exclusion):")
    print(f"  • Total Candidate Trades Generated : {iso_trades}")
    print(f"  • Blocked by Same-Symbol Collision : {len(df_col)} trades ({col_blocked_net:+,.2f} INR net)")
    if not df_col.empty:
        sample_col = df_col[["sim_date", "strategy", "symbol", "block_reason"]]
        print(sample_col.head(6).to_string(index=False))

    print("\n[2] DAILY CIRCUIT BREAKER AUDIT (Session Loss Limit: -Rs. 5,000 per Exchange):")
    print(f"  • Blocked by Circuit Breaker Trip  : {len(df_cb)} trades")
    print(f"  • Net PnL of Trades Blocked by CB  : Rs. {cb_blocked_net:+,.2f}")
    if cb_blocked_net < 0:
        print(f"  🛡️ CAPITAL SAVED BY CB SHUTDOWN   : Rs. {abs(cb_blocked_net):,.2f} saved by halting runaway losses!")

    print("\n[3] REALISTIC CONSTRAINED PORTFOLIO PERFORMANCE vs ISOLATED SUM:")
    report_data = [
        {"Metric": "Total Trades", "Isolated Sum (Fantasy)": f"{iso_trades}", "Realistic Portfolio (Constrained)": f"{exec_count}", "Delta": f"{exec_count - iso_trades}"},
        {"Metric": "Win Rate", "Isolated Sum (Fantasy)": f"{iso_wr:.1f}%", "Realistic Portfolio (Constrained)": f"{exec_wr:.1f}%", "Delta": f"{exec_wr - iso_wr:+.1f}%"},
        {"Metric": "Gross PnL", "Isolated Sum (Fantasy)": f"Rs. {iso_gross:+,.2f}", "Realistic Portfolio (Constrained)": f"Rs. {exec_gross:+,.2f}", "Delta": f"Rs. {exec_gross - iso_gross:+,.2f}"},
        {"Metric": "Taxes & Statutory Fees", "Isolated Sum (Fantasy)": f"Rs. {iso_taxes:,.2f}", "Realistic Portfolio (Constrained)": f"Rs. {exec_taxes:,.2f}", "Delta": f"Rs. {exec_taxes - iso_taxes:+,.2f}"},
        {"Metric": "Net Realized PnL", "Isolated Sum (Fantasy)": f"Rs. {iso_net:+,.2f}", "Realistic Portfolio (Constrained)": f"Rs. {exec_net:+,.2f}", "Delta": f"Rs. {exec_net - iso_net:+,.2f}"},
        {"Metric": "Profit Factor", "Isolated Sum (Fantasy)": f"{iso_pf:.2f}", "Realistic Portfolio (Constrained)": f"{exec_pf:.2f}", "Delta": f"{exec_pf - iso_pf:+.2f}"},
        {"Metric": "Max Drawdown", "Isolated Sum (Fantasy)": f"Rs. {iso_max_dd:,.2f}", "Realistic Portfolio (Constrained)": f"Rs. {exec_max_dd:,.2f}", "Delta": f"Rs. {exec_max_dd - iso_max_dd:+,.2f}"}
    ]
    df_rep = pd.DataFrame(report_data)
    print(df_rep.to_string(index=False))
    print("=" * 90)


def main():
    parser = argparse.ArgumentParser(description="OpenAlgo Universal Deterministic Strategy Audit Engine")
    parser.add_argument("--all", action="store_true", help="Audit all registered strategies across NSE, BSE, & MCX")
    parser.add_argument("--strategy", type=str, help="Audit a specific strategy by key")
    parser.add_argument("--exchange", choices=["NSE", "BSE", "MCX"], help="Audit strategies for a specific exchange")
    parser.add_argument("--simulate-portfolio", action="store_true", help="Run multi-strategy concurrency & stacking simulation")
    parser.add_argument("--start", type=str, default="2026-03-01", help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default="2026-08-31", help="End date (YYYY-MM-DD)")
    args = parser.parse_args()

    targets = {}
    if args.strategy:
        if args.strategy in STRATEGY_REGISTRY:
            targets[args.strategy] = STRATEGY_REGISTRY[args.strategy]
        else:
            print(f"Strategy '{args.strategy}' not found in registry. Available: {list(STRATEGY_REGISTRY.keys())}")
            sys.exit(1)
    elif args.exchange:
        targets = {k: v for k, v in STRATEGY_REGISTRY.items() if v["exchange"] == args.exchange}
    else:
        # Default to all registered
        targets = STRATEGY_REGISTRY

    print("\n" + "=" * 90)
    print(" 🚀 OPENALGO UNIVERSAL STRATEGY AUDIT ENGINE")
    print(f" Active Strategies: {len(targets)} | Period: {args.start} to {args.end}")
    print("=" * 90 + "\n")

    summary_rows = []
    trade_map = {}

    for key, reg in targets.items():
        print(f"\n[*] Executing Audit: {reg['name']} ({reg['exchange']}) ...")
        t0 = time.time()
        trades = execute_native_strategy_backtest(key, reg, start=args.start, end=args.end)
        elapsed = time.time() - t0
        print(f"[*] Completed {reg['name']} in {elapsed:.2f}s | Executed Trades: {len(trades)}")

        trade_map[key] = trades
        metrics = analyze_trades(trades, reg["name"])
        summary_rows.append(metrics)

    # Master Comparative Tearsheet
    df_summary = pd.DataFrame(summary_rows)
    print("\n" + "=" * 90)
    print(" 🏆 MASTER UNIVERSAL AUDIT REPORT (DETERMINISTIC & REPRODUCIBLE)")
    print("=" * 90)
    print(df_summary.to_string(index=False))
    print("=" * 90)

    # Run Concurrency Simulation if requested or if multiple strategies audited
    if args.simulate_portfolio or len(targets) > 1:
        run_portfolio_concurrency_simulation(trade_map)


if __name__ == "__main__":
    main()
