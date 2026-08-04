---
name: python-expert
description: Expert Python development rules, type hints, PEP 8 standards, async handlers, clean architecture, and error handling.
argument-hint: "[module or script path]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Python Expert Skill

Use this skill when developing, refactoring, or reviewing Python code to enforce high quality, clean architecture, robust typing, and standard conventions.

## Key Principles

1. **Strict Type Annotations**:
   - Always add clear type hints for function arguments and return types.
   - Use `Optional[T]` / `T | None`, `list[T]`, `dict[K, V]`, and `Callable`.

2. **Clean Error Handling & Logging**:
   - Never swallow exceptions blindly with `except Exception: pass`.
   - Always log tracebacks or raise custom descriptive exceptions.
   - Use Python's built-in `logging` module with structured formats (`%(asctime)s [%(levelname)s] %(message)s`).

3. **PEP 8 & Formatting**:
   - Follow PEP 8 guidelines (snake_case functions/variables, PascalCase classes, UPPER_CASE constants).
   - Keep functions modular and single-responsibility.

4. **Resource Safety**:
   - Always use context managers (`with open(...) as f:`, `async with ...`) for file I/O, database sessions, and socket locks.

5. **Async & Performance**:
   - Prefer vectorized operations (NumPy, pandas) for data processing.
   - Avoid blocking calls in async or event loop threads.
