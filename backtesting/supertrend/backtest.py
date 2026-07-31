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

# Try importing openalgo
try:
    from openalgo import api, ta
except ImportError:
    print("OpenAlgo SDK not found. Install via: pip install openalgo")
    sys.exit(1)

# --- Load Environment ---
load_dotenv(find_dotenv(), override=False)

# --- Configuration ---
SYMBOL = "NIFTY"
EXCHANGE = "NSE_INDEX"
INTERVAL = "5m"
LOOKBACK_DAYS = 30
INIT_CASH = 100_000
ALLOCATION = 0.95

# Strategy Parameters
ST_PERIOD = 7
ST_MULTIPLIER = 2.0
ADX_PERIOD = 14
ADX_MIN = 25
VOL_MA_PERIOD = 20
VOL_MIN_MULT = 1.2

# Costs
FEES = 0.0003          # 0.03%
FIXED_FEES = 20.0       # Rs 20 per order

print("=" * 60)
print(f"Running VectorBT Backtest: Supertrend ({SYMBOL} - {INTERVAL})")
print("=" * 60)

# --- Fetch Data ---
api_key = os.getenv("OPENALGO_API_KEY", "")
host = os.getenv("HOST_SERVER") or os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000")

client = api(api_key=api_key, host=host)

end_date = datetime.now().date()
start_date = end_date - timedelta(days=LOOKBACK_DAYS)

print(f"Fetching historical data from {start_date} to {end_date}...")
try:
    df = client.history(
        symbol=SYMBOL,
        exchange=EXCHANGE,
        interval=INTERVAL,
        start_date=start_date.strftime("%Y-%m-%d"),
        end_date=end_date.strftime("%Y-%m-%d"),
    )
except Exception as e:
    print(f"[ERROR] Failed to fetch historical data: {e}")
    sys.exit(1)

if isinstance(df, dict) or df is None or getattr(df, 'empty', True) or "close" not in df.columns:
    print("[ERROR] No valid data returned from OpenAlgo. Ensure OpenAlgo server is running and master contracts are loaded.")
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
high = df["high"]
low = df["low"]
volume = df.get("volume", pd.Series(1, index=df.index))

# --- Compute Supertrend using openalgo.ta ---
st_val, st_direction = ta.supertrend(high, low, close, period=ST_PERIOD, multiplier=ST_MULTIPLIER)
st_direction = pd.Series(st_direction, index=close.index)

# Signal Logic: Direction 1 = Bullish, -1 = Bearish
buy_raw = (st_direction == 1) & (st_direction.shift(1) != 1)
sell_raw = (st_direction == -1) & (st_direction.shift(1) != -1)

# Filter: ADX & Volume
try:
    adx_res = ta.adx(high, low, close, period=ADX_PERIOD)
    if isinstance(adx_res, tuple):
        adx_val = adx_res[0]
    elif isinstance(adx_res, pd.DataFrame):
        adx_val = adx_res["ADX"] if "ADX" in adx_res.columns else adx_res.iloc[:, 0]
    else:
        adx_val = adx_res
    adx_filter = pd.Series(adx_val >= ADX_MIN, index=close.index)
except Exception:
    adx_filter = pd.Series(True, index=close.index)

vol_ma = ta.sma(volume, VOL_MA_PERIOD)
vol_filter = pd.Series(volume >= (vol_ma * VOL_MIN_MULT), index=close.index)

entries_raw = buy_raw & adx_filter & vol_filter
exits_raw = sell_raw

# Convert to pd.Series before fillna & exrem
entries_s = pd.Series(entries_raw, index=close.index).fillna(False).astype(bool)
exits_s = pd.Series(exits_raw, index=close.index).fillna(False).astype(bool)

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
    freq=INTERVAL,
)

# --- Print Backtest Metrics ---
print("\n" + "=" * 60)
print("VECTORBT PERFORMANCE STATS")
print("=" * 60)
print(pf.stats())

# --- Strategy vs Benchmark Comparison ---
pf_bench = vbt.Portfolio.from_holding(close, init_cash=INIT_CASH, fees=FEES, freq=INTERVAL)

comparison = pd.DataFrame(
    {
        "Supertrend Strategy": [
            f"{pf.total_return() * 100:.2f}%",
            f"{pf.sharpe_ratio():.2f}",
            f"{pf.sortino_ratio():.2f}",
            f"{pf.max_drawdown() * 100:.2f}%",
            f"{pf.trades.win_rate() * 100:.1f}%" if len(pf.trades) > 0 else "N/A",
            f"{pf.trades.count()}",
            f"{pf.trades.profit_factor():.2f}" if len(pf.trades) > 0 else "N/A",
        ],
        f"Buy & Hold ({SYMBOL})": [
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
