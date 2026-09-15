---
name: risk-guard
description: >-
  Freqtrade-grade capital protection and risk management module for OpenAlgo trading strategies.
  Enforces trailing stop offsets, drawdown-from-peak circuit breakers, and consecutive loss cooldowns.
---

# Risk Guard Skill (Freqtrade Protections for OpenAlgo)

This skill enforces battle-tested risk mechanisms inspired by Freqtrade to protect trading capital and prevent revenge trading, bad market regime drawdowns, and unmanaged open positions.

## Core Protections

1. **Trailing Stop With Offset (`TrailingStopWithOffset`):**
   - The trailing stop does **not** move upwards until the trade moves into profit by at least `trailing_offset_pct` (e.g. +2.5%).
   - Eliminates premature stopouts caused by normal entry noise or small fluctuations.
   - Once the offset is reached, the stop trails at `trailing_stop_pct` (e.g. 1.5%) behind the peak price.

2. **Consecutive Loss Cooldown (`CooldownPeriod`):**
   - If a strategy experiences `max_consecutive_losses` (default: 2), it is immediately locked out from taking new entries for `cooldown_seconds` (default: 30 minutes / 1800s).
   - Protects against chop zones and trending reversals where repetitive signals trigger multiple false breakouts.

3. **Intraday Drawdown from Peak (`MaxDrawdownProtection`):**
   - Tracks the highest realized PnL achieved during the day.
   - If equity pulls back from this peak by more than `max_drawdown_limit` (e.g. Rs. 3,000), the strategy stops trading for the rest of the day.

## How to Use in Any OpenAlgo Strategy

```python
from strategies.freqtrade_guard import FreqtradeRiskGuard, TrailingStopConfig

risk_guard = FreqtradeRiskGuard()

# 1. Before entry order:
permitted, reason = risk_guard.check_entry_permitted(STRATEGY_NAME)
if not permitted:
    logger.warning(f"[RISK REJECT] Entry blocked: {reason}")
    return

# 2. While managing open trade:
sl_price = FreqtradeRiskGuard.calculate_trailing_stop(
    direction="BUY",
    entry_price=entry_price,
    current_price=ltp,
    highest_price=peak_high,
    lowest_price=trough_low,
    config=TrailingStopConfig(
        stop_loss_pct=0.02,
        trailing_stop_pct=0.015,
        trailing_offset_pct=0.025
    )
)

# 3. After trade exit:
risk_guard.record_trade_result(STRATEGY_NAME, realized_pnl=pnl)
```
