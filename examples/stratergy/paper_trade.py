import pandas as pd
import numpy as np
import yfinance as yf 

# 1. Fetch Data
print("Fetching 5m historical data...")
df = yf.download("RELIANCE.NS", interval="5m", period="60d")

# ---------------------------------------------------------
# THE FIX: Aggressively flatten yfinance MultiIndex columns
# ---------------------------------------------------------
# Newer versions of yfinance return a tuple like ('Close', 'RELIANCE.NS') 
# This list comprehension forces columns to be standard flat strings (e.g., 'Close')
if isinstance(df.columns, pd.MultiIndex):
    df.columns = [col[0] for col in df.columns]

df.dropna(inplace=True)

# 2. Define Risk Parameters
TARGET_PCT = 0.02
SL_PCT = 0.01
TRAIL_PCT = 0.002
PULLBACK_PCT = 0.001

# Adjusted static levels to match recent Reliance price action for testing
SUPPORT = 1270.0
RESISTANCE = 1350.0

trades = []
in_position = False
pos_dir = None
entry_price = 0.0
sl = 0.0
target = 0.0
trail_sl = 0.0
extreme_price = 0.0 

# 3. Core Backtest Loop
for i in range(2, len(df)):
    c1 = df.iloc[i-2]
    c2 = df.iloc[i-1]
    c3 = df.iloc[i] 
    
    # Manage Open Positions
    if in_position:
        if pos_dir == "BUY":
            extreme_price = max(extreme_price, float(c3['High']))
            trail_sl = max(trail_sl, extreme_price * (1 - TRAIL_PCT))
            
            if float(c3['Low']) <= trail_sl:
                trades.append({"type": "BUY", "entry": entry_price, "exit": trail_sl, "pnl": (trail_sl - entry_price)/entry_price})
                in_position = False
            elif float(c3['High']) >= target:
                trades.append({"type": "BUY", "entry": entry_price, "exit": target, "pnl": TARGET_PCT})
                in_position = False
                
        elif pos_dir == "SELL":
            extreme_price = min(extreme_price, float(c3['Low']))
            trail_sl = min(trail_sl, extreme_price * (1 + TRAIL_PCT))
            
            if float(c3['High']) >= trail_sl:
                trades.append({"type": "SELL", "entry": entry_price, "exit": trail_sl, "pnl": (entry_price - trail_sl)/entry_price})
                in_position = False
            elif float(c3['Low']) <= target:
                trades.append({"type": "SELL", "entry": entry_price, "exit": target, "pnl": TARGET_PCT})
                in_position = False
        continue

    # Signal Generation
    # Explicitly casting to float ensures Pandas evaluates this as a clean number
    c1_close = float(c1['Close'])
    c2_close = float(c2['Close'])
    
    if c1_close > RESISTANCE and c2_close > c1_close:
        entry_level = c2_close * (1 - PULLBACK_PCT)
        if float(c3['Low']) <= entry_level:
            in_position = True
            pos_dir = "BUY"
            entry_price = entry_level
            target = entry_price * (1 + TARGET_PCT)
            sl = entry_price * (1 - SL_PCT)
            trail_sl = sl
            extreme_price = entry_price
            
    elif c1_close < SUPPORT and c2_close < c1_close:
        entry_level = c2_close * (1 + PULLBACK_PCT)
        if float(c3['High']) >= entry_level:
            in_position = True
            pos_dir = "SELL"
            entry_price = entry_level
            target = entry_price * (1 - TARGET_PCT)
            sl = entry_price * (1 + SL_PCT)
            trail_sl = sl
            extreme_price = entry_price

# 4. Results
if trades:
    results = pd.DataFrame(trades)
    print(f"Total Trades: {len(results)}")
    print(f"Win Rate: {len(results[results['pnl'] > 0]) / len(results) * 100:.2f}%")
    print(f"Net Return: {results['pnl'].sum() * 100:.2f}%")
else:
    print("0 Trades Executed. S/R levels were not breached.")