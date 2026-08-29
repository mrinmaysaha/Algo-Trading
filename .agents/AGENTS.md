# OpenAlgo Project Rules & Guidelines

These rules apply to all AI assistants and tools working within this workspace to ensure maximum token efficiency, fast debugging, and high code quality.

## 1. Output & Interaction Protocol
- **Diffs & Partial Snippets Only:** Never reproduce entire files. Output concise unified diffs or minimal function overrides using `# ... existing code ...` for unchanged parts.
- **Zero Conversational Filler:** Lead directly with code changes, terminal commands, or factual explanations.
- **No Unrequested Refactoring:** Fix only the targeted bug or feature. Do not adjust formatting, reorder imports, or touch adjacent unreferenced files.

## 2. Project Architecture & Context Limits
- **Frameworks:** Python 3.10+, Flask / FastAPI, Pandas, NumPy, VectorBT, SQLite / Historify, WebSockets, REST APIs.
- **Domain Modules:**
  - `app / server`: API routes, UI dashboard handlers, session management.
  - `broker_adapters`: API clients for Angel One, Dhan, Alice Blue, etc.
  - `strategies`: Vectorized logic receiving OHLCV DataFrames (`entries`, `exits`).
  - `execution_engine`: Order placement, position tracking, webhook handlers.
- **Targeted Reading:** Inspect only the relevant function or module. Do not scan entire directory trees or pull unneeded files into context.

## 3. Debugging & Testing Efficiency
- **Stack Trace Priority:** Read only the exception type, error message, and the exact line of code referenced in the stack trace. Ignore framework boilerplate logs.
- **Log Suppression:** When running test commands, capture only stderr and summary lines. Never print full payload dumps or entire DataFrame outputs.
- **Skill Usage:** Use `.agents/skills/openalgo-dev-debug/` harness for quick isolated unit testing without loading full server context.

## 4. Workspace Hygiene & Automatic Cleanup
- **No Temporary Files Left on Disk:** Any scratch scripts, ad-hoc backtesters, extracted text caches, or temporary `.csv` files created during a task must be automatically purged before concluding the turn.
- **Production Isolation:** Only commit-ready code in `strategies/scripts/`, `strategies/strategy_configs.json`, and official test files in `test/` may persist in the workspace.
- **Skill Usage:** Leverage `.agents/skills/auto-cleanup/` to enforce clean workspaces.
