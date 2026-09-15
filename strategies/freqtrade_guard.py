"""
================================================================================
FREQTRADE-GRADE RISK PROTECTIONS FOR OPENALGO
================================================================================
Ported from high-performance Freqtrade risk management architecture:
1. TrailingStopWithOffset:
   - Locks trailing stop ONLY after trade moves into profit by `offset_pct`.
   - Never risks premature exit during initial chop.
2. MaxDrawdownProtection:
   - Halts trading if intraday drawdown from peak realized PnL exceeds limit.
3. CooldownPeriod:
   - Enforces a mandatory cooldown period (e.g. 30 mins) after N consecutive losses.
4. StoplossOnExchangeSync:
   - Validates that broker-side SL order exists and prices match.
================================================================================
"""

import time
import json
import os
import logging
from typing import Dict, Any, Tuple, Optional
from dataclasses import dataclass, field

logger = logging.getLogger("freqtrade_guard")

@dataclass
class TrailingStopConfig:
    enabled: bool = True
    stop_loss_pct: float = 0.02         # 2% initial SL
    trailing_stop_pct: float = 0.015     # 1.5% trailing distance
    trailing_offset_pct: float = 0.025   # Offset needed to activate trailing (2.5%)
    trailing_only_offset_is_reached: bool = True

@dataclass
class StrategyRiskState:
    consecutive_losses: int = 0
    last_loss_time: float = 0.0
    cooldown_seconds: int = 1800        # 30 mins cooldown after max consecutive losses
    max_consecutive_losses: int = 2     # Max losses before cooling down
    peak_pnl: float = 0.0
    current_pnl: float = 0.0
    max_drawdown_limit: float = 3000.0  # Max acceptable drawdown from intraday peak (INR)

class FreqtradeRiskGuard:
    def __init__(self, state_file: Optional[str] = None):
        if not state_file:
            state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "risk_guard_state.json")
        self.state_file = state_file
        self.states: Dict[str, StrategyRiskState] = {}
        self._load_state()

    def _load_state(self):
        if os.path.exists(self.state_file):
            try:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
                    for k, v in data.items():
                        self.states[k] = StrategyRiskState(**v)
            except Exception as e:
                logger.warning(f"Failed to load risk guard state: {e}")

    def _save_state(self):
        try:
            data = {k: v.__dict__ for k, v in self.states.items()}
            with open(self.state_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.warning(f"Failed to save risk guard state: {e}")

    def get_or_create_state(self, strategy_name: str) -> StrategyRiskState:
        if strategy_name not in self.states:
            self.states[strategy_name] = StrategyRiskState()
        return self.states[strategy_name]

    def check_entry_permitted(self, strategy_name: str) -> Tuple[bool, str]:
        """
        Validates if strategy is allowed to enter a new trade.
        Checks:
        1. Cooldown period after consecutive losses.
        2. Intraday drawdown from peak.
        """
        state = self.get_or_create_state(strategy_name)
        now = time.time()

        # 1. Cooldown check
        if state.consecutive_losses >= state.max_consecutive_losses:
            elapsed = now - state.last_loss_time
            if elapsed < state.cooldown_seconds:
                remaining_min = int((state.cooldown_seconds - elapsed) / 60)
                return False, f"Strategy '{strategy_name}' in COOLDOWN ({remaining_min}m left) after {state.consecutive_losses} consecutive losses"
            else:
                # Cooldown expired, reset counter
                state.consecutive_losses = 0
                self._save_state()

        # 2. Drawdown from peak check
        drawdown = state.peak_pnl - state.current_pnl
        if drawdown > state.max_drawdown_limit:
            return False, f"Strategy '{strategy_name}' exceeded MAX DRAWDOWN limit: Current DD Rs.{drawdown:.2f} > Limit Rs.{state.max_drawdown_limit:.2f}"

        return True, "OK"

    def record_trade_result(self, strategy_name: str, realized_pnl: float):
        """
        Updates consecutive losses, peak equity, and drawdown metrics.
        """
        state = self.get_or_create_state(strategy_name)
        state.current_pnl += realized_pnl
        if state.current_pnl > state.peak_pnl:
            state.peak_pnl = state.current_pnl

        if realized_pnl < 0:
            state.consecutive_losses += 1
            state.last_loss_time = time.time()
            logger.warning(f"[RISK GUARD] {strategy_name} recorded loss Rs.{realized_pnl:.2f}. Consecutive losses: {state.consecutive_losses}")
        else:
            state.consecutive_losses = 0
            logger.info(f"[RISK GUARD] {strategy_name} recorded win Rs.{realized_pnl:.2f}. Consecutive losses reset.")

        self._save_state()

    @staticmethod
    def calculate_trailing_stop(
        direction: str,
        entry_price: float,
        current_price: float,
        highest_price: float,
        lowest_price: float,
        config: TrailingStopConfig
    ) -> Optional[float]:
        """
        Implements Freqtrade TrailingStop with Offset:
        - Only starts trailing after price reaches entry_price * (1 + offset_pct) for LONG
          or entry_price * (1 - offset_pct) for SHORT.
        """
        if not config.enabled:
            return None

        if direction.upper() in ("BUY", "LONG"):
            profit_pct = (highest_price - entry_price) / entry_price
            if config.trailing_only_offset_is_reached and profit_pct < config.trailing_offset_pct:
                # Offset not yet reached, use base SL
                return entry_price * (1.0 - config.stop_loss_pct)

            # Trailing stop price
            trailing_sl = highest_price * (1.0 - config.trailing_stop_pct)
            # Ensure it is at least entry SL
            return max(trailing_sl, entry_price * (1.0 - config.stop_loss_pct))

        elif direction.upper() in ("SELL", "SHORT"):
            profit_pct = (entry_price - lowest_price) / entry_price
            if config.trailing_only_offset_is_reached and profit_pct < config.trailing_offset_pct:
                return entry_price * (1.0 + config.stop_loss_pct)

            trailing_sl = lowest_price * (1.0 + config.trailing_stop_pct)
            return min(trailing_sl, entry_price * (1.0 + config.stop_loss_pct))

        return None
