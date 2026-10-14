# Frontier Deliberative Reasoning & Cognitive Protocol (Opus / Sonnet Parity)

## 0. AUTO-TRIGGER — Sequential Thinking MUST Fire (No /plan Required)

The `sequentialthinking` MCP tool or its inline equivalent MUST be invoked automatically
(without the user asking) whenever ANY of the following conditions are true:

| Trigger Condition | Examples |
|---|---|
| Multi-file architectural change | Touching ≥ 2 strategy files simultaneously |
| Capital / sizing / margin logic | `compute_dynamic_lots`, margin caps, allocation % |
| Risk circuit breaker changes | SL multiplier, basket SL, max loss thresholds |
| Entry / exit gate timing changes | Entry windows, EOD exit, re-entry cutoffs |
| State persistence changes | `_save_state`, `_load_state`, rollover resets |
| Broker API mapping changes | `funds.py`, order routing, fill verification |
| Any change that can cause a live money loss | Any of the above |

**HOW:** Before writing a single line of code on any trigger above, call the `sequentialthinking`
MCP tool with `thought` describing the sub-problem decomposition and counter-hypothesis.
If the MCP tool is unavailable, perform the same decomposition inline in the response as a
numbered reasoning block before any code block.

**WHY:** These are the exact categories that caused the historical bugs in this codebase:
- Sequential fund re-query → margin starvation (capital/sizing trigger)
- `_squaring_off` not persisted → orphaned shorts (state persistence trigger)
- Net credit not validated → debit condor entry (entry gate trigger)


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

## 4. Ultra-Compact Output Protocol (Opus Depth, Minimal Tokens)
- **Zero Conversational Fluff:** Skip introductory pleasantries and restating questions. Lead directly with the technical breakdown, code diffs, or commands.
- **Diffs & Function Overrides Only:** Output minimal unified diffs or targeted functions with `# ... existing code ...`. Never re-print unmodified files.
- **High-Density Scannability:** Use structured tables and concise bullets instead of paragraphs of narrative prose.
- **Line Slices:** Read only 30–60 line slices (`StartLine`/`EndLine`). Never load 1,000+ line files into context when a grep slice suffices.
