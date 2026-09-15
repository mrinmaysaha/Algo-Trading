---
name: quant-agent
description: >-
  Multi-agent quantitative signal and confluence engine inspired by QuantHarness / QuantAgent.
  Deploys IndicatorAgent, PatternAgent, and TrendAgent to score confluence before entering trades.
---

# QuantAgent Confluence Skill

This skill implements a multi-agent quantitative framework for evaluating market state before strategy order placement.

## Multi-Agent Hierarchy

1. **Indicator Agent**: Calculates momentum metrics (RSI, VWAP bias, MACD).
2. **Pattern Agent**: Identifies Price Action formations (Fair Value Gaps, Liquidity Sweeps, rejection wicks).
3. **Trend Agent**: Calculates multi-period EMA structure (EMA 9/21/50 alignment) and trend direction.
4. **Decision Agent**: Synthesizes agent inputs into a weighted **Confluence Score (0 to 100)**:
   - **Score >= 70**: `LONG` signal confirmed.
   - **Score <= 30**: `SHORT` signal confirmed.
   - **Score 31 to 69**: `WAIT` (Market in chop / lack of consensus).

## How to Integrate in OpenAlgo Strategies

```python
from strategies.quant_agent import QuantDecisionAgent

# In strategy signal generation:
confluence = QuantDecisionAgent.evaluate_confluence(df_candles)

if confluence["decision"] == "LONG" and signal == "BUY":
    logger.info(f"[CONFLUENCE CONFIRMED] Score: {confluence['confluence_score']}")
    # Proceed to enter trade
elif confluence["decision"] == "WAIT":
    logger.info(f"[CONFLUENCE REJECT] Agents recommend WAIT. Score: {confluence['confluence_score']}")
    return
```
