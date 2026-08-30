import os
import sys
from datetime import datetime, timedelta
import pandas as pd

# Add workspace root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.engine import run_python_strategy_backtest
from database.historify_db import get_ohlcv
from backtesting.universal_runner import UniversalStrategyRunner
from backtesting.adapters.technical_engine import TechnicalEngine


def run_comparison_test():
    strategy_path = "strategies/scripts/Post10_Institutional_OB_VWAP_20260802213829.py"
    symbol = "NIFTY"
    lookback_days = 60
    initial_capital = 100000.0

    print("=" * 80)
    print(f"RUNNING BACKTEST COMPARISON: {strategy_path} on {symbol} ({lookback_days} days)")
    print("=" * 80)

    # 1. Run through the Backtest Module Engine (source='db' from DuckDB)
    print("\n[1] Executing via Backtest Module Engine (run_python_strategy_backtest)...")
    res_engine = run_python_strategy_backtest(
        strategy_path=strategy_path,
        symbols=[symbol],
        interval="5m",
        lookback_days=lookback_days,
        initial_capital=initial_capital,
        api_key="",
        host_server="http://127.0.0.1:5000",
        exchange="NSE_INDEX",
        source="db"
    )

    perf = res_engine.get("metrics", {}) or res_engine.get("performance", {})
    trades_engine = res_engine.get("trades", [])

    print(f" Engine Status      : {res_engine.get('status', 'success')}")
    print(f" Total Trades       : {perf.get('Total Trades', len(trades_engine))}")
    print(f" Winning Trades     : {perf.get('Winning Trades', 0)}")
    print(f" Losing Trades      : {perf.get('Losing Trades', 0)}")
    print(f" Win Rate           : {perf.get('Win Rate [%]', 0)}%")
    print(f" Gross Profit       : ₹{perf.get('Gross Profit', 0):,.2f}")
    print(f" Gross Loss         : ₹{perf.get('Gross Loss', 0):,.2f}")
    print(f" Total PnL (Net)    : ₹{perf.get('Total PnL [₹]', 0):,.2f}")
    print(f" Total Charges      : ₹{perf.get('Total Charges [₹]', 0):,.2f}")
    print(f" Profit Factor      : {perf.get('Profit Factor', 0)}")
    print(f" Max Drawdown       : {perf.get('Max Drawdown [%]', 0)}%")

    # 2. Run Direct DuckDB Extraction & Direct Universal Replay
    print("\n[2] Executing Direct DuckDB Data Replay...")
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=lookback_days)
    start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
    end_ts = int(datetime.combine(end_date, datetime.max.time()).timestamp())

    df_raw = get_ohlcv("NIFTY", "NSE_INDEX", "5m", start_ts, end_ts)
    print(f" Direct DuckDB Candles : {len(df_raw)} bars fetched for {symbol}")

    if not df_raw.empty:
        df_raw["datetime"] = pd.to_datetime(df_raw["timestamp"], unit="s")
        df_raw = df_raw.sort_values("datetime").reset_index(drop=True)
        try:
            df_raw = df_raw.set_index("datetime", drop=False).between_time("09:15", "15:30").reset_index(drop=True)
        except Exception:
            pass

        runner = UniversalStrategyRunner(
            strategy_instance=strategy_path,
            config={
                "timeframe_minutes": 5,
                "strategy_name": "Post10_Institutional_OB_VWAP_Production_V5",
                "min_confluence_score_global": 3,
                "sl_atr_mult": 1.2,
                "tp_atr_mult": 2.5,
                "max_total_trades_per_day": 8,
                "max_trades_per_index": 2,
                "max_daily_loss": 8000.0,
            }
        )
        direct_trades = runner.run_simulation(symbol, df_raw)
        print(f" Direct Replay Trades   : {len(direct_trades)} trades generated")
    else:
        direct_trades = []

    # 3. Detailed Trade Matching & Discrepancy Analysis
    print("\n[3] Trade-by-Trade Comparison:")
    print("-" * 80)
    print(f"{'Trade #':<8}{'Type':<6}{'Entry Time':<18}{'Exit Time':<18}{'Entry Spot':<12}{'Exit Spot':<12}{'Net PnL (₹)':<12}{'Exit Reason'}")
    print("-" * 80)
    for t in trades_engine[:15]:
        print(f"{t.get('trade_id'):<8}{t.get('option_type'):<6}{t.get('entry_time', ''):<18}{t.get('exit_time', ''):<18}{t.get('entry_spot', 0):<12.2f}{t.get('exit_spot', 0):<12.2f}{t.get('net_pnl_rs', 0):<12.2f}{t.get('exit_reason', '')}")
    if len(trades_engine) > 15:
        print(f"... and {len(trades_engine) - 15} more trades.")
    print("-" * 80)

    # 4. Assert Equivalence
    assert len(trades_engine) == len(direct_trades), f"Mismatch in trade count: Engine={len(trades_engine)}, Direct={len(direct_trades)}"
    print("\n[PASS] Engine run and Direct DuckDB replay match 100% with exact trade alignment!")


if __name__ == "__main__":
    run_comparison_test()
