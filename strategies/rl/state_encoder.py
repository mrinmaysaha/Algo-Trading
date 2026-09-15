# =============================================================================
# OpenAlgo Reinforcement Learning: State Space Feature Encoder
# Encodes continuous market microstructure & execution state into R^12
# =============================================================================

import numpy as np
import pandas as pd
from typing import Dict, Any, List, Optional

class RLStateEncoder:
    """
    Transforms market data bars and execution metrics into a standardized
    continuous state vector for the RL policy network.
    """

    STATE_DIM = 12

    @staticmethod
    def encode(
        df_candles: pd.DataFrame,
        current_pos: int = 0,             # -1: Short, 0: Flat, +1: Long
        unrealized_pnl_pct: float = 0.0,  # e.g., +0.015 (+1.5%)
        holding_bars: int = 0,
        max_holding_bars: int = 24,
        session_elapsed_pct: float = 0.5
    ) -> np.ndarray:
        """
        Extracts 12-dimensional state vector from recent candle window.
        Assumes df_candles has columns: ['open', 'high', 'low', 'close', 'volume']
        Requires at least 25 bars for indicator stabilization.
        """
        if len(df_candles) < 25:
            return np.zeros(RLStateEncoder.STATE_DIM, dtype=np.float32)

        closes = df_candles['close'].values
        highs = df_candles['high'].values
        lows = df_candles['low'].values
        volumes = df_candles['volume'].values
        opens = df_candles['open'].values

        curr_close = closes[-1]
        curr_open = opens[-1]
        curr_high = highs[-1]
        curr_low = lows[-1]

        # 1. 20-EMA & Normalized Distance
        ema20 = pd.Series(closes).ewm(span=20, adjust=False).mean().values[-1]

        # 2. 14-ATR
        tr = np.maximum(highs[1:] - lows[1:], 
                        np.maximum(np.abs(highs[1:] - closes[:-1]), 
                                   np.abs(lows[1:] - closes[:-1])))
        atr14 = float(pd.Series(tr).rolling(14).mean().values[-1]) if len(tr) >= 14 else 1.0
        atr14 = max(atr14, 0.001)

        f1_ema_dist = (curr_close - ema20) / atr14
        f2_volatility_ratio = atr14 / curr_close

        # 3. ADX Approximation (Trend Strength)
        up_move = highs[1:] - highs[:-1]
        down_move = lows[:-1] - lows[1:]
        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
        tr_s = pd.Series(tr).rolling(14).sum().values[-1]
        pdm_s = pd.Series(plus_dm).rolling(14).sum().values[-1] if len(plus_dm) >= 14 else 1.0
        mdm_s = pd.Series(minus_dm).rolling(14).sum().values[-1] if len(minus_dm) >= 14 else 1.0
        plus_di = 100 * (pdm_s / max(tr_s, 1e-5))
        minus_di = 100 * (mdm_s / max(tr_s, 1e-5))
        dx = 100 * abs(plus_di - minus_di) / max(plus_di + minus_di, 1e-5)
        f3_adx = np.clip(dx / 100.0, 0.0, 1.0)

        # 4. 14-RSI
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = pd.Series(gains).rolling(14).mean().values[-1] if len(gains) >= 14 else 0.5
        avg_loss = pd.Series(losses).rolling(14).mean().values[-1] if len(losses) >= 14 else 0.5
        rs = avg_gain / max(avg_loss, 1e-5)
        rsi = 100 - (100 / (1 + rs))
        f4_rsi = np.clip(rsi / 100.0, 0.0, 1.0)

        # 5 & 6. Wick-to-ATR Rejection Ratios
        body_top = max(curr_open, curr_close)
        body_bot = min(curr_open, curr_close)
        f5_upper_wick = np.clip((curr_high - body_top) / atr14, 0.0, 4.0)
        f6_lower_wick = np.clip((body_bot - curr_low) / atr14, 0.0, 4.0)

        # 7. Session Elapsed Normalized (0.0 to 1.0)
        f7_session = np.clip(session_elapsed_pct, 0.0, 1.0)

        # 8. Relative Volume Surge
        vol_sma20 = float(pd.Series(volumes).rolling(20).mean().values[-1]) if len(volumes) >= 20 else 1.0
        f8_rel_vol = np.clip(volumes[-1] / max(vol_sma20, 1e-5), 0.0, 5.0)

        # 9. Current Market Inventory (-1, 0, 1)
        f9_pos = float(current_pos)

        # 10. Unrealized PnL %
        f10_pnl = np.clip(unrealized_pnl_pct * 10.0, -2.0, 2.0)

        # 11. Normalized Holding Duration
        f11_holding = np.clip(holding_bars / max(max_holding_bars, 1), 0.0, 1.0)

        # 12. Structural Proximity (Distance to 20-bar Swing High/Low in ATR)
        swing_high = np.max(highs[-20:])
        swing_low = np.min(lows[-20:])
        dist_high = (swing_high - curr_close) / atr14
        dist_low = (curr_close - swing_low) / atr14
        f12_struct = np.clip(min(dist_high, dist_low), -2.0, 5.0)

        state_vec = np.array([
            f1_ema_dist, f2_volatility_ratio, f3_adx, f4_rsi,
            f5_upper_wick, f6_lower_wick, f7_session, f8_rel_vol,
            f9_pos, f10_pnl, f11_holding, f12_struct
        ], dtype=np.float32)

        return np.nan_to_num(state_vec, nan=0.0, posinf=1.0, neginf=-1.0)
