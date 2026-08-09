"""
Backtest/Optimization script for post10_institutional strategy.
Sweeps Confluence Score thresholds (3,4,5) and Trailing SL values across multiple indices.
Saves per-run CSV and a summary markdown report.

Usage (from repo root):
    uv run python backtesting\post10_institutional\post10_institutional_optimize_allindices.py \
        --symbols NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,NIFTYNXT50 --interval 5m \
        --start 2026-01-01 --end 2026-06-30 --out reports/post10_opt

Notes:
- Tries OpenAlgo client.history(); falls back to DuckDB if DUCKDB_PATH env is set; otherwise uses yfinance for indexing convenience.
- Uses openalgo.ta when available; falls back to a simple exrem implementation.
- Requires vectorbt, pandas, plotly, duckdb (optional), tqdm.
"""

import os
import argparse
from pathlib import Path
import csv
import sys
import math
from datetime import datetime

try:
    from dotenv import find_dotenv, load_dotenv
    load_dotenv(find_dotenv())
except Exception:
    pass

import numpy as np
import pandas as pd

# vectorbt and openalgo imports (optional)
try:
    import vectorbt as vbt
except Exception:
    print('vectorbt not installed. Install with pip to run backtests.', file=sys.stderr)
    raise

try:
    import duckdb
except Exception:
    duckdb = None

# Prefer openalgo.ta
try:
    import openalgo.ta as ta
    HAS_OPENALGO_TA = True
except Exception:
    HAS_OPENALGO_TA = False

# Minimal exrem fallback
def exrem(series, window=3):
    # simple local extrema detection: peaks where value greater than neighbors
    s = series.fillna(method='ffill').fillna(method='bfill')
    peaks = (s > s.shift(1)) & (s > s.shift(-1))
    troughs = (s < s.shift(1)) & (s < s.shift(-1))
    return peaks.fillna(False) | troughs.fillna(False)

# Data loader: try OpenAlgo client.history(), else duckdb, else yfinance
def load_history(symbol, exchange, interval, start=None, end=None):
    # Try OpenAlgo client
    try:
        from openalgo.client import OpenAlgoClient
        client = OpenAlgoClient()
        df = client.history(symbol=symbol, exchange=exchange, interval=interval, start=start, end=end)
        # Expect OHLCV with datetime index or column 'timestamp'
        if isinstance(df, pd.DataFrame):
            return df
    except Exception:
        pass

    # Try DuckDB path
    duckdb_path = os.environ.get('DUCKDB_PATH')
    if duckdb_path and duckdb:
        try:
            con = duckdb.connect(duckdb_path, read_only=True)
            # Assumes table "prices" with columns symbol, datetime, open, high, low, close, volume
            q = f"SELECT datetime AS timestamp, open, high, low, close, volume FROM prices WHERE symbol='{symbol}' AND exchange='{exchange}'"
            if start:
                q += f" AND datetime >= '{start}'"
            if end:
                q += f" AND datetime <= '{end}'"
            q += f" ORDER BY datetime"
            df = con.execute(q).fetchdf()
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.set_index('timestamp')
            return df
        except Exception:
            pass

    # Final fallback: yfinance for NSE indices if symbol is NIFTY-like
    try:
        import yfinance as yf
        ticker = symbol
        # Map common symbols to Yahoo tickers
        yahoo_map = {
            'NIFTY': '^NSEI',
            'BANKNIFTY': '^NSEBANK',
        }
        ticker = yahoo_map.get(symbol, symbol + '.NS')
        period = '6mo'
        df = yf.download(ticker, period=period, interval=interval)
        if df.empty:
            raise RuntimeError('yfinance returned empty')
        df = df.rename(columns={'Open':'open','High':'high','Low':'low','Close':'close','Volume':'volume'})
        return df
    except Exception as e:
        raise RuntimeError(f'No data source available for {symbol}: {e}')

# Simple confluence score: EMA crosses, RSI direction, MACD sign, ADX > 20, Volume > MA
def compute_confluence(df):
    # EMA fast/slow
    ema_fast = vbt.MA.run(df['close'], window=8).ma
    ema_slow = vbt.MA.run(df['close'], window=21).ma
    ema_bull = ema_fast > ema_slow

    # RSI
    rsi = vbt.RSI.run(df['close'], window=14).rsi
    rsi_bull = rsi > 50

    # MACD
    macd = vbt.MACD.run(df['close']).macd
    macd_bull = macd > 0

    # ADX
    adx = vbt.ADX.run(df['high'], df['low'], df['close']).adx
    adx_bull = adx > 20

    # Volume above MA
    vol_ma = df['volume'].rolling(20).mean()
    vol_bull = df['volume'] > vol_ma

    confluence = (ema_bull.astype(int) + rsi_bull.astype(int) + macd_bull.astype(int) + adx_bull.astype(int) + vol_bull.astype(int))
    return confluence

# Entry rule: price >= vwap OR (not strict) price condition + confluence >= threshold + supertrend bull
# For simplicity compute VWAP as typical_price * cum(volume) / cum(volume)
def compute_vwap(df):
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    cp = tp * df['volume']
    vwap = cp.cumsum() / df['volume'].cumsum()
    return vwap

# Supertrend via openalgo.ta if available, else simple moving-average trend
def compute_supertrend(df):
    if HAS_OPENALGO_TA:
        try:
            st = ta.supertrend(df['high'], df['low'], df['close'], period=10, multiplier=3.0)
            # assume returns DataFrame with 'supertrend' or boolean 'trend' column
            if 'supertrend' in st:
                return st['supertrend']
            # fallback
        except Exception:
            pass
    # fallback: close > 50 EMA
    ema50 = df['close'].ewm(span=50).mean()
    return df['close'] > ema50

# Backtest single configuration
def backtest_config(df, confluence_threshold, trailing_sl_pct, strict_vwap_required=False, fees=0.00111, fixed_fees=20, lot_min=None, size_granularity=None):
    df = df.copy()
    df = df.dropna(subset=['close'])
    df['vwap'] = compute_vwap(df)
    df['confluence'] = compute_confluence(df)
    df['supertrend'] = compute_supertrend(df)

    price = df['close']

    # Entry signal
    if strict_vwap_required:
        prev_close = price.shift(1)
        prev_vwap = df['vwap'].shift(1)
        cross_up = (prev_close < prev_vwap) & (price > df['vwap'])
        entry = cross_up & (df['confluence'] >= confluence_threshold) & (df['supertrend'])
    else:
        entry = (price >= df['vwap']) & (df['confluence'] >= confluence_threshold) & (df['supertrend'])

    # Use exrem to clean (if openalgo.ta available)
    try:
        entry = entry.fillna(False)
        if HAS_OPENALGO_TA:
            entry_clean = ta.exrem(entry.astype(int))
            entry = entry_clean.astype(bool)
        else:
            entry = exrem(entry.astype(int))
    except Exception:
        entry = entry.fillna(False)

    # Exits: trailing SL or fixed percent stop-loss trailing
    # Build stops: we'll use vectorbt's TrailingStopLoss
    entries = entry.values
    exits = np.zeros_like(entries, dtype=bool)

    # Use vectorbt entries/exits via signals; then apply trailing stop externally by simulating orders
    pf = vbt.Portfolio.from_signals(close=price, entries=entries, exits=exits,
                                    fees=fees, fixed_fees=fixed_fees, slippage=0.0)
    # Now apply trailing stop: iterate trades
    trades = pf.trades.records_readable
    # If no trades, return zeros
    if trades.empty:
        return {'total_return':0.0,'win_rate':0.0,'profit_factor':0.0,'max_drawdown':0.0,'trade_count':0}

    # Re-run a simple manual P&L simulation with trailing stop
    balance = 1.0
    wins = 0
    losses = 0
    total_profit = 0.0
    gross_profit = 0.0
    gross_loss = 0.0

    for _, tr in trades.iterrows():
        entry_idx = int(tr['EntryIdx'])
        exit_idx = int(tr['ExitIdx']) if not math.isnan(tr['ExitIdx']) else None
        entry_price = float(tr['EntryPrice'])
        # simulate daily progression until exit by trailing stop or final close
        peak = entry_price
        exited = False
        for i in range(entry_idx+1, len(price)):
            p = price.iloc[i]
            if p > peak:
                peak = p
            # trailing stop price
            stop_price = peak * (1.0 - trailing_sl_pct)
            if p <= stop_price:
                exit_price = p
                exited = True
                break
            # if original portfolio exited earlier, respect it
            if i == exit_idx:
                exit_price = p
                exited = True
                break
        if not exited:
            exit_price = price.iloc[-1]
        pnl = (exit_price - entry_price) / entry_price
        total_profit += pnl
        if pnl > 0:
            wins += 1
            gross_profit += pnl
        else:
            losses += 1
            gross_loss += -pnl
    trade_count = wins + losses
    win_rate = wins / trade_count if trade_count>0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss>0 else (np.inf if gross_profit>0 else 0.0)
    total_return = total_profit
    # approximate max drawdown from pf
    try:
        stats = pf.stats()
        maxdd = 0.0
        if 'Max Drawdown [%]' in stats:
            maxdd = float(stats['Max Drawdown [%]']) / 100.0
    except Exception:
        maxdd = 0.0

    return {'total_return': total_return, 'win_rate': win_rate, 'profit_factor': profit_factor, 'max_drawdown': maxdd, 'trade_count': trade_count}


def run_grid(symbols, exchange, interval, start, end, out_dir, confluence_list, trailing_list, strict_vwap_required=False):
    rows = []
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for symbol in symbols:
        print(f'Loading {symbol}...')
        df = load_history(symbol, exchange, interval, start, end)
        if df is None or df.empty:
            print(f'No data for {symbol}, skipping')
            continue
        for conf in confluence_list:
            for tsl in trailing_list:
                res = backtest_config(df, confluence_threshold=conf, trailing_sl_pct=tsl, strict_vwap_required=strict_vwap_required)
                row = {'symbol':symbol,'confluence':conf,'trailing_sl_pct':tsl,'total_return':res['total_return'],'win_rate':res['win_rate'],'profit_factor':res['profit_factor'],'max_drawdown':res['max_drawdown'],'trade_count':res['trade_count'],'strict_vwap_required':strict_vwap_required}
                rows.append(row)
                print(f"{symbol} conf={conf} tsl={tsl:.4f} -> trades={res['trade_count']} winrate={res['win_rate']:.3f} pf={res['profit_factor']:.3f}")
    dfres = pd.DataFrame(rows)
    csv_path = Path(out_dir) / f'post10_opt_results_{"strict" if strict_vwap_required else "flex"}.csv'
    dfres.to_csv(csv_path, index=False)
    # summary markdown
    md = []
    md.append('# post10_institutional Optimization Results')
    md.append('')
    md.append(f'Generated: {datetime.utcnow().isoformat()} UTC')
    md.append('')
    for symbol in dfres['symbol'].unique():
        sub = dfres[dfres['symbol']==symbol]
        best_by_return = sub.sort_values('total_return', ascending=False).head(3)
        best_by_pf = sub.sort_values('profit_factor', ascending=False).head(3)
        md.append(f'## {symbol}')
        md.append('\n**Top by Total Return**\n')
        md.append(best_by_return.to_markdown(index=False))
        md.append('\n**Top by Profit Factor**\n')
        md.append(best_by_pf.to_markdown(index=False))
        md.append('')
    md_path = Path(out_dir) / f'post10_opt_summary_{"strict" if strict_vwap_required else "flex"}.md'
    md_text = '\n\n'.join(md)
    md_path.write_text(md_text)
    print(f'Results saved to {csv_path} and {md_path}')


if __name__ == '__main__':
    p = argparse.ArgumentParser()
    p.add_argument('--symbols', default='NIFTY,BANKNIFTY,FINNIFTY,MIDCPNIFTY,NIFTYNXT50')
    p.add_argument('--exchange', default='NSE')
    p.add_argument('--interval', default='5m')
    p.add_argument('--start', default=None)
    p.add_argument('--end', default=None)
    p.add_argument('--out', default='reports/post10_opt')
    p.add_argument('--strict', action='store_true', help='Require strict single-bar VWAP crossover')
    args = p.parse_args()

    symbols = [s.strip() for s in args.symbols.split(',') if s.strip()]
    confluence_list = [3,4,5]
    trailing_list = [0.002,0.005,0.01,0.015,0.02]

    run_grid(symbols, args.exchange, args.interval, args.start, args.end, args.out, confluence_list, trailing_list, strict_vwap_required=args.strict)
