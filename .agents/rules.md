# OpenAlgo Global Token-Conscious Rules

## 1. Output & Interaction Protocol
- **Diffs & Partial Snippets Only:** Never reproduce entire files. Output concise unified diffs or minimal function overrides using `# ... existing code ...` for unchanged parts.
- **Zero Conversational Filler:** Omit intros ("Sure, I can fix that"), pleasantries, post-summaries, and conversational explanations unless explicitly requested.
- **Immediate Deliverable:** Lead directly with the code change or terminal command in line 1.
- **No Unrequested Refactoring:** Fix only the targeted bug or feature. Do not adjust formatting, reorder imports, or touch adjacent unreferenced files.

## 2. Project Architecture & Context Limits
- **Frameworks:** Python 3.10+, Flask / FastAPI, Pandas, NumPy, VectorBT, SQLite / Historify, WebSockets, REST APIs.
- **Domain Modules:**
  - `app / server`: API routes, UI dashboard handlers, session management.
  - `broker_adapters`: API clients for Angel One, Dhan, CoinDCX, etc.
  - `strategies`: Vectorized logic receiving OHLCV DataFrames (`entries`, `exits`).
  - `execution_engine`: Order placement, position tracking, webhook handlers.
- **Targeted Reading:** When asked to inspect an issue, read only the relevant function or module. Do not scan entire directory trees or pull unneeded files into context.

## 3. Debugging & Testing Efficiency
- **Stack Trace Priority:** Read only the exception type, error message, and the exact line of code referenced in the stack trace. Ignore framework boilerplate logs.
- **Log Suppression:** When running test commands, capture only stderr and summary lines. Never print full payload dumps or entire DataFrame outputs.