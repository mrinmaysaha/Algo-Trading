---
name: prompt-optimizer
description: Transform raw, conversational, or vague user prompts into structured, professional, production-grade engineering and quantitative trading instructions. Automatically unpacks intent, defines technical requirements, guards against ambiguities, and drives superior code and architectural outputs.
---

# Prompt Optimizer Skill (Professional Prompt Engineering Engine)

## Purpose
Users often give concise, shorthand, or vague prompts (e.g., *"why did it take 11 trades and settle for 8"*, *"make slippage limit order"*, or *"add supertrend filter"*). This skill establishes the standardized cognitive protocol to instantly unpack, elevate, and structure conversational input into unambiguous, institutional-grade engineering prompts before execution.

---

## 4-Step Transformation Protocol

When a user provides a raw or vague request:

```
[User's Raw Prompt]
       │
       ▼
1. Unpack Core Intent & Hidden Constraints
       │
       ▼
2. Synthesize Professional Architectural Specification
       │
       ▼
3. Audit Edge Cases & Invariants (Tick sizes, multi-instances, state locks)
       │
       ▼
4. Formulate Verified Execution Plan
```

### 1. Unpack Core Intent
- Identify what the user is experiencing (e.g., an unexpected order log, an unhedged leg, high slippage).
- Extract the mathematical objective (e.g., maximize profit factor, eliminate adverse fill spread, limit maximum drawdown).
- Surface implicit constraints (e.g., dual-instance port separation, atomic basket safety, database schemas).

### 2. Synthesize Professional Specification
Re-frame the problem with concrete domain terminology:
- Rather than *"fix slippage"*, specify *"Replace entry MARKET orders with marketable pegged LIMIT orders (LTP + 0.5% buffer) and reject entries where ask exceeds the buffer"*.
- Rather than *"why 11 trades"*, specify *"Audit orderbook transition state from 10:00 PM to 10:01 PM IST across legs, orphan wing safety rollbacks, and basket retry mechanics"*.

### 3. Edge Case & Failure Mode Auditing
Before writing a single line of code, verify:
- What happens if quotes return zero or None?
- What happens if one leg fills and another is cancelled by the exchange?
- Does the change violate risk rules or cross-pollinate ports 5000 vs 5001?
- How will this impact trade frequency, margin requirements, and fees?

### 4. Implementation & Quality Gate
- Run automated dry-runs and historical backtests over relevant intervals.
- Ensure all scratch test files are cleaned up.
- Provide both the technical verification diff and an accessible plain-English summary.
