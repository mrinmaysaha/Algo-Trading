# OpenAlgo Engineering Conventions & Coding Standards

## 1. Python Architecture & Syntax Standards
- **Python Version**: Python 3.10+ (Targeting 3.12).
- **Type Hints**: Explicit type annotations on all public functions, classes, and service methods (`from typing import Optional, Dict, Any, List`).
- **Formatting**: PEP 8 compliant, 4-space indentation, line length 100 characters max.
- **Import Ordering**:
  1. Standard library (`os`, `sys`, `time`, `json`, `datetime`)
  2. Third-party core (`flask`, `pandas`, `numpy`, `requests`)
  3. Local domain modules (`database`, `broker`, `services`, `utils`)

---

## 2. Token Hygiene & AI Assistant Protocols
To ensure minimal token burn, fast context loading, and high precision across sessions:

1. **Diffs & Partial Snippets Only**: Never reproduce entire 500-line files. Use focused diffs or minimal function overrides with `# ... existing code ...` markers.
2. **Progressive Disclosure**:
   - Step 1: Read high-level docs (`PROJECT.md`, `ARCHITECTURE.md`).
   - Step 2: Use `grep_search` or targeted line view to inspect only the required function.
   - Step 3: Edit strictly the affected lines.
3. **Zero Conversational Filler**: Output code changes, test commands, or factual conclusions directly.
4. **No Unrequested Refactoring**: Fix only the reported bug or requested feature. Do not reformat adjacent functions or reorder unchanged imports.
5. **Session Start Ritual**: Always load project context from `/docs` first before querying code.

---

## 3. Error Handling & Safety Invariants

- **Fail-Closed Execution**: If an API call, broker quote, or order response fails or returns malformed schema, fail closed (abort new entries, keep protective stops active). Never proceed with unverified fallback approximations.
- **No Bare Excepts**: Never use `except: pass`. Catch specific exceptions (`requests.RequestException`, `KeyError`, `ValueError`) and log full context.
- **Sanitized Logging**: Never log raw API secrets, private keys, or passwords. All sensitive parameters must be masked: `api_key[:4] + '****'`.
- **Atomic State Journaling**: Multi-leg orders must write transition journals before and after broker network calls.

---

## 4. Testing Expectations
- **Empirical Grounding**: Always validate algorithmic modifications against isolated backtest scripts or unit tests before altering live production files.
- **Log Suppression**: When executing tests, suppress huge payload dumps; display only test counts, execution time, and failure traces.
- **Workspace Cleanliness (Rule 4)**: All scratch scripts (`test_*.py` in temp directories, test `.csv` dumps, extracted pickles) must be deleted before concluding the turn.
