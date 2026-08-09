# backtesting/adapters/strategy_protocol.py
"""
Universal Strategy Protocol & Adapters for Live Classes and Script-Based Strategies.
"""
import math
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple, Any, NamedTuple
import pandas as pd


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
    """Wraps live strategy classes to ensure live and backtest paths stay unified."""

    def __init__(self, live_instance: Any, config: Dict):
        self.live = live_instance
        self.cfg = config

    def evaluate_entry_signal(self, df_slice: pd.DataFrame, symbol: str) -> Optional[Signal]:
        if len(df_slice) < 5:
            return None

        # Route to strategy instance method if present
        for method_name in ["evaluate_signal_from_dataframe", "evaluate_signal", "get_signal", "generate_signal"]:
            if hasattr(self.live, method_name):
                sig_val = getattr(self.live, method_name)(df_slice, symbol)
                if isinstance(sig_val, str) and sig_val.upper() in ["CE", "PE"]:
                    return Signal(action="ENTER", option_type=sig_val.upper(), reason="LIVE_INSTANCE_SIGNAL")

        latest = df_slice.iloc[-1]
        t_curr = pd.to_datetime(latest["datetime"])
        time_str = t_curr.strftime("%H:%M")

        # --- ORB Breakout Signal Detection ---
        df_slice_copy = df_slice.copy()
        if "datetime" in df_slice_copy.columns:
            df_slice_copy["date_val"] = pd.to_datetime(df_slice_copy["datetime"]).dt.date
            curr_date = t_curr.date()
            today_bars = df_slice_copy[df_slice_copy["date_val"] == curr_date]

            if not today_bars.empty:
                first_bar = today_bars.iloc[0]
                first_bar_time = pd.to_datetime(first_bar["datetime"]).strftime("%H:%M")
                if first_bar_time in ["09:15", "09:18", "09:30"]:
                    mother_high = float(first_bar["high"])
                    mother_low = float(first_bar["low"])
                    buffer_val = float(self.cfg.get("breakout_buffer", 3.0))

                    if time_str > first_bar_time and float(latest["close"]) > (mother_high + buffer_val):
                        return Signal(action="ENTER", option_type="CE", reason="ORB_BULLISH_BREAKOUT")
                    elif time_str > first_bar_time and float(latest["close"]) < (mother_low - buffer_val):
                        return Signal(action="ENTER", option_type="PE", reason="ORB_BEARISH_BREAKOUT")

        # Technical Confluence Fallback
        ob_bull_mitigated = False
        if len(df_slice) >= 4 and df_slice.iloc[-3].get('bullish_impulse', False):
            ob_high = df_slice.iloc[-4]['high']
            if latest['low'] <= ob_high and latest['close'] > ob_high:
                ob_bull_mitigated = True

        ob_bear_mitigated = False
        if len(df_slice) >= 4 and df_slice.iloc[-3].get('bearish_impulse', False):
            ob_low = df_slice.iloc[-4]['low']
            if latest['high'] >= ob_low and latest['close'] < ob_low:
                ob_bear_mitigated = True

        long_zone = (latest['close'] >= latest['vwap']) or ob_bull_mitigated
        short_zone = (latest['close'] <= latest['vwap']) or ob_bear_mitigated

        ema_bull = (latest['ema_fast'] > latest['ema_slow'])
        ema_bear = (latest['ema_fast'] < latest['ema_slow'])

        rsi_bull = latest['rsi'] > 50
        rsi_bear = latest['rsi'] < 50

        macd_bull = (latest['macd_line'] > latest['macd_signal']) and (latest['macd_hist'] > 0)
        macd_bear = (latest['macd_line'] < latest['macd_signal']) and (latest['macd_hist'] < 0)

        vol_spike = latest.get('vol_spike', False)
        adx_bull = (latest['adx'] > self.cfg.get("adx_threshold", 20)) and (latest['di_plus'] > latest['di_minus'])
        adx_bear = (latest['adx'] > self.cfg.get("adx_threshold", 20)) and (latest['di_minus'] > latest['di_plus'])

        buy_score = sum([ema_bull, rsi_bull, macd_bull, vol_spike, adx_bull])
        sell_score = sum([ema_bear, rsi_bear, macd_bear, vol_spike, adx_bear])

        st_bull = latest['st_direction'] == 1
        st_bear = latest['st_direction'] == -1

        req_score = self.cfg.get("min_confluence_score_global", 2)

        if long_zone and st_bull and (buy_score >= req_score):
            return Signal(action="ENTER", option_type="CE", reason="CONFLUENCE_BUY")
        elif short_zone and st_bear and (sell_score >= req_score):
            return Signal(action="ENTER", option_type="PE", reason="CONFLUENCE_SELL")

        return None

    def update_trailing_sl(self, pos_state: Dict, current_high: float, current_low: float, atr_val: float) -> Tuple[float, bool]:
        position = pos_state["position"]
        entry_spot = pos_state["entry_spot"]
        current_sl = pos_state["stop_loss"]
        tsl_activated = pos_state["tsl_activated"]

        activation_mult = self.cfg.get("tsl_activation_atr_mult", 1.0)
        step_mult = self.cfg.get("trail_step_atr_mult", 0.5)
        step_size = atr_val * step_mult

        if position == "CE":
            if pos_state.get("last_step_high") is None:
                pos_state["last_step_high"] = entry_spot

            unrealized_profit_atr = (current_high - entry_spot) / max(0.1, atr_val)

            if unrealized_profit_atr >= activation_mult:
                if not tsl_activated:
                    pos_state["tsl_activated"] = True
                    pos_state["last_step_high"] = current_high

                price_advance = current_high - pos_state["last_step_high"]
                if price_advance >= step_size:
                    num_steps = math.floor(price_advance / step_size)
                    pos_state["last_step_high"] += num_steps * step_size
                    proposed_sl = current_sl + (num_steps * step_size)
                    if proposed_sl > current_sl:
                        return proposed_sl, True

        elif position == "PE":
            if pos_state.get("last_step_low") is None:
                pos_state["last_step_low"] = entry_spot

            unrealized_profit_atr = (entry_spot - current_low) / max(0.1, atr_val)

            if unrealized_profit_atr >= activation_mult:
                if not tsl_activated:
                    pos_state["tsl_activated"] = True
                    pos_state["last_step_low"] = current_low

                price_decline = pos_state["last_step_low"] - current_low
                if price_decline >= step_size:
                    num_steps = math.floor(price_decline / step_size)
                    pos_state["last_step_low"] -= num_steps * step_size
                    proposed_sl = current_sl - (num_steps * step_size)
                    if proposed_sl < current_sl:
                        return proposed_sl, True

        return current_sl, tsl_activated


class GenericIndicatorStrategy(StrategyProtocol):
    """Fallback Strategy Adapter for plain EMA / Supertrend scripts."""

    def __init__(self, config: Dict):
        self.cfg = config

    def evaluate_entry_signal(self, df_slice: pd.DataFrame, symbol: str) -> Optional[Signal]:
        if len(df_slice) < 2:
            return None

        latest = df_slice.iloc[-1]
        prev = df_slice.iloc[-2]

        if "ema_fast" in df_slice.columns and "ema_slow" in df_slice.columns:
            if prev["ema_fast"] <= prev["ema_slow"] and latest["ema_fast"] > latest["ema_slow"]:
                return Signal(action="ENTER", option_type="CE", reason="EMA_CROSSOVER")
            elif prev["ema_fast"] >= prev["ema_slow"] and latest["ema_fast"] < latest["ema_slow"]:
                return Signal(action="ENTER", option_type="PE", reason="EMA_CROSSUNDER")

        if "st_direction" in df_slice.columns:
            if prev["st_direction"] == -1 and latest["st_direction"] == 1:
                return Signal(action="ENTER", option_type="CE", reason="SUPERTREND_BULL")
            elif prev["st_direction"] == 1 and latest["st_direction"] == -1:
                return Signal(action="ENTER", option_type="PE", reason="SUPERTREND_BEAR")

        return None
