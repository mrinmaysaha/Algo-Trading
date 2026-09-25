---
name: layman-explainer
description: Translate complex algorithmic trading mechanics, order routing events, risk circuit breakers, PnL breakdowns, and system logs into clear, simple English and intuitive layman language with concrete analogies so anyone can easily understand what happened, why it happened, and what it means for their money.
---

# Layman Explainer Skill (Simple English Communication Engine)

## Purpose
Trading algorithms and server backends produce dense terminal logs, order IDs, timestamps, and multi-leg option mechanics that can be intimidating and confusing. This skill enforces a structured **Plain English & Layman's Terms** standard for all explanations, ensuring the user always knows exactly what their money is doing, why specific orders fired, and whether anything is wrong.

---

## Core Guidelines for Plain English Explanations

1. **Zero Unexplained Jargon**:
   - Instead of *"atomic rollback of orphan wing"*, explain: *"The computer bought the call protection first, but when the put protection was cancelled, it immediately sold the call back so your account wasn't left holding a risky one-sided bet."*
   - Instead of *"already flat on broker cas check"*, explain: *"The algorithm checked your broker account before sending a sell order and saw you had 0 shares/contracts left, so it saved you money by skipping an unnecessary order."*
   - Instead of *"closedQty filtering"*, explain: *"The P&L table only shows completed round-trip trades (where you both bought AND sold). If a position only had an entry and no exit, it hides it until it is closed."*

2. **The 4-Part Layman Breakdown Template**:
   Every operational or trade explanation must follow this intuitive structure:
   - **1. What You Saw:** (Acknowledge the numbers or logs the user saw in their dashboard).
   - **2. What the Algorithm Actually Did:** (Step-by-step story of what happened behind the scenes in everyday terms).
   - **3. The Money Impact (PnL & Safety):** (Did you make or lose money? Were fees saved? Was your capital protected?).
   - **4. Is This Normal / Live Ready?:** (Direct confirmation on whether this behavior is expected and safe for live trading).

3. **Use Simple Visual Analogies**:
   - Compare multi-leg orders (Iron Condors) to buying a 4-legged table: if one leg breaks while setting it up, the system pauses, packs it up, and restarts instead of letting the table collapse.
   - Compare slippage limits to a price limit tag at the store: you tell the broker *"I will buy this only if it costs $100 or less; if the store tries to charge $121, cancel the transaction."*
