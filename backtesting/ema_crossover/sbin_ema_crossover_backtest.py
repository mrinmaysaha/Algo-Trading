import os
import sys
import types
from datetime import datetime, timedelta
from pathlib import Path

# --- VectorBT Compatibility Patch ---
t_err = types.ModuleType('telegram.error')
t_err.Unauthorized = Exception
t_err.ChatMigrated = Exception
sys.modules['telegram.error'] = t_err

import numpy as np
import pandas as pd
import vectorbt as vbt
from dotenv import find_dotenv, load_dotenv

# Try importing openstatz
try:
    import openstatz as ostz
    HAS_OPENSTATZ = True
except ImportError:
    HAS_OPENSTATZ = False

# Try importing openalgo
try:
    from openalgo import api, ta
except ImportError:
    print("OpenAlgo SDK not found. Install via: pip install openalgo")
    sys.exit(1)

# --- Load Environment ---
load_dotenv(find_dotenv(), override=False)

# --- Configuration ---
SYMBOL = "SBIN"
EXCHANGE = "NSE"
INTERVAL = "15m"
LOOKBACK_DAYS = 60
INIT_CASH = 100_000
ALLOCATION = 0.95  # 95% capital allocation

# Strategy Parameters
EMA_FAST_PERIOD = 10
EMA_SLOW_PERIOD = 20

# Costs (Indian Delivery / Equity: STT + Statutory + Rs 20 Brokerage)
FEES = 0.00111          # 0.111% total statutory charges
FIXED_FEES = 20.0       # Rs 20 per order

BENCHMARK_SYMBOL = "NIFTY"
BENCHMARK_EXCHANGE = "NSE_INDEX"

print("=" * 60)
print(f"Running VectorBT Backtest: EMA Crossover ({SYMBOL} - {INTERVAL})")
print("=" * 60)

# --- Fetch Data ---
api_key = os.getenv("OPENALGO_API_KEY", "")
host = os.getenv("HOST_SERVER") or os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000")

client = api(api_key=api_key, host=host)

end_date = datetime.now().date()
start_date = end_date - timedelta(days=LOOKBACK_DAYS)

print(f"Fetching historical data for {SYMBOL} from {start_date} to {end_date}...")
try:
    df = client.history(
        symbol=SYMBOL,
        exchange=EXCHANGE,
        interval=INTERVAL,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
    )
except Exception as e:
    print(f"[ERROR] Failed to fetch historical data for {SYMBOL}: {e}")
    sys.exit(1)

if isinstance(df, dict) or df is None or getattr(df, 'empty', True) or "close" not in df.columns:
    print(f"[ERROR] No valid data returned for {SYMBOL}. Ensure OpenAlgo server is running and master contracts are loaded.")
    sys.exit(1)

# Format Index
if "timestamp" in df.columns:
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("timestamp")
else:
    df.index = pd.to_datetime(df.index)

df = df.sort_index()
if df.index.tz is not None:
    df.index = df.index.tz_convert(None)

close = df["close"]

# --- Compute Indicators using openalgo.ta ---
ema_fast = ta.ema(close, EMA_FAST_PERIOD)
ema_slow = ta.ema(close, EMA_SLOW_PERIOD)

# Signal logic: Fast EMA crossing above Slow EMA -> BUY, crossing below -> SELL
buy_raw = ta.crossover(ema_fast, ema_slow)
sell_raw = ta.crossunder(ema_fast, ema_slow)

# Convert to pd.Series before fillna & exrem
entries_s = pd.Series(buy_raw, index=close.index).fillna(False).astype(bool)
exits_s = pd.Series(sell_raw, index=close.index).fillna(False).astype(bool)

# Signal cleaning with ta.exrem
entries = ta.exrem(entries_s, exits_s)
exits = ta.exrem(exits_s, entries_s)

# --- VectorBT Portfolio Simulation ---
pf = vbt.Portfolio.from_signals(
    close,
    entries=entries,
    exits=exits,
    init_cash=INIT_CASH,
    size=ALLOCATION,
    size_type="percent",
    fees=FEES,
    fixed_fees=FIXED_FEES,
    direction="longonly",
    min_size=1,
    size_granularity=1,
    freq=INTERVAL,
)

# --- Fetch Benchmark Data (NIFTY) ---
print(f"Fetching benchmark data ({BENCHMARK_SYMBOL})...")
try:
    df_bench = client.history(
        symbol=BENCHMARK_SYMBOL,
        exchange=BENCHMARK_EXCHANGE,
        interval=INTERVAL,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
    )
    if "timestamp" in df_bench.columns:
        df_bench["timestamp"] = pd.to_datetime(df_bench["timestamp"])
        df_bench = df_bench.set_index("timestamp")
    else:
        df_bench.index = pd.to_datetime(df_bench.index)
    df_bench = df_bench.sort_index()
    if df_bench.index.tz is not None:
        df_bench.index = df_bench.index.tz_convert(None)
    bench_close = df_bench["close"].reindex(close.index).ffill().bfill()
    pf_bench = vbt.Portfolio.from_holding(bench_close, init_cash=INIT_CASH, fees=FEES, freq=INTERVAL)
except Exception as e:
    print(f"[WARNING] Could not fetch benchmark: {e}. Using SBIN Buy & Hold as benchmark.")
    pf_bench = vbt.Portfolio.from_holding(close, init_cash=INIT_CASH, fees=FEES, freq=INTERVAL)

# --- Print Backtest Metrics ---
print("\n" + "=" * 60)
print("VECTORBT PERFORMANCE STATS")
print("=" * 60)
print(pf.stats())

# --- Strategy vs Benchmark Comparison ---
comparison = pd.DataFrame(
    {
        "EMA Strategy (SBIN)": [
            f"{pf.total_return() * 100:.2f}%",
            f"{pf.sharpe_ratio():.2f}",
            f"{pf.sortino_ratio():.2f}",
            f"{pf.max_drawdown() * 100:.2f}%",
            f"{pf.trades.win_rate() * 100:.1f}%" if len(pf.trades) > 0 else "N/A",
            f"{pf.trades.count()}",
            f"{pf.trades.profit_factor():.2f}" if len(pf.trades) > 0 else "N/A",
        ],
        f"Benchmark ({BENCHMARK_SYMBOL})": [
            f"{pf_bench.total_return() * 100:.2f}%",
            f"{pf_bench.sharpe_ratio():.2f}",
            f"{pf_bench.sortino_ratio():.2f}",
            f"{pf_bench.max_drawdown() * 100:.2f}%",
            "-",
            "-",
            "-",
        ],
    },
    index=[
        "Total Return",
        "Sharpe Ratio",
        "Sortino Ratio",
        "Max Drawdown",
        "Win Rate",
        "Total Trades",
        "Profit Factor",
    ],
)

print("\n" + "=" * 60)
print("STRATEGY VS BENCHMARK COMPARISON")
print("=" * 60)
print(comparison.to_string())

# --- Plain-Language Summary ---
print("\n" + "=" * 60)
print("PLAIN-LANGUAGE SUMMARY REPORT")
print("=" * 60)
print(f"* Total Return: {pf.total_return() * 100:.2f}% vs Benchmark {pf_bench.total_return() * 100:.2f}%")
print(f"* Total Trades: {pf.trades.count()} trades executed with a Win Rate of {pf.trades.win_rate() * 100:.1f}%")
print(f"* Maximum Drawdown: {pf.max_drawdown() * 100:.2f}% (Worst peak-to-trough capital dip)")
profit_factor_val = pf.trades.profit_factor() if len(pf.trades) > 0 else 0.0
print(f"* Profit Factor: {profit_factor_val:.2f} (Gross Profit / Gross Loss ratio)")

# --- Generate OpenStatz Interactive Dashboard Tearsheet ---
if HAS_OPENSTATZ:
    print("\n" + "=" * 60)
    print("GENERATING OPENSTATZ INTERACTIVE TEARSHEET")
    print("=" * 60)
    try:
        strategy_returns = pf.returns()
        if strategy_returns.index.tz is not None:
            strategy_returns.index = strategy_returns.index.tz_convert(None)
        strategy_returns.name = "EMA 10/20 Crossover (SBIN 15m)"

        bench_returns = pf_bench.returns()
        if bench_returns.index.tz is not None:
            bench_returns.index = bench_returns.index.tz_convert(None)
        bench_returns = bench_returns.reindex(strategy_returns.index).fillna(0)
        bench_returns.name = f"Benchmark ({BENCHMARK_SYMBOL})"

        tearsheet_file = Path("backtesting/ema_crossover/sbin_ema_tearsheet.html").resolve()
        ostz.dashboard(
            strategy_returns,
            benchmark=bench_returns,
            output=str(tearsheet_file),
            title="SBIN EMA Crossover OpenStatz Tearsheet",
            open_browser=False,
        )
        print(f"[SUCCESS] OpenStatz Tearsheet saved to: file:///{tearsheet_file}")
    except Exception as e:
        print(f"[WARNING] OpenStatz tearsheet generation error: {e}")
