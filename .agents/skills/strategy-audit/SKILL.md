---
name: strategy-audit
description: Master universal strategy backtesting and portfolio concurrency audit engine across NSE, BSE, and MCX. Enforces 100% deterministic backtests, extracts parameter cards, and prevents multi-strategy risk stacking.
argument-hint: "[--all | --strategy <name> | --exchange <NSE|BSE|MCX> | --simulate-portfolio]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# OpenAlgo Master Strategy Audit Engine

This skill serves as the **single source of truth** for testing, auditing, and validating algorithmic trading strategies across **NSE, BSE, and MCX** in OpenAlgo.

## Core Mandates & Rules

1. **Zero Ad-Hoc Scripts:**
   Never write temporary, ad-hoc, handwritten backtest scripts that approximate strategy logic. Such approximations lead to divergent results and breaches of trust.
2. **Deterministic Source of Truth:**
   Always use `strategies/audit_engine.py` or the strategy's native `--mode backtest` entry point.
3. **Parameter Card Transparency:**
   Every audit must explicitly inspect and display the strategy's **Parameter Card** (Exchange, Underlyings, Lots, Timeframe, Active Sessions, SL/TP Multipliers, Trailing Stops, Statutory Costs).
4. **Universal Multi-Exchange Support:**
   - `NSE` (NFO Index Options): Post-Oct 2024 SEBI model (0.1% STT, Rs.40 brokerage, GST, exchange turnover).
   - `BSE` (BFO Sensex/Bankex Options): BSE statutory turnover model.
   - `MCX` (Commodities): Commodity Transaction Tax (CTT 0.0125%), MCX turnover fees, evening session hours (`16:00 - 23:25 IST`).
5. **Mutual Exclusion & Anti-Stacking:**
   Always enforce single-symbol mutual exclusion via `strategies/portfolio_supervisor.py` so multiple strategies cannot concurrently stack positions on the same underlying.

---

## Command Reference

### 1. Audit All Strategies Across All Exchanges
```bash
python strategies/audit_engine.py --all
```

### 2. Audit a Specific Strategy
```bash
python strategies/audit_engine.py --strategy Post10_Institutional_OB_VWAP
python strategies/audit_engine.py --strategy Sensex_3Min_ORB_Quant
python strategies/audit_engine.py --strategy liquid_sweep_options
python strategies/audit_engine.py --strategy Prime_Indicator_Scalper_Options
python strategies/audit_engine.py --strategy SMC_FVG_ZeroLag_Options
python strategies/audit_engine.py --strategy multi-commodity_strategy
```

### 3. Audit by Exchange
```bash
python strategies/audit_engine.py --exchange NSE
python strategies/audit_engine.py --exchange BSE
python strategies/audit_engine.py --exchange MCX
```

### 4. Run Multi-Strategy Portfolio Concurrency & Collision Simulation
```bash
python strategies/audit_engine.py --simulate-portfolio
```

---

## Universal Strategy Protocol for Any New Strategy

When creating a new strategy for any asset class or exchange:

1. **Expose Parameter Configuration:**
   Declare exchange, underlying symbols, lots, timeframe, session hours, SL/TP, and trailing rules.
2. **Implement Native `--mode backtest`:**
   Add CLI argument handling:
   ```python
   if __name__ == "__main__":
       import argparse
       parser = argparse.ArgumentParser()
       parser.add_argument("--mode", choices=["live", "backtest"], default="live")
       args = parser.parse_args()
       if args.mode == "backtest":
           run_duckdb_6m_backtest()
       else:
           # Live trading execution
   ```
3. **Register in `strategies/audit_engine.py`:**
   Add the strategy profile to `STRATEGY_REGISTRY` in `strategies/audit_engine.py`.
4. **Integrate Portfolio Supervisor Mutual Exclusion:**
   Before placing an entry order, check:
   ```python
   from strategies.portfolio_supervisor import is_symbol_locked, register_symbol_position, deregister_symbol_position
   
   locked, reason = is_symbol_locked(symbol, strategy_name)
   if locked:
       logger.warning("[PORTFOLIO LOCK] %s", reason)
       return
   ```
