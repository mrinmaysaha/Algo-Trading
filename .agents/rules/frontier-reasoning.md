# Frontier Deliberative Reasoning & Cognitive Protocol (Opus / Sonnet Parity)

## 1. Multi-Hypothesis Deliberation & Self-Disproof Gate
- **Pre-Execution Reflection:** Before making code edits or finalizing architecture, perform a deep deliberation. Break complex problems into distinct, numbered sub-problems.
- **Counter-Hypothesis Construction:** Always formulate at least 1 counter-hypothesis: "Why might my first intuition be flawed? What edge case or market regime invalidates this?"
- **No Premature Convergence:** Evaluate trade-offs explicitly (e.g., Win Rate vs. Risk-Reward ratio, latency vs. safety, slippage vs. opportunity cost) before selecting an approach.

## 2. Invariant & Boundary Auditing
- **Zero Hallucination of Schemas:** Never guess contract lot sizes, tick sizes, order types, margin multipliers, or database column names. Always inspect active code, contracts, or DB schemas directly.
- **Side-Effect Tracing:** Trace full end-to-end execution paths (e.g., Signal -> Sizing -> Order Routing -> Broker Fill -> Position Tracking -> Exit Trail -> PnL Attribution) to prevent downstream breakage.
- **Compaction Survival:** Extract and record critical repository discoveries and system constraints using persistent state artifacts or memory tools.

## 3. Grounded Empirical Proof & Verification Loop
- **Test-Driven Grounding:** Mathematical, algorithmic, or financial claims must be backed by empirical execution (e.g., vectorized DuckDB simulations, pytest runs, or syntax verification via `ast.parse`).
- **Defensive Error Analysis:** When a test or script fails, never retry the exact same approach blindly. Read the exact exception, trace the root cause, adapt the hypothesis, and re-verify.
- **Workspace Cleanliness:** Temporary scratch scripts, ad-hoc backtesters, or benchmark dumps must be automatically purged upon verification.
