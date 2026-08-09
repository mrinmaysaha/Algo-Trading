# backtesting/adapters/technical_engine.py
"""
Technical Indicators Engine for Strategy Signal Generation.
Computes VWAP, ATR, Supertrend, EMAs, RSI, MACD, ADX, and Order Block Impulses.
"""
import numpy as np
import pandas as pd


class TechnicalEngine:
    """Calculates strategy indicators on OHLC DataFrames."""

    @staticmethod
    def calculate_all(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
        if len(df) < 8:
            return df

        df = df.copy()
        if "datetime" in df.columns:
            df['datetime'] = pd.to_datetime(df['datetime'])
        elif "timestamp" in df.columns:
            df['datetime'] = pd.to_datetime(df['timestamp'])
        else:
            df['datetime'] = pd.to_datetime(df.index)

        df['date'] = df['datetime'].dt.date

        # Intraday VWAP
        df['tp'] = (df['high'] + df['low'] + df['close']) / 3.0
        df['tp_vol'] = df['tp'] * df['volume']
        df['cum_tp_vol'] = df.groupby('date')['tp_vol'].cumsum()
        df['cum_vol'] = df.groupby('date')['volume'].cumsum()
        df['vwap'] = np.where(df['cum_vol'] > 0, df['cum_tp_vol'] / df['cum_vol'], df['close'])

        # True Range & ATR
        tr1 = df['high'] - df['low']
        tr2 = (df['high'] - df['close'].shift(1)).abs()
        tr3 = (df['low'] - df['close'].shift(1)).abs()
        df['tr'] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        df['atr'] = df['tr'].rolling(window=cfg.get("supertrend_period", 10)).mean()

        # Supertrend
        hl2 = (df['high'] + df['low']) / 2.0
        multiplier = cfg.get("supertrend_multiplier", 3.0)
        basic_upper = hl2 + (multiplier * df['atr'])
        basic_lower = hl2 - (multiplier * df['atr'])

        st = [0.0] * len(df)
        st_dir = [1] * len(df)
        st[0] = basic_upper.iloc[0]

        for i in range(1, len(df)):
            final_lower = basic_lower.iloc[i] if (basic_lower.iloc[i] > st[i-1] or df['close'].iloc[i-1] < st[i-1]) else st[i-1]
            final_upper = basic_upper.iloc[i] if (basic_upper.iloc[i] < st[i-1] or df['close'].iloc[i-1] > st[i-1]) else st[i-1]

            if st_dir[i-1] == 1:
                if df['close'].iloc[i] < final_lower:
                    st_dir[i] = -1
                    st[i] = final_upper
                else:
                    st_dir[i] = 1
                    st[i] = final_lower
            else:
                if df['close'].iloc[i] > final_upper:
                    st_dir[i] = 1
                    st[i] = final_lower
                else:
                    st_dir[i] = -1
                    st[i] = final_upper

        df['supertrend'] = st
        df['st_direction'] = st_dir

        # Moving Averages
        df['ema_fast'] = df['close'].ewm(span=cfg.get("ema_fast", 20), adjust=False).mean()
        df['ema_slow'] = df['close'].ewm(span=cfg.get("ema_slow", 50), adjust=False).mean()
        df['ema_macro'] = df['close'].ewm(span=cfg.get("ema_macro", 200), adjust=False).mean()

        # RSI
        delta = df['close'].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=cfg.get("rsi_period", 14), min_periods=1).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=cfg.get("rsi_period", 14), min_periods=1).mean()
        rs = gain / loss.replace(0, np.nan)
        df['rsi'] = (100 - (100 / (1 + rs))).fillna(50.0)

        # MACD
        ema12 = df['close'].ewm(span=12, adjust=False).mean()
        ema26 = df['close'].ewm(span=26, adjust=False).mean()
        df['macd_line'] = ema12 - ema26
        df['macd_signal'] = df['macd_line'].ewm(span=9, adjust=False).mean()
        df['macd_hist'] = df['macd_line'] - df['macd_signal']

        # ADX
        up_move = df['high'].diff()
        down_move = df['low'].shift(1) - df['low']
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        adx_p = cfg.get("adx_period", 14)
        atr_14 = df['tr'].rolling(window=adx_p, min_periods=1).mean()
        plus_di = (100 * (pd.Series(plus_dm).rolling(window=adx_p, min_periods=1).mean() / atr_14.replace(0, np.nan))).fillna(0.0)
        minus_di = (100 * (pd.Series(minus_dm).rolling(window=adx_p, min_periods=1).mean() / atr_14.replace(0, np.nan))).fillna(0.0)
        dx = (100 * ((plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan))).fillna(0.0)
        df['adx'] = dx.rolling(window=adx_p, min_periods=1).mean().fillna(0.0)
        df['di_plus'] = plus_di
        df['di_minus'] = minus_di

        # Volume Spike & Order Block Impulse
        df['vol_ma'] = df['volume'].rolling(window=cfg.get("vol_ma_period", 20)).mean()
        df['vol_spike'] = df['volume'] > (df['vol_ma'] * cfg.get("vol_multiplier", 1.5))

        ob_mult = cfg.get("ob_impulse_mult", 1.2)
        df['bullish_impulse'] = (df['close'] - df['open']) > (df['atr'] * ob_mult)
        df['bearish_impulse'] = (df['open'] - df['close']) > (df['atr'] * ob_mult)

        return df
