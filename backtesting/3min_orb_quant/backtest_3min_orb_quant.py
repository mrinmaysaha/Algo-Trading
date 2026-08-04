import os
import sys
import datetime
import numpy as np
import pandas as pd

# Ensure openalgo module path is reachable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))

import numpy as np
if not hasattr(np, "float_"):
    np.float_ = np.float64
if not hasattr(np, "int_"):
    np.int_ = np.int64

try:
    import vectorbt as vbt
    HAS_VBT = True
except ImportError:
    HAS_VBT = False

from openalgo import api, ta

def fetch_banknifty_data():
    """
    Attempts to fetch historical 1-minute / 3-minute data for BANKNIFTY.
    Fallbacks:
    1. OpenAlgo API (if logged in & API key configured)
    2. DuckDB / local sqlite database
    3. yfinance (^NSEBANK)
    """
    api_key = os.getenv("OPENALGO_API_KEY", "")
    host = os.getenv("HOST_SERVER", "http://127.0.0.1:5000")
    
    df = None

    # Try OpenAlgo API
    if api_key and api_key != "YOUR_ACTUAL_API_KEY_HERE":
        try:
            print("[Backtest] Attempting data fetch from OpenAlgo API...")
            client = api(api_key=api_key, host=host)
            data = client.history(
                symbol="BANKNIFTY",
                exchange="NSE_INDEX",
                interval="1m",
                start_date=(datetime.date.today() - datetime.timedelta(days=30)).strftime("%Y-%m-%d"),
                end_date=datetime.date.today().strftime("%Y-%m-%d")
            )
            if isinstance(data, list) and len(data) > 0:
                df = pd.DataFrame(data)
                df['datetime'] = pd.to_datetime(df['timestamp'])
                df.set_index('datetime', inplace=True)
                print(f"[Backtest] Fetched {len(df)} 1-minute bars from OpenAlgo API.")
        except Exception as e:
            print(f"[Backtest] OpenAlgo API fetch notice: {e}")

    # Try yfinance as fallback for BANKNIFTY (^NSEBANK)
    if df is None or len(df) == 0:
        try:
            print("[Backtest] Attempting data fetch from Yahoo Finance (^NSEBANK)...")
            import yfinance as yf
            ticker = yf.Ticker("^NSEBANK")
            # 1m data available up to 7 days
            df_yf = ticker.history(period="7d", interval="1m")
            if df_yf is not None and not df_yf.empty:
                df = df_yf.rename(columns={
                    "Open": "open", "High": "high", "Low": "low", "Close": "close", "Volume": "volume"
                })
                # Convert timezone to IST if tz aware
                if df.index.tz is not None:
                    df.index = df.index.tz_convert("Asia/Kolkata").tz_localize(None)
                print(f"[Backtest] Fetched {len(df)} 1-minute bars from Yahoo Finance (^NSEBANK).")
        except Exception as e:
            print(f"[Backtest] Yahoo Finance fetch notice: {e}")

    # Synthetic fallback for demonstration if no live internet/broker feed
    if df is None or len(df) == 0:
        print("[Backtest] Generating realistic synthetic 1-minute BANKNIFTY price series for testing...")
        dates = pd.date_range(end=datetime.datetime.now(), periods=5*375, freq="1min")
        # Keep only market hours 09:15 to 15:30
        dates = dates[(dates.time >= datetime.time(9, 15)) & (dates.time <= datetime.time(15, 30))]
        
        np.random.seed(42)
        base_price = 51500.0
        returns = np.random.normal(0.00005, 0.0012, size=len(dates))
        price_series = base_price * np.exp(np.cumsum(returns))
        
        high = price_series * (1 + np.abs(np.random.normal(0, 0.0005, len(dates))))
        low = price_series * (1 - np.abs(np.random.normal(0, 0.0005, len(dates))))
        open_p = price_series + np.random.normal(0, 5.0, len(dates))
        close_p = price_series
        
        df = pd.DataFrame({
            "open": open_p,
            "high": high,
            "low": low,
            "close": close_p,
            "volume": np.random.randint(1000, 50000, len(dates))
        }, index=dates)

    return df


def run_3min_orb_quant_backtest(df):
    """
    Executes the exact 3-Min ORB Quant strategy logic:
    - 09:15 - 09:18 IST: Mother Candle (3-minute opening range)
    - Filter: 35 pts <= Mother Range <= 180 pts
    - Breakout Buffer: 3.0 pts
    - Entry cutoff: 10:00 AM IST
    - Max 1 trade per day
    - Delta = 0.55 approximation for ATM option premium
    - Risk = Mother Range * Delta
    - Target = 1.5 * Risk
    - Trailing: SL moved to Break-even at 1.0x Risk
    - EOD Exit at 15:15 IST
    """
    df = df.sort_index()
    
    # Resample or group by trading date
    df['date'] = df.index.date
    df['time'] = df.index.time

    trades = []
    
    daily_groups = df.groupby('date')
    
    min_range = 35.0
    max_range = 180.0
    buffer_pts = 3.0
    delta = 0.55
    rr_ratio = 1.5
    lot_size = 30
    num_lots = 2
    qty = lot_size * num_lots
    
    total_days = len(daily_groups)
    traded_days = 0
    filter_rejected_days = 0
    no_breakout_days = 0

    print("\n========================================================")
    print("      3MIN ORB QUANT BACKTEST SIMULATION RESULTS        ")
    print("========================================================")
    print(f"Strategy Name      : 3Min_ORB_Quant")
    print(f"Underlying Symbol  : BANKNIFTY")
    print(f"Position Sizing    : {num_lots} Lots ({qty} Qty)")
    print(f"Mother Candle Window: 09:15 - 09:18 IST (3 min)")
    print(f"Range Filter Bounds: [{min_range} pts, {max_range} pts]")
    print(f"Breakout Buffer    : {buffer_pts} pts")
    print(f"Risk-to-Reward     : 1:{rr_ratio}")
    print(f"Entry Cutoff Time  : 10:00 AM IST")
    print(f"Force Square-off   : 15:15 IST")
    print("--------------------------------------------------------\n")

    for date, day_df in daily_groups:
        # Filter for 09:15 to 09:18 mother candle
        mother_bars = day_df[(day_df['time'] >= datetime.time(9, 15)) & (day_df['time'] < datetime.time(9, 18))]
        
        if mother_bars.empty:
            continue
            
        m_high = mother_bars['high'].max()
        m_low = mother_bars['low'].min()
        m_range = m_high - m_low
        m_midpoint = (m_high + m_low) / 2.0
        
        # Volatility filter check
        if not (min_range <= m_range <= max_range):
            filter_rejected_days += 1
            continue
            
        # Post-mother bars up to EOD
        trading_window = day_df[(day_df['time'] >= datetime.time(9, 18)) & (day_df['time'] <= datetime.time(15, 15))]
        
        trade_taken = False
        
        for ts, row in trading_window.iterrows():
            current_time = row['time']
            spot_price = row['close']
            
            # Cutoff check
            if current_time >= datetime.time(10, 0) and not trade_taken:
                no_breakout_days += 1
                break
                
            # Bullish Breakout
            if not trade_taken and spot_price >= (m_high + buffer_pts):
                trade_taken = True
                traded_days += 1
                
                entry_time = ts
                entry_spot = spot_price
                pos_type = "CE"
                
                # Dynamic Option Premium estimation (~0.75% of spot)
                est_entry_premium = spot_price * 0.0075
                # Option risk in pts
                spot_risk = spot_price - m_midpoint
                opt_risk_pts = spot_risk * delta
                sl_premium = max(1.0, est_entry_premium - opt_risk_pts)
                target_premium = est_entry_premium + (opt_risk_pts * rr_ratio)
                
                curr_sl = sl_premium
                trailed = False
                exit_price = None
                exit_time = None
                exit_reason = None
                
                # Replay remaining bars for position management
                post_entry_window = day_df[day_df.index > ts]
                for p_ts, p_row in post_entry_window.iterrows():
                    p_spot = p_row['close']
                    p_time = p_row['time']
                    
                    # Approx current option price using delta
                    spot_diff = p_spot - entry_spot
                    curr_opt_price = max(0.5, est_entry_premium + (spot_diff * delta))
                    
                    # 1. Target Hit
                    if curr_opt_price >= target_premium:
                        exit_price = target_premium
                        exit_time = p_ts
                        exit_reason = "TARGET_HIT"
                        break
                        
                    # 2. Step Trail to Cost at 1.0x Risk
                    if not trailed and curr_opt_price >= (est_entry_premium + opt_risk_pts):
                        curr_sl = est_entry_premium
                        trailed = True
                        
                    # 3. SL Hit
                    if curr_opt_price <= curr_sl:
                        exit_price = curr_sl
                        exit_time = p_ts
                        exit_reason = "SL_HIT_BREAKEVEN" if trailed else "SL_HIT_INITIAL"
                        break
                        
                    # 4. EOD Force Exit at 15:15
                    if p_time >= datetime.time(15, 15):
                        exit_price = curr_opt_price
                        exit_time = p_ts
                        exit_reason = "TIME_EXIT"
                        break
                        
                if exit_price is None:
                    exit_price = curr_opt_price
                    exit_time = post_entry_window.index[-1] if not post_entry_window.empty else ts
                    exit_reason = "EOD_CLOSE"
                    
                pnl_pts = exit_price - est_entry_premium
                pnl_rs = pnl_pts * qty
                
                trades.append({
                    "date": date,
                    "type": pos_type,
                    "entry_time": entry_time.strftime("%H:%M"),
                    "exit_time": exit_time.strftime("%H:%M") if hasattr(exit_time, 'strftime') else str(exit_time),
                    "entry_premium": est_entry_premium,
                    "exit_premium": exit_price,
                    "pnl_pts": pnl_pts,
                    "pnl_rs": pnl_rs,
                    "reason": exit_reason,
                    "trailed": trailed
                })
                break

            # Bearish Breakout
            elif not trade_taken and spot_price <= (m_low - buffer_pts):
                trade_taken = True
                traded_days += 1
                
                entry_time = ts
                entry_spot = spot_price
                pos_type = "PE"
                
                est_entry_premium = spot_price * 0.0075
                spot_risk = m_midpoint - spot_price
                opt_risk_pts = spot_risk * delta
                sl_premium = max(1.0, est_entry_premium - opt_risk_pts)
                target_premium = est_entry_premium + (opt_risk_pts * rr_ratio)
                
                curr_sl = sl_premium
                trailed = False
                exit_price = None
                exit_time = None
                exit_reason = None
                
                post_entry_window = day_df[day_df.index > ts]
                for p_ts, p_row in post_entry_window.iterrows():
                    p_spot = p_row['close']
                    p_time = p_row['time']
                    
                    # Bearish spot move increases PE option price
                    spot_diff = entry_spot - p_spot
                    curr_opt_price = max(0.5, est_entry_premium + (spot_diff * delta))
                    
                    if curr_opt_price >= target_premium:
                        exit_price = target_premium
                        exit_time = p_ts
                        exit_reason = "TARGET_HIT"
                        break
                        
                    if not trailed and curr_opt_price >= (est_entry_premium + opt_risk_pts):
                        curr_sl = est_entry_premium
                        trailed = True
                        
                    if curr_opt_price <= curr_sl:
                        exit_price = curr_sl
                        exit_time = p_ts
                        exit_reason = "SL_HIT_BREAKEVEN" if trailed else "SL_HIT_INITIAL"
                        break
                        
                    if p_time >= datetime.time(15, 15):
                        exit_price = curr_opt_price
                        exit_time = p_ts
                        exit_reason = "TIME_EXIT"
                        break
                        
                if exit_price is None:
                    exit_price = curr_opt_price
                    exit_time = post_entry_window.index[-1] if not post_entry_window.empty else ts
                    exit_reason = "EOD_CLOSE"
                    
                pnl_pts = exit_price - est_entry_premium
                pnl_rs = pnl_pts * qty
                
                trades.append({
                    "date": date,
                    "type": pos_type,
                    "entry_time": entry_time.strftime("%H:%M"),
                    "exit_time": exit_time.strftime("%H:%M") if hasattr(exit_time, 'strftime') else str(exit_time),
                    "entry_premium": est_entry_premium,
                    "exit_premium": exit_price,
                    "pnl_pts": pnl_pts,
                    "pnl_rs": pnl_rs,
                    "reason": exit_reason,
                    "trailed": trailed
                })
                break

    trades_df = pd.DataFrame(trades)
    
    print(f"Total Trading Sessions Evaluated : {total_days}")
    print(f"Sessions Traded                  : {traded_days}")
    print(f"Filter Rejected Sessions (Range) : {filter_rejected_days}")
    print(f"No Breakout Before 10:00 AM      : {no_breakout_days}")

    if not trades_df.empty:
        total_pnl = trades_df['pnl_rs'].sum()
        wins = trades_df[trades_df['pnl_rs'] > 0]
        losses = trades_df[trades_df['pnl_rs'] <= 0]
        win_rate = (len(wins) / len(trades_df)) * 100
        avg_win = wins['pnl_rs'].mean() if not wins.empty else 0.0
        avg_loss = abs(losses['pnl_rs'].mean()) if not losses.empty else 0.0
        profit_factor = (wins['pnl_rs'].sum() / abs(losses['pnl_rs'].sum())) if not losses.empty and losses['pnl_rs'].sum() != 0 else 999.0

        print("\n--------------------------------------------------------")
        print("                PERFORMANCE METRICS SUMMARY             ")
        print("--------------------------------------------------------")
        print(f"Total Net PnL (INR)  : ₹{total_pnl:,.2f}")
        print(f"Total Executed Trades: {len(trades_df)}")
        print(f"Win Rate             : {win_rate:.2f}% ({len(wins)} W / {len(losses)} L)")
        print(f"Profit Factor        : {profit_factor:.2f}")
        print(f"Average Win (INR)    : ₹{avg_win:,.2f}")
        print(f"Average Loss (INR)   : ₹{avg_loss:,.2f}")
        print(f"Target Hits          : {len(trades_df[trades_df['reason'] == 'TARGET_HIT'])}")
        print(f"SL Hits (Initial)    : {len(trades_df[trades_df['reason'] == 'SL_HIT_INITIAL'])}")
        print(f"SL Hits (Break-even) : {len(trades_df[trades_df['reason'] == 'SL_HIT_BREAKEVEN'])}")
        print("--------------------------------------------------------\n")
        
        print("Recent Executed Trades Sample:")
        print(trades_df[['date', 'type', 'entry_time', 'exit_time', 'entry_premium', 'exit_premium', 'pnl_rs', 'reason']].tail(10).to_string(index=False))
    else:
        print("\nNo trades executed matching breakout criteria during the dataset window.")

if __name__ == "__main__":
    df = fetch_banknifty_data()
    if df is not None and not df.empty:
        run_3min_orb_quant_backtest(df)
    else:
        print("[Error] No data available for backtesting.")
