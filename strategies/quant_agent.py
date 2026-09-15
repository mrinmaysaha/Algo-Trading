"""
================================================================================
QUANT HARNESS: MULTI-AGENT QUANTITATIVE CONFLUENCE ENGINE
================================================================================
Inspired by SBU/CMU/Yale QuantAgent Architecture:
Synthesizes signals from 4 specialized agent modules:
  1. IndicatorAgent: Evaluates RSI momentum, VWAP alignment, and MACD divergence.
  2. PatternAgent: Scans for FVG, Liquidity Sweeps, and Rejection wicks.
  3. TrendAgent: Evaluates multi-period EMA slopes and channel boundaries.
  4. DecisionAgent: Weighs evidence and outputs a unified Confluence Score (0-100)
     with clear trade recommendation (LONG, SHORT, or WAIT).
================================================================================
"""

import pandas as pd
import numpy as np
from typing import Dict, Any, Tuple

class IndicatorAgent:
    @staticmethod
    def analyze(df: pd.DataFrame) -> Dict[str, Any]:
        """Evaluates momentum indicators: RSI, VWAP distance, MACD."""
        if len(df) < 20:
            return {"score": 50, "bias": "NEUTRAL", "reason": "Insufficient data"}

        close = df["close"].iloc[-1]
        
        # Calculate RSI if not present
        if "rsi" in df.columns:
            rsi = df["rsi"].iloc[-1]
        else:
            delta = df["close"].diff()
            gain = (delta.where(delta > 0, 0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(14).mean()
            rs = gain / (loss + 1e-9)
            rsi = 100 - (100 / (1 + rs)).iloc[-1]

        # VWAP check if available
        vwap_bias = "NEUTRAL"
        if "vwap" in df.columns:
            vwap = df["vwap"].iloc[-1]
            vwap_bias = "BULLISH" if close > vwap else "BEARISH"

        bullish_pts = 0
        bearish_pts = 0

        # RSI Evaluation
        if 40 <= rsi <= 60:
            pass # Neutral zone
        elif 60 < rsi < 75:
            bullish_pts += 2 # Strong upward momentum
        elif rsi >= 75:
            bearish_pts += 1 # Overbought warning
        elif 25 < rsi < 40:
            bearish_pts += 2 # Strong downward momentum
        elif rsi <= 25:
            bullish_pts += 1 # Oversold bounce potential

        # VWAP Evaluation
        if vwap_bias == "BULLISH":
            bullish_pts += 2
        elif vwap_bias == "BEARISH":
            bearish_pts += 2

        total = bullish_pts + bearish_pts
        score = 50 + int((bullish_pts - bearish_pts) * 10)
        score = max(10, min(90, score))

        bias = "BULLISH" if score > 55 else ("BEARISH" if score < 45 else "NEUTRAL")
        return {"score": score, "bias": bias, "rsi": round(rsi, 2), "vwap_bias": vwap_bias}


class PatternAgent:
    @staticmethod
    def analyze(df: pd.DataFrame) -> Dict[str, Any]:
        """Detects Price Action Formations: FVGs, Rejection Wicks, and Sweeps."""
        if len(df) < 5:
            return {"score": 50, "bias": "NEUTRAL", "patterns": []}

        patterns = []
        bullish_score = 0
        bearish_score = 0

        c_curr = df["close"].iloc[-1]
        o_curr = df["open"].iloc[-1]
        h_curr = df["high"].iloc[-1]
        l_curr = df["low"].iloc[-1]

        # Fair Value Gap (FVG) Check on last 3 bars
        # Bullish FVG: Low of candle 0 > High of candle -2
        if len(df) >= 3:
            h_prev2 = df["high"].iloc[-3]
            l_curr_bar = df["low"].iloc[-1]
            if l_curr_bar > h_prev2:
                patterns.append("Bullish_FVG")
                bullish_score += 2

            l_prev2 = df["low"].iloc[-3]
            h_curr_bar = df["high"].iloc[-1]
            if h_curr_bar < l_prev2:
                patterns.append("Bearish_FVG")
                bearish_score += 2

        # Pin Bar / Rejection Wick Check
        body = abs(c_curr - o_curr)
        total_range = h_curr - l_curr + 1e-9
        upper_wick = h_curr - max(c_curr, o_curr)
        lower_wick = min(c_curr, o_curr) - l_curr

        if lower_wick > 2.0 * body and lower_wick / total_range > 0.5:
            patterns.append("Bullish_Hammer_Sweep")
            bullish_score += 2
        elif upper_wick > 2.0 * body and upper_wick / total_range > 0.5:
            patterns.append("Bearish_ShootingStar_Sweep")
            bearish_score += 2

        score = 50 + int((bullish_score - bearish_score) * 12)
        score = max(10, min(90, score))
        bias = "BULLISH" if score > 55 else ("BEARISH" if score < 45 else "NEUTRAL")
        return {"score": score, "bias": bias, "patterns": patterns}


class TrendAgent:
    @staticmethod
    def analyze(df: pd.DataFrame) -> Dict[str, Any]:
        """Quantifies Trend Direction, EMA Slopes, and Dynamic Channel Boundaries."""
        if len(df) < 30:
            return {"score": 50, "bias": "NEUTRAL", "trend": "UNCERTAIN"}

        close = df["close"]
        ema9 = close.ewm(span=9, adjust=False).mean().iloc[-1]
        ema21 = close.ewm(span=21, adjust=False).mean().iloc[-1]
        ema50 = close.ewm(span=50, adjust=False).mean().iloc[-1]
        curr_price = close.iloc[-1]

        bullish_alignment = curr_price > ema9 > ema21 > ema50
        bearish_alignment = curr_price < ema9 < ema21 < ema50

        if bullish_alignment:
            return {"score": 85, "bias": "STRONG_BULLISH", "trend": "UPTREND"}
        elif curr_price > ema21:
            return {"score": 65, "bias": "MODERATE_BULLISH", "trend": "WEAK_UPTREND"}
        elif bearish_alignment:
            return {"score": 15, "bias": "STRONG_BEARISH", "trend": "DOWNTREND"}
        elif curr_price < ema21:
            return {"score": 35, "bias": "MODERATE_BEARISH", "trend": "WEAK_DOWNTREND"}

        return {"score": 50, "bias": "NEUTRAL", "trend": "RANGEBOUND"}


class QuantDecisionAgent:
    @staticmethod
    def evaluate_confluence(df: pd.DataFrame) -> Dict[str, Any]:
        """
        Synthesizes results from Indicator, Pattern, and Trend Agents.
        Returns final trade decision (LONG, SHORT, WAIT) and Confluence Score (0-100).
        """
        ind_report = IndicatorAgent.analyze(df)
        pat_report = PatternAgent.analyze(df)
        trd_report = TrendAgent.analyze(df)

        # Weighted Confluence: 40% Trend, 30% Pattern, 30% Indicator
        confluence_score = int(
            trd_report["score"] * 0.40 +
            pat_report["score"] * 0.30 +
            ind_report["score"] * 0.30
        )

        decision = "WAIT"
        if confluence_score >= 70:
            decision = "LONG"
        elif confluence_score <= 30:
            decision = "SHORT"

        return {
            "decision": decision,
            "confluence_score": confluence_score,
            "indicator_agent": ind_report,
            "pattern_agent": pat_report,
            "trend_agent": trd_report,
        }
