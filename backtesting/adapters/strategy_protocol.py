# backtesting/adapters/strategy_protocol.py
"""
Universal Strategy Protocol & Adapters for Live Classes and Script-Based Strategies.
Provides strategy-family signal evaluation (ORB, Post10 OB+VWAP, SMC FVG, Liquidity Sweep, Scalper).
"""
import math
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple, Any, NamedTuple
import pandas as pd
import numpy as np


class Signal(NamedTuple):
    action: str          # "ENTER" | "EXIT" | "HOLD"
    option_type: Optional[str] = None  # "CE" / "PE"
    reason: str = ""


class StrategyProtocol(ABC):
    """Universal contract for both class-based strategies and generic scripts."""

    @abstractmethod
    def evaluate_entry_signal(self, df_slice: pd.DataFrame, symbol: str) -> Optional[Signal]:
        pass

    def update_trailing_sl(self, pos_state: Dict, current_high: float, current_low: float, atr_val: float) -> Tuple[float, bool]:
        return pos_state["stop_loss"], pos_state.get("tsl_activated", False)


class LiveStrategyAdapter(StrategyProtocol):
    """Wraps live strategy classes and modules to ensure live and backtest paths stay unified."""

    def __init__(self, live_instance: Any, config: Dict, strategy_path: str = ""):
        self.live = live_instance
        self.cfg = config or {}
        self.strategy_path = str(strategy_path or "").lower()
        self.strategy_name = str(self.cfg.get("strategy_name", "")).lower()

    def _determine_strategy_family(self) -> str:
        ident = f"{self.strategy_path} {self.strategy_name}"
        if "orb" in ident:
            return "ORB"
        elif any(k in ident for k in ["post10", "institutional", "ob_vwap", "vwap"]):
            return "POST10"
        elif any(k in ident for k in ["smc", "fvg", "zerolag"]):
            return "SMC_FVG"
        elif any(k in ident for k in ["sweep", "liquid"]):
            return "LIQUID_SWEEP"
        elif any(k in ident for k in ["prime", "scalp"]):
            return "PRIME_SCALPER"
        elif any(k in ident for k in ["commodity", "gold", "silver", "crude"]):
            return "COMMODITY"
        return "GENERAL"

    def evaluate_entry_signal(self, df_slice: pd.DataFrame, symbol: str) -> Optional[Signal]:
        if len(df_slice) < 4:
            return None

        # 1. Inspect live instance / module for direct signal generation methods
        if self.live is not None:
            methods = [
                "evaluate_signal_from_dataframe", "evaluate_signal", "get_signal",
                "generate_signal", "evaluate", "on_bar", "on_candle", "on_data",
                "process_bar", "evaluate_entry", "_execute_signal"
            ]
            for m in methods:
                if hasattr(self.live, m):
                    try:
                        sig_val = getattr(self.live, m)(df_slice, symbol)
                        if isinstance(sig_val, str) and sig_val.upper() in ["CE", "PE", "LONG", "SHORT", "BUY", "SELL"]:
                            opt = "CE" if sig_val.upper() in ["CE", "LONG", "BUY"] else "PE"
                            return Signal(action="ENTER", option_type=opt, reason=f"METHOD_{m.upper()}")
                    except Exception:
                        pass

        # 2. Evaluate Strategy-Family Specific Logic
        strat_family = self._determine_strategy_family()
        latest = df_slice.iloc[-1]
        t_curr = pd.to_datetime(latest["datetime"])
        time_str = t_curr.strftime("%H:%M")
        now_time = t_curr.time()

        df_slice_copy = df_slice.copy()
        df_slice_copy["date_val"] = pd.to_datetime(df_slice_copy["datetime"]).dt.date
        curr_date = t_curr.date()
        today_bars = df_slice_copy[df_slice_copy["date_val"] == curr_date]

        # -------------------------------------------------------------
        # A. 3-Minute / 5-Minute Opening Range Breakout (ORB) Strategy
        # -------------------------------------------------------------
        if strat_family == "ORB":
            if not today_bars.empty:
                first_bar = today_bars.iloc[0]
                first_bar_time = pd.to_datetime(first_bar["datetime"]).strftime("%H:%M")
                mother_high = float(first_bar["high"])
                mother_low = float(first_bar["low"])
                mother_range = mother_high - mother_low

                range_min = float(self.cfg.get("range_filter_min", 35.0))
                range_max = float(self.cfg.get("range_filter_max", 250.0))
                buffer_val = float(self.cfg.get("breakout_buffer", 3.0))

                if range_min <= mother_range <= range_max:
                    # Strict 10:00 AM Cutoff for ORB Entries
                    if time_str > first_bar_time and time_str <= "10:00":
                        prev_bars = today_bars[today_bars["datetime"] < latest["datetime"]]
                        # Ensure only the FIRST valid breakout triggers the daily trade
                        prev_broken_ce = any(float(b["close"]) > (mother_high + buffer_val) for _, b in prev_bars.iloc[1:].iterrows()) if len(prev_bars) > 1 else False
                        prev_broken_pe = any(float(b["close"]) < (mother_low - buffer_val) for _, b in prev_bars.iloc[1:].iterrows()) if len(prev_bars) > 1 else False

                        if float(latest["close"]) > (mother_high + buffer_val) and not prev_broken_ce:
                            return Signal(action="ENTER", option_type="CE", reason="ORB_BULLISH_BREAKOUT")
                        elif float(latest["close"]) < (mother_low - buffer_val) and not prev_broken_pe:
                            return Signal(action="ENTER", option_type="PE", reason="ORB_BEARISH_BREAKOUT")

        # -------------------------------------------------------------
        # B. Post10 Institutional Order Block & VWAP Multi-Confluence
        # -------------------------------------------------------------
        elif strat_family == "POST10":
            # Session Time Check (Morning: 09:30-11:15 | Afternoon: 12:45-14:15)
            is_morning = "09:30" <= time_str <= "11:15"
            is_afternoon = "12:45" <= time_str <= "14:15"

            if is_morning or is_afternoon:
                ob_bull = False
                ob_bear = False
                if len(df_slice) >= 4 and df_slice.iloc[-3].get('bullish_impulse', False):
                    ob_high = df_slice.iloc[-4]['high']
                    if latest['low'] <= ob_high and latest['close'] > ob_high:
                        ob_bull = True

                if len(df_slice) >= 4 and df_slice.iloc[-3].get('bearish_impulse', False):
                    ob_low = df_slice.iloc[-4]['low']
                    if latest['high'] >= ob_low and latest['close'] < ob_low:
                        ob_bear = True

                vwap_val = float(latest.get('vwap', latest['close']))
                long_zone = (latest['close'] >= vwap_val) or ob_bull
                short_zone = (latest['close'] <= vwap_val) or ob_bear

                ema_bull = latest['ema_fast'] > latest['ema_slow']
                ema_bear = latest['ema_fast'] < latest['ema_slow']
                rsi_bull = latest['rsi'] > 50
                rsi_bear = latest['rsi'] < 50
                macd_bull = (latest['macd_line'] > latest['macd_signal']) and (latest['macd_hist'] > 0)
                macd_bear = (latest['macd_line'] < latest['macd_signal']) and (latest['macd_hist'] < 0)
                st_bull = latest['st_direction'] == 1
                st_bear = latest['st_direction'] == -1

                buy_score = sum([ema_bull, rsi_bull, macd_bull, st_bull])
                sell_score = sum([ema_bear, rsi_bear, macd_bear, st_bear])
                req_score = self.cfg.get("min_confluence_score_global", 3)

                if long_zone and st_bull and (buy_score >= req_score):
                    return Signal(action="ENTER", option_type="CE", reason="POST10_INSTITUTIONAL_BUY")
                elif short_zone and st_bear and (sell_score >= req_score):
                    return Signal(action="ENTER", option_type="PE", reason="POST10_INSTITUTIONAL_SELL")

        # -------------------------------------------------------------
        # C. Smart Money Concepts / Fair Value Gap (SMC / FVG)
        # -------------------------------------------------------------
        elif strat_family == "SMC_FVG":
            if len(df_slice) >= 4:
                # Bullish FVG: Candle 1 High < Candle 3 Low
                c1_high = df_slice.iloc[-3]["high"]
                c3_low = latest["low"]
                c2_open = df_slice.iloc[-2]["open"]
                c2_close = df_slice.iloc[-2]["close"]

                if c3_low > c1_high and (c2_close > c2_open) and latest["close"] > latest.get("vwap", latest["close"]):
                    return Signal(action="ENTER", option_type="CE", reason="SMC_BULLISH_FVG")

                # Bearish FVG: Candle 1 Low > Candle 3 High
                c1_low = df_slice.iloc[-3]["low"]
                c3_high = latest["high"]
                if c3_high < c1_low and (c2_close < c2_open) and latest["close"] < latest.get("vwap", latest["close"]):
                    return Signal(action="ENTER", option_type="PE", reason="SMC_BEARISH_FVG")

        # -------------------------------------------------------------
        # D. Liquidity Sweep & Institutional Rejection
        # -------------------------------------------------------------
        elif strat_family == "LIQUID_SWEEP":
            if len(df_slice) >= 10:
                lookback_high = df_slice.iloc[-10:-1]["high"].max()
                lookback_low = df_slice.iloc[-10:-1]["low"].min()

                # Bullish sweep of lows (Liquidity grab below prior low with close back inside)
                if latest["low"] < lookback_low and latest["close"] > lookback_low:
                    return Signal(action="ENTER", option_type="CE", reason="LIQUIDITY_SWEEP_BUY")

                # Bearish sweep of highs (Liquidity grab above prior high with close back inside)
                if latest["high"] > lookback_high and latest["close"] < lookback_high:
                    return Signal(action="ENTER", option_type="PE", reason="LIQUIDITY_SWEEP_SELL")

        # -------------------------------------------------------------
        # E. General / Scalper / Technical Confluence
        # -------------------------------------------------------------
        prev = df_slice.iloc[-2]
        if "st_direction" in df_slice.columns:
            if prev["st_direction"] == -1 and latest["st_direction"] == 1:
                return Signal(action="ENTER", option_type="CE", reason="SUPERTREND_REVERSAL_BUY")
            elif prev["st_direction"] == 1 and latest["st_direction"] == -1:
                return Signal(action="ENTER", option_type="PE", reason="SUPERTREND_REVERSAL_SELL")

        if "ema_fast" in df_slice.columns and "ema_slow" in df_slice.columns:
            if prev["ema_fast"] <= prev["ema_slow"] and latest["ema_fast"] > latest["ema_slow"]:
                return Signal(action="ENTER", option_type="CE", reason="EMA_CROSSOVER_BUY")
            elif prev["ema_fast"] >= prev["ema_slow"] and latest["ema_fast"] < latest["ema_slow"]:
                return Signal(action="ENTER", option_type="PE", reason="EMA_CROSSUNDER_SELL")

        return None

    def update_trailing_sl(self, pos_state: Dict, current_high: float, current_low: float, atr_val: float) -> Tuple[float, bool]:
        position = pos_state["position"]
        entry_spot = pos_state["entry_spot"]
        current_sl = pos_state["stop_loss"]
        tsl_activated = pos_state["tsl_activated"]

        # Support both Range-based and ATR-based step-locking
        unit_scale = pos_state.get("unit_range") or atr_val
        activation_mult = float(self.cfg.get("act_mult", self.cfg.get("atr_activation_mult", self.cfg.get("tsl_activation_atr_mult", 0.75))))
        step_mult = float(self.cfg.get("step_mult", self.cfg.get("atr_step_mult", self.cfg.get("trail_step_atr_mult", 0.35))))
        step_size = max(0.5, unit_scale * step_mult)
        activation_dist = unit_scale * activation_mult

        if position == "CE":
            if pos_state.get("last_step_high") is None:
                pos_state["last_step_high"] = entry_spot

            if current_high > pos_state["last_step_high"]:
                pos_state["last_step_high"] = current_high

            favorable_gain = pos_state["last_step_high"] - entry_spot

            if favorable_gain >= activation_dist:
                if not tsl_activated:
                    tsl_activated = True
                    pos_state["tsl_activated"] = True

                extra_gain = favorable_gain - activation_dist
                num_steps = int(math.floor(extra_gain / step_size))
                proposed_sl = entry_spot + (0.20 * unit_scale) + (num_steps * step_size)
                if proposed_sl > current_sl:
                    return round(proposed_sl, 2), True

        elif position == "PE":
            if pos_state.get("last_step_low") is None:
                pos_state["last_step_low"] = entry_spot

            if current_low < pos_state["last_step_low"]:
                pos_state["last_step_low"] = current_low

            favorable_gain = entry_spot - pos_state["last_step_low"]

            if favorable_gain >= activation_dist:
                if not tsl_activated:
                    tsl_activated = True
                    pos_state["tsl_activated"] = True

                extra_gain = favorable_gain - activation_dist
                num_steps = int(math.floor(extra_gain / step_size))
                proposed_sl = entry_spot - (0.20 * unit_scale) - (num_steps * step_size)
                if proposed_sl < current_sl:
                    return round(proposed_sl, 2), True

        return current_sl, tsl_activated


class GenericIndicatorStrategy(LiveStrategyAdapter):
    """Fallback Strategy Adapter matching general technical confluence rules."""
    def __init__(self, config: Dict):
        super().__init__(live_instance=None, config=config)
