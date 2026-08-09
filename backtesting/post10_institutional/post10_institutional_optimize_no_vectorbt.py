"""
Lightweight optimization for post10_institutional without vectorbt.
Performs confluence (EMA, RSI, MACD, ADX fallback, Volume MA), sweeps confluence thresholds and trailing SLs,
and writes CSV + markdown summary.
"""
import os
import argparse
from pathlib import Path
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

# Prefer openalgo.ta for ADX/exrem/supertrend if available
try:
    import openalgo.ta as ta
    HAS_OPENALGO_TA = True
except Exception:
    HAS_OPENALGO_TA = False

# Data loader (reuse same approach)
def load_history(symbol, exchange, interval, start=None, end=None):
    try:
        from openalgo.client import OpenAlgoClient
        client = OpenAlgoClient()
        df = client.history(symbol=symbol, exchange=exchange, interval=interval, start=start, end=end)
        if isinstance(df, pd.DataFrame):
            return df
    except Exception:
        pass
    duckdb_path = os.environ.get('DUCKDB_PATH')
    if duckdb_path:
        try:
            import duckdb
            con = duckdb.connect(duckdb_path, read_only=True)
            q = f"SELECT datetime AS timestamp, open, high, low, close, volume FROM prices WHERE symbol='{symbol}' AND exchange='{exchange}'"
            if start:
                q += f" AND datetime >= '{start}'"
            if end:
                q += f" AND datetime <= '{end}'"
            q += " ORDER BY datetime"
            df = con.execute(q).fetchdf()
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            df = df.set_index('timestamp')
            return df
        except Exception:
            pass
    try:
        import yfinance as yf
        yahoo_map = {'NIFTY':'^NSEI','BANKNIFTY':'^NSEBANK'}
        ticker = yahoo_map.get(symbol, symbol + '.NS')
        # prefer explicit start/end for reproducibility; yfinance limits 5m range to 60 days
        if start and end:
            df = yf.download(ticker, start=start, end=end, interval=interval)
        else:
            df = yf.download(ticker, period='6mo', interval=interval)
        if df.empty:
            raise RuntimeError('No data')
        # normalize column names to lowercase
        # normalize column names; handle tuple/multiindex columns from yfinance
        new_cols = []
        for c in df.columns:
            if isinstance(c, tuple):
                new_cols.append('_'.join([str(x).lower().replace(' ', '_') for x in c if x is not None]))
            else:
                new_cols.append(str(c).lower().replace(' ', '_'))
        df.columns = new_cols
        # Map multiindex-like names to base OHLCV if necessary
        required = ['open','high','low','close','volume']
        for col in list(df.columns):
            base = col.split('_')[0]
            if base in required and base not in df.columns:
                df[base] = df[col]
        # adj close fallback
        if 'close' not in df.columns and 'adj_close' in df.columns:
            df['close'] = df['adj_close']
        # ensure required columns exist
        for col in required:
            if col not in df.columns:
                raise RuntimeError(f'Missing column {col} from yfinance for {symbol}')
        return df[['open','high','low','close','volume']]
    except Exception as e:
        raise RuntimeError(f'No data source for {symbol}: {e}')

# Indicators
def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()

def rsi(series, window=14):
    delta = series.diff()
    up = delta.clip(lower=0)
    down = -1 * delta.clip(upper=0)
    ma_up = up.rolling(window).mean()
    ma_down = down.rolling(window).mean()
    rs = ma_up / (ma_down + 1e-9)
    return 100 - (100 / (1 + rs))

def macd(series, fast=12, slow=26):
    fast_ema = ema(series, fast)
    slow_ema = ema(series, slow)
    return fast_ema - slow_ema

def adx(df, window=14):
    if HAS_OPENALGO_TA:
        try:
            adx_series = ta.adx(df['high'], df['low'], df['close'], period=window)
            if isinstance(adx_series, pd.Series):
                return adx_series
            if isinstance(adx_series, dict) and 'adx' in adx_series:
                return adx_series['adx']
        except Exception:
            pass
    # fallback: use ATR-based proxy (very rough)
    tr = pd.concat([df['high'] - df['low'], (df['high'] - df['close'].shift()).abs(), (df['low'] - df['close'].shift()).abs()], axis=1)
    tr = tr.max(axis=1)
    atr = tr.rolling(window).mean()
    # produce synthetic adx by normalizing ATR
    return 100 * (atr / (df['close'].rolling(window).mean()+1e-9))

def compute_confluence(df):
    close = df['close']
    ema_fast = ema(close, 8)
    ema_slow = ema(close, 21)
    ema_bull = ema_fast > ema_slow
    r = rsi(close, 14)
    rsi_bull = r > 50
    m = macd(close)
    macd_bull = m > 0
    adx_s = adx(df)
    adx_bull = adx_s > 20
    vol_ma = df['volume'].rolling(20).mean()
    vol_bull = df['volume'] > vol_ma
    confluence = (ema_bull.astype(int) + rsi_bull.astype(int) + macd_bull.astype(int) + adx_bull.astype(int) + vol_bull.astype(int))
    return confluence

def compute_vwap(df):
    tp = (df['high'] + df['low'] + df['close']) / 3.0
    cp = tp * df['volume']
    return cp.cumsum() / (df['volume'].cumsum()+1e-9)

def compute_supertrend(df):
    if HAS_OPENALGO_TA:
        try:
            st = ta.supertrend(df['high'], df['low'], df['close'], period=10, multiplier=3.0)
            if isinstance(st, pd.Series):
                return st
            if isinstance(st, dict) and 'supertrend' in st:
                return st['supertrend']
        except Exception:
            pass
    ema50 = ema(df['close'], 50)
    return df['close'] > ema50

# Simple backtest simulation
def backtest_config(df, confluence_threshold, trailing_sl_pct, strict_vwap_required=False, fees=0.00111, fixed_fees=20):
    df = df.copy()
    df = df.dropna(subset=['close'])
    df['vwap'] = compute_vwap(df)
    df['confluence'] = compute_confluence(df)
    df['supertrend'] = compute_supertrend(df)
    close = df['close']
    entries_idx = []
    if strict_vwap_required:
        prev_close = close.shift(1)
        prev_vwap = df['vwap'].shift(1)
        cross_up = (prev_close < prev_vwap) & (close > df['vwap'])
        entry_signal = cross_up & (df['confluence'] >= confluence_threshold) & (df['supertrend'])
    else:
        entry_signal = (close >= df['vwap']) & (df['confluence'] >= confluence_threshold) & (df['supertrend'])
    entry_signal = entry_signal.fillna(False)
    # Use simple local maxima filter if exrem available
    if HAS_OPENALGO_TA:
        try:
            entry_signal = ta.exrem(entry_signal.astype(int)).astype(bool)
        except Exception:
            entry_signal = entry_signal
    else:
        entry_signal = entry_signal
    for i in range(len(df)):
        if entry_signal.iloc[i]:
            entries_idx.append(i)
    if not entries_idx:
        return {'total_return':0.0,'win_rate':0.0,'profit_factor':0.0,'max_drawdown':0.0,'trade_count':0}
    wins = 0
    losses = 0
    gross_profit = 0.0
    gross_loss = 0.0
    total_profit = 0.0
    equity_curve = [1.0]
    for entry_idx in entries_idx:
        entry_price = close.iloc[entry_idx]
        peak = entry_price
        exited = False
        for j in range(entry_idx+1, len(close)):
            p = close.iloc[j]
            if p > peak:
                peak = p
            stop_price = peak * (1.0 - trailing_sl_pct)
            if p <= stop_price:
                exit_price = p
                exited = True
                break
        if not exited:
            exit_price = close.iloc[-1]
        pnl = (exit_price - entry_price) / entry_price
        # apply fees roughly
        pnl_net = pnl - fees - (fixed_fees / max(1, 10000))
        total_profit += pnl_net
        equity_curve.append(equity_curve[-1] * (1 + pnl_net))
        if pnl_net > 0:
            wins += 1
            gross_profit += pnl_net
        else:
            losses += 1
            gross_loss += -pnl_net
    trade_count = wins + losses
    win_rate = wins / trade_count if trade_count>0 else 0.0
    profit_factor = (gross_profit / gross_loss) if gross_loss>0 else (np.inf if gross_profit>0 else 0.0)
    total_return = total_profit
    # max drawdown
    ec = pd.Series(equity_curve)
    roll_max = ec.cummax()
    drawdown = (ec - roll_max) / roll_max
    maxdd = drawdown.min() if not drawdown.empty else 0.0
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
    csv_path = Path(out_dir) / f'post10_opt_results_no_vectorbt_{"strict" if strict_vwap_required else "flex"}.csv'
    dfres.to_csv(csv_path, index=False)
    md = []
    md.append('# post10_institutional Optimization Results (no-vectorbt)')
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
    md_path = Path(out_dir) / f'post10_opt_summary_no_vectorbt_{"strict" if strict_vwap_required else "flex"}.md'
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
    p.add_argument('--strict', action='store_true')
    args = p.parse_args()
    symbols = [s.strip() for s in args.symbols.split(',') if s.strip()]
    confluence_list = [3,4,5]
    trailing_list = [0.002,0.005,0.01,0.015,0.02]
    run_grid(symbols, args.exchange, args.interval, args.start, args.end, args.out, confluence_list, trailing_list, strict_vwap_required=args.strict)
