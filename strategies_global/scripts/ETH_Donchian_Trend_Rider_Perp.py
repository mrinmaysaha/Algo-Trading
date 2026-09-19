#!/usr/bin/env python3
"""
================================================================================
OPENALGO PRODUCTION STRATEGY: ETH DONCHIAN ASYMMETRIC TREND RIDER (PERP)
================================================================================
Target Symbol   : ETHUSDFUT (Delta Exchange: ETHUSD)
Exchange        : CRYPTO
Product Type    : NRML (Perpetual Futures)
Timeframe       : 1-Hour Execution, 4-Hour Trend Context
Leverage        : 15x - 20x (Isolated Margin)
Capital Base    : $5,000 - $10,000 (or INR equivalent)
Instance Route  : Delta Exchange Instance -> Port 5001 (openalgo-global)
================================================================================
"""

import os
import sys
import time
import argparse
import logging
from datetime import datetime, timezone, timedelta
import numpy as np
import pandas as pd

# OpenAlgo SDK
try:
    from openalgo import api, ta
except ImportError:
    pass

# Setup Logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ETH_Trend_Rider")

# Strategy Parameters
STRATEGY_NAME = "ETH_Donchian_Trend_Rider"
SYMBOL = "ETHUSDFUT"
DELTA_SYMBOL = "ETHUSD"
EXCHANGE = "CRYPTO"
PRODUCT = "NRML"
TIMEFRAME = "1h"
DONCHIAN_ENTRY_PERIOD = int(os.getenv("DONCHIAN_ENTRY_PERIOD", "24"))     # 24 hours responsive breakout (configurable)
DONCHIAN_EXIT_PERIOD = 16      # 16 hours trailing exit
EMA_TREND_PERIOD = 200
ATR_PERIOD = 14
ATR_SL_MULT = 1.5
DEFAULT_LEVERAGE = 20.0
ACCOUNT_CAPITAL_USD = float(os.getenv("ACCOUNT_CAPITAL_USD", "1000.0"))
RISK_PER_TRADE_PCT = 0.02

# Delta Exchange Cost Constants
MAKER_FEE = 0.0002             # 0.02%
TAKER_FEE = 0.0005             # 0.05%
GST_RATE = 0.18                # 18% on trading fees
SLIPPAGE_RATE = 0.0003          # 0.03% estimated slippage


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """Computes Donchian Channels, 200 EMA, and 14 ATR."""
    df = df.copy()
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    n = len(df)
    
    # 200 EMA Trend Filter
    df['ema200'] = pd.Series(closes).ewm(span=EMA_TREND_PERIOD, adjust=False).mean().values
    
    # 36-bar Donchian Entry Channel
    df['donchian_high'] = pd.Series(highs).shift(1).rolling(DONCHIAN_ENTRY_PERIOD).max().values
    df['donchian_low'] = pd.Series(lows).shift(1).rolling(DONCHIAN_ENTRY_PERIOD).min().values
    
    # 16-bar Donchian Exit Channel
    df['exit_low'] = pd.Series(lows).shift(1).rolling(DONCHIAN_EXIT_PERIOD).min().values
    df['exit_high'] = pd.Series(highs).shift(1).rolling(DONCHIAN_EXIT_PERIOD).max().values
    
    # 14 ATR
    tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
    atr = np.zeros(n)
    atr[1:] = pd.Series(tr).rolling(ATR_PERIOD).mean().fillna(10.0).values
    df['atr'] = atr
    
    return df


def run_backtest(data_path: str = "data/crypto/ETHUSD_1h_1year.parquet", capital: float = ACCOUNT_CAPITAL_USD, leverage: float = DEFAULT_LEVERAGE):
    """Executes a 1-year backtest for ETH Donchian Trend Rider."""
    logger.info("=" * 80)
    logger.info(f"RUNNING ETH DONCHIAN ASYMMETRIC TREND BACKTEST ({leverage}x LEVERAGE)")
    logger.info(f"Data Source: {data_path} | Starting Capital: ${capital:,.2f}")
    logger.info("=" * 80)
    
    if not os.path.exists(data_path):
        logger.error(f"Data file not found at {data_path}!")
        return
        
    df_raw = pd.read_parquet(data_path)
    df = compute_indicators(df_raw)
    
    closes = df['close'].values
    highs = df['high'].values
    lows = df['low'].values
    ema200 = df['ema200'].values
    don_high = df['donchian_high'].values
    don_low = df['donchian_low'].values
    exit_low = df['exit_low'].values
    exit_high = df['exit_high'].values
    atr = df['atr'].values
    timestamps = df['timestamp'].values
    n = len(df)
    
    trades = []
    in_pos = False
    pos_type = None
    entry_price = 0.0
    stop_loss = 0.0
    initial_sl_dist = 0.0
    entry_bar = 0
    pos_size_usd = 0.0
    margin_usd = 0.0
    
    for i in range(200, n):
        curr_close = closes[i]
        curr_high = highs[i]
        curr_low = lows[i]
        curr_atr = atr[i]
        
        # Check Exits
        if in_pos:
            exit_trade = False
            exit_price = 0.0
            exit_reason = ""
            
            if pos_type == "LONG":
                if curr_close < exit_low[i]:
                    exit_trade = True
                    exit_price = curr_close * (1.0 - SLIPPAGE_RATE)
                    exit_reason = "TRAILING_CHANNEL_EXIT"
                elif curr_low <= stop_loss:
                    exit_trade = True
                    exit_price = stop_loss * (1.0 - SLIPPAGE_RATE)
                    exit_reason = "INITIAL_SL_HIT"
                    
            elif pos_type == "SHORT":
                if curr_close > exit_high[i]:
                    exit_trade = True
                    exit_price = curr_close * (1.0 + SLIPPAGE_RATE)
                    exit_reason = "TRAILING_CHANNEL_EXIT"
                elif curr_high >= stop_loss:
                    exit_trade = True
                    exit_price = stop_loss * (1.0 + SLIPPAGE_RATE)
                    exit_reason = "INITIAL_SL_HIT"
                    
            if exit_trade:
                gross_pct = (exit_price - entry_price) / entry_price if pos_type == "LONG" else (entry_price - exit_price) / entry_price
                gross_pnl = gross_pct * pos_size_usd
                
                entry_fees = (pos_size_usd * MAKER_FEE) * (1.0 + GST_RATE)
                exit_fees = (pos_size_usd * TAKER_FEE) * (1.0 + GST_RATE)
                total_fees = entry_fees + exit_fees
                net_pnl = gross_pnl - total_fees
                
                trades.append({
                    "entry_time": datetime.fromtimestamp(timestamps[entry_bar], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "exit_time": datetime.fromtimestamp(timestamps[i], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
                    "type": pos_type,
                    "entry_price": entry_price,
                    "exit_price": exit_price,
                    "gross_pnl": gross_pnl,
                    "fees": total_fees,
                    "net_pnl": net_pnl,
                    "exit_reason": exit_reason,
                    "return_on_margin": (net_pnl / margin_usd) * 100
                })
                in_pos = False
                continue
                
        # Check Entries
        if not in_pos:
            if curr_close > don_high[i] and curr_close > ema200[i]:
                in_pos = True
                pos_type = "LONG"
                entry_price = curr_close
                initial_sl_dist = ATR_SL_MULT * curr_atr
                stop_loss = entry_price - initial_sl_dist
                entry_bar = i
                
                risk_amount = capital * RISK_PER_TRADE_PCT
                raw_notional = risk_amount / (initial_sl_dist / entry_price)
                pos_size_usd = min(raw_notional, capital * leverage)
                margin_usd = pos_size_usd / leverage
                
            elif curr_close < don_low[i] and curr_close < ema200[i]:
                in_pos = True
                pos_type = "SHORT"
                entry_price = curr_close
                initial_sl_dist = ATR_SL_MULT * curr_atr
                stop_loss = entry_price + initial_sl_dist
                entry_bar = i
                
                risk_amount = capital * RISK_PER_TRADE_PCT
                raw_notional = risk_amount / (initial_sl_dist / entry_price)
                pos_size_usd = min(raw_notional, capital * leverage)
                margin_usd = pos_size_usd / leverage

    df_trades = pd.DataFrame(trades)
    if len(df_trades) == 0:
        logger.warning("No trades generated during backtest.")
        return
        
    wins = df_trades[df_trades['net_pnl'] > 0]
    losses = df_trades[df_trades['net_pnl'] <= 0]
    total_net_pnl = df_trades['net_pnl'].sum()
    win_rate = (len(wins) / len(df_trades)) * 100
    avg_win = wins['net_pnl'].mean() if len(wins) > 0 else 0
    avg_loss = abs(losses['net_pnl'].mean()) if len(losses) > 0 else 1e-5
    payoff_ratio = avg_win / avg_loss
    profit_factor = wins['net_pnl'].sum() / max(abs(losses['net_pnl'].sum()), 1e-5)
    
    df_trades['cum_pnl'] = df_trades['net_pnl'].cumsum()
    df_trades['equity'] = capital + df_trades['cum_pnl']
    df_trades['peak'] = df_trades['equity'].cummax()
    df_trades['drawdown'] = (df_trades['peak'] - df_trades['equity']) / df_trades['peak']
    max_dd_pct = df_trades['drawdown'].max() * 100
    
    logger.info("-" * 80)
    logger.info(f"  • Total Trades Executed   : {len(df_trades)}")
    logger.info(f"  • Winning Trades          : {len(wins)} ({win_rate:.1f}%)")
    logger.info(f"  • Losing Trades           : {len(losses)} ({100 - win_rate:.1f}%)")
    logger.info(f"  • Average Winning Trade   : ${avg_win:,.2f}")
    logger.info(f"  • Average Losing Trade    : ${avg_loss:,.2f}")
    logger.info(f"  • Payoff Ratio (W/L)      : {payoff_ratio:.2f} (1 WIN COVERS {payoff_ratio:.1f} LOSSES)")
    logger.info(f"  • Profit Factor           : {profit_factor:.2f}")
    logger.info(f"  • Max Drawdown %          : {max_dd_pct:.2f}%")
    logger.info(f"  • Total Fees & GST Paid   : ${df_trades['fees'].sum():,.2f}")
    logger.info(f"  • Final Equity            : ${capital + total_net_pnl:,.2f}")
    logger.info(f"  • Net 1-Year ROI          : +{(total_net_pnl / capital) * 100:.2f}%")
    logger.info("=" * 80)
    return df_trades


def log_15min_status_heartbeat(client, last_candle: dict, indicators: dict, active_pos: dict):
    """Standardized OpenAlgo 15-Minute Diagnostic Heartbeat."""
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    logger.info("=" * 80)
    logger.info(f" [DIAGNOSTIC HEARTBEAT] {STRATEGY_NAME} | {now_str}")
    logger.info("-" * 80)
    logger.info(f"  • Symbol & Price   : {SYMBOL} = ${last_candle.get('close', 0.0):,.2f}")
    logger.info(f"  • 200 EMA Macro    : ${indicators.get('ema200', 0.0):,.2f} [{'BULLISH' if last_candle.get('close', 0) > indicators.get('ema200', 0) else 'BEARISH'}]")
    logger.info(f"  • Donchian 36 High : ${indicators.get('donchian_high', 0.0):,.2f} | Low: ${indicators.get('donchian_low', 0.0):,.2f}")
    logger.info(f"  • Exit Channel 16  : High = ${indicators.get('exit_high', 0.0):,.2f} | Low = ${indicators.get('exit_low', 0.0):,.2f}")
    logger.info(f"  • 14 ATR Volatility: ${indicators.get('atr', 0.0):,.2f}")
    
    qty = active_pos.get('quantity', 0.0)
    if qty != 0:
        side = "LONG" if qty > 0 else "SHORT"
        pnl = active_pos.get('pnl', 0.0)
        logger.info(f"  • ACTIVE POSITION  : {side} {abs(qty)} lots | Unrealized PnL: ${pnl:,.2f}")
        logger.info(f"  • Trailing Stop    : ${active_pos.get('stop_loss', 0.0):,.2f}")
    else:
        logger.info(f"  • ACTIVE POSITION  : FLAT (Scanning for 36-bar breakout)")
    logger.info("=" * 80)


def run_live(api_key: str = None, host: str = None, dry_run: bool = False):
    """Executes the live trading loop connecting to OpenAlgo Delta Exchange instance (Port 5001)."""
    logger.info(f"Starting {STRATEGY_NAME} Live Execution Loop (Dry-Run={dry_run})...")
    # Delta Exchange instance runs on host port 5001 (or internal 5000 inside docker container)
    default_host = "http://127.0.0.1:5000" if os.path.exists("/.dockerenv") else "http://127.0.0.1:5001"
    host = host or os.getenv("OPENALGO_HOST_CRYPTO") or os.getenv("OPENALGO_HOST", default_host)
    api_key = api_key or os.getenv("OPENALGO_API_KEY_CRYPTO") or os.getenv("OPENALGO_API_KEY", "56609a7a820eaadf566b44babb61609fa55c25100996723f31a0b048f0c86721")
    
    logger.info(f"Targeting OpenAlgo Delta instance: {host}")
    client = api(api_key=api_key, host=host) if 'api' in globals() else None
    
    while True:
        try:
            today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            start_str = (datetime.now(timezone.utc) - timedelta(days=12)).strftime("%Y-%m-%d")
            df = None
            if client and not dry_run:
                try:
                    res = client.history(symbol=SYMBOL, exchange=EXCHANGE, interval="1h", start_date=start_str, end_date=today_str)
                    if isinstance(res, pd.DataFrame):
                        df = res
                    elif isinstance(res, dict) and 'data' in res:
                        df = pd.DataFrame(res['data'])
                    elif isinstance(res, list):
                        df = pd.DataFrame(res)
                except Exception as api_err:
                    logger.warning(f"Live API history fetch notice: {api_err}")
                    
            data_file = "data/crypto/ETHUSD_1h_1year.parquet"
            if df is None or (isinstance(df, pd.DataFrame) and df.empty):
                if os.path.exists(data_file):
                    df = pd.read_parquet(data_file).tail(80)
                elif os.path.exists(f"/app/{data_file}"):
                    df = pd.read_parquet(f"/app/{data_file}").tail(80)
                
            if df is not None and isinstance(df, pd.DataFrame) and len(df) >= 45:
                df.columns = [str(c).lower() for c in df.columns]
                for col in ['open', 'high', 'low', 'close', 'volume']:
                    if col in df.columns:
                        df[col] = df[col].astype(float)
                df = compute_indicators(df)
                last = df.iloc[-1].to_dict()
                
                pos_info = {'quantity': 0.0, 'pnl': 0.0, 'stop_loss': 0.0}
                if client and not dry_run:
                    try:
                        pos_resp = client.positionbook()
                        if pos_resp and 'data' in pos_resp:
                            total_qty = 0.0
                            total_pnl = 0.0
                            for p in pos_resp['data']:
                                if p.get('symbol') in [SYMBOL, DELTA_SYMBOL]:
                                    raw_qty = p.get('quantity') if p.get('quantity') is not None else p.get('netqty', 0)
                                    total_qty += float(raw_qty or 0.0)
                                    total_pnl += float(p.get('pnl', 0.0))
                            pos_info['quantity'] = total_qty
                            pos_info['pnl'] = total_pnl
                    except Exception:
                        pass
                        
                log_15min_status_heartbeat(client, last, last, pos_info)

                # --------------------------------------------------------------
                # LIVE EXECUTION ENGINE (Breakout Entry & Trailing Exit)
                # --------------------------------------------------------------
                curr_close = float(last.get('close', 0.0))
                don_high = float(last.get('donchian_high', 0.0))
                don_low = float(last.get('donchian_low', 0.0))
                ema200 = float(last.get('ema200', 0.0))
                exit_high = float(last.get('exit_high', 0.0))
                exit_low = float(last.get('exit_low', 0.0))
                curr_atr = float(last.get('atr', 10.0))
                active_qty = float(pos_info.get('quantity', 0.0))

                if client and not dry_run:
                    # 1. Manage Active Positions (Exits)
                    if active_qty > 0:  # LONG position
                        if curr_close < exit_low:
                            logger.info(f"🚨 EXIT TRIGGER [LONG]: Close=${curr_close:.2f} < ExitLow=${exit_low:.2f}. Closing position.")
                            client.order(symbol=SYMBOL, exchange=EXCHANGE, action="SELL", quantity=abs(int(active_qty)), product=PRODUCT, pricetype="MARKET")
                    elif active_qty < 0:  # SHORT position
                        if curr_close > exit_high:
                            logger.info(f"🚨 EXIT TRIGGER [SHORT]: Close=${curr_close:.2f} > ExitHigh=${exit_high:.2f}. Closing position.")
                            client.order(symbol=SYMBOL, exchange=EXCHANGE, action="BUY", quantity=abs(int(active_qty)), product=PRODUCT, pricetype="MARKET")

                    # 2. Check Breakout Entries if FLAT
                    elif active_qty == 0:
                        # Bullish Breakout: Close > Donchian High and above 200 EMA
                        if curr_close > don_high and curr_close > ema200:
                            initial_sl_dist = ATR_SL_MULT * curr_atr
                            risk_usd = ACCOUNT_CAPITAL_USD * RISK_PER_TRADE_PCT
                            # 1 ETH contract = 0.01 ETH. Loss per contract = initial_sl_dist * 0.01
                            loss_per_contract = max(0.10, initial_sl_dist * 0.01)
                            target_qty = max(1, int(risk_usd / loss_per_contract))
                            logger.info(
                                f"🚀 BULLISH BREAKOUT TRIGGERED: Close=${curr_close:.2f} > DonchianHigh=${don_high:.2f} & EMA200=${ema200:.2f}. "
                                f"Placing BUY {target_qty} lots (SL: ${curr_close - initial_sl_dist:.2f})"
                            )
                            client.order(symbol=SYMBOL, exchange=EXCHANGE, action="BUY", quantity=target_qty, product=PRODUCT, pricetype="MARKET")

                        # Bearish Breakdown: Close < Donchian Low and below 200 EMA
                        elif curr_close < don_low and curr_close < ema200:
                            initial_sl_dist = ATR_SL_MULT * curr_atr
                            risk_usd = ACCOUNT_CAPITAL_USD * RISK_PER_TRADE_PCT
                            loss_per_contract = max(0.10, initial_sl_dist * 0.01)
                            target_qty = max(1, int(risk_usd / loss_per_contract))
                            logger.info(
                                f"🔻 BEARISH BREAKDOWN TRIGGERED: Close=${curr_close:.2f} < DonchianLow=${don_low:.2f} & EMA200=${ema200:.2f}. "
                                f"Placing SELL {target_qty} lots (SL: ${curr_close + initial_sl_dist:.2f})"
                            )
                            client.order(symbol=SYMBOL, exchange=EXCHANGE, action="SELL", quantity=target_qty, product=PRODUCT, pricetype="MARKET")
            else:
                loaded_bars = len(df) if df is not None and isinstance(df, pd.DataFrame) else 0
                logger.info(f"Waiting for sufficient candle history ({loaded_bars} bars loaded)...")
                
        except Exception as e:
            logger.error(f"Error in strategy loop: {e}")
            
        if dry_run:
            logger.info("Dry-run iteration complete. Exiting.")
            break
        time.sleep(60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ETH Donchian Asymmetric Trend Rider")
    parser.add_argument("--mode", choices=["backtest", "live"], default=os.getenv("MODE", "live"), help="Execution mode")
    parser.add_argument("--data", default="data/crypto/ETHUSD_1h_1year.parquet", help="Backtest data path")
    parser.add_argument("--leverage", type=float, default=20.0, help="Leverage multiplier")
    parser.add_argument("--dry-run", action="store_true", help="Perform single live check without loop")
    args = parser.parse_args()
    
    if args.mode == "backtest":
        data_path = args.data
        if not os.path.exists(data_path):
            alt = os.path.join("/app", data_path)
            if os.path.exists(alt):
                data_path = alt
        run_backtest(data_path=data_path, leverage=args.leverage)
    else:
        run_live(dry_run=args.dry_run)

