# OpenAlgo Project Rules & Guidelines

These rules apply to all AI assistants and tools working within this workspace to ensure maximum token efficiency, fast debugging, and high code quality.

## 1. Output & Interaction Protocol
- **Diffs & Partial Snippets Only:** Never reproduce entire files. Output concise unified diffs or minimal function overrides using `# ... existing code ...` for unchanged parts.
- **Zero Conversational Filler:** Lead directly with code changes, terminal commands, or factual explanations. No pleasantries, no restating the user's prompt.
- **No Unrequested Refactoring:** Fix only the targeted bug or feature. Do not adjust formatting, reorder imports, or touch adjacent unreferenced files.

## 2. Session Start Ritual & Documentation First (Long-Term Memory)
- **Always Start by Reading Docs:** Before writing code or modifying logic, read [docs/PROJECT.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/PROJECT.md), [docs/ARCHITECTURE.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/ARCHITECTURE.md), and [docs/MEMORY.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/MEMORY.md).
- **Living Documentation (Source of Truth):** After any structural, algorithmic, or schema change, **update the corresponding docs** (`ARCHITECTURE.md`, `DESIGN.md`, `DATA_MODEL.md`, `MEMORY.md`, and `CHANGELOG.md`).
- **Memory Maintenance (Compaction Survival):** Automatically append key architectural discoveries, resolved gotchas, and empirical invariants to [docs/MEMORY.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/MEMORY.md) before concluding a turn.
- **UI Strictness:** All UI adjustments must strictly follow [docs/DESIGN_SYSTEM.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/DESIGN_SYSTEM.md); never invent ad-hoc styles.

## 3. Token Hygiene & Context Optimization (Quota Conservation)
- **Strict Targeted Slices (<= 60 lines):** Never dump entire 500+ line files into context. Locate targets using `grep_search`, then use `view_file` strictly on 30–60 line slices (`StartLine`/`EndLine`).
- **Test Output Suppression:** Always run pytest with `-q --tb=short` or pipe command outputs to capture only summary and failures. Never permit thousands of lines of DataFrames, orderbooks, or stack traces to pollute context.
- **Hierarchical Progressive Disclosure:** Read high-level docs first. Keep prompt responses scannable using Mermaid diagrams and compact bullet points instead of verbose prose.
- **Compaction Resilience:** Rely on distilled [docs/MEMORY.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/MEMORY.md) + current turn state rather than dragging huge multi-turn transcripts.

## 4. Project Architecture & Context Limits
- **Frameworks:** Python 3.10+, Flask / FastAPI, Pandas, NumPy, VectorBT, SQLite / Historify, WebSockets, REST APIs.
- **Domain Modules:**
  - `app / server`: API routes, UI dashboard handlers, session management.
  - `broker_adapters`: API clients for Angel One, Dhan, Alice Blue, Delta Exchange, etc.
  - `strategies`: Vectorized logic receiving OHLCV DataFrames (`entries`, `exits`).
  - `execution_engine`: Order placement, position tracking, webhook handlers.
- **Targeted Reading:** Inspect only the relevant function or module. Do not scan entire directory trees or pull unneeded files into context.

## 5. Debugging & Testing Efficiency
- **Stack Trace Priority:** Read only the exception type, error message, and the exact line of code referenced in the stack trace. Ignore framework boilerplate logs.
- **Log Suppression:** When running test commands, capture only stderr and summary lines.
- **Skill Usage:** Use `.agents/skills/openalgo-dev-debug/` harness for quick isolated unit testing without loading full server context.

## 6. Workspace Hygiene & Automatic Cleanup
- **No Temporary Files Left on Disk:** Any scratch scripts, ad-hoc backtesters, extracted text caches, or temporary `.csv` files created during a task must be automatically purged before concluding the turn.
- **Production Isolation:** Only commit-ready code in `strategies/scripts/`, `strategies/strategy_configs.json`, and official test files in `test/` may persist in the workspace.
- **Skill Usage:** Leverage `.agents/skills/auto-cleanup/` to enforce clean workspaces.

## 7. Frontier Deliberative Reasoning & Cognitive Protocol (Opus / Sonnet Parity)
- **Cognitive Scaffolding & Self-Disproof Gate:** For all non-trivial problems, break the objective into sub-problems, formulate at least 1 counter-hypothesis ("Why might this intuition fail or cause regressions?"), and evaluate trade-offs before generating code.
- **Invariant & Boundary Auditing:** Audit active schemas, tick sizes, order types, and contract multipliers directly from active code before proposing edits. Never assume or approximate.
- **Empirical Grounding:** Validate algorithmic adjustments with isolated data-backed verification scripts and syntax checks before finalizing. Rule file: `.agents/rules/frontier-reasoning.md`.

## 8. Dual-Instance Routing Invariant (Port 5000 vs Port 5001)
- **Port 5000 (`openalgo-angel`, WS 8765):** Exclusively for Indian domestic markets (NSE, BSE, MCX, NIFTY, BANKNIFTY, SENSEX, CRUDEOIL, GOLD). Strategies live in `strategies/scripts/` and `strategies/strategy_configs.json`.
- **Port 5001 (`openalgo-global`, WS 8766):** Exclusively for Crypto derivatives on Delta Exchange (BTC, ETH, DOGE, SOL, perpetuals, options). Strategies live in `strategies_global/scripts/` and `strategies_global/strategy_configs.json`.
- **Zero Cross-Pollution:** Never register crypto strategies on port 5000, and never place domestic strategies on port 5001. Skill: `.agents/skills/crypto-delta-instance/`.


