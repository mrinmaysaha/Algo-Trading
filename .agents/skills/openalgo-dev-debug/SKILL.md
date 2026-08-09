---
name: openalgo-dev-debug
description: Targeted debugging, testing, and fixing workflow across OpenAlgo API routes, broker adapters, execution engine, and strategies with minimal token consumption.
---

# OpenAlgo Development & Debugging Workflow

Use this skill when diagnosing, debugging, or modifying OpenAlgo API endpoints, strategy handlers, or broker integrations.

## 1. Token-Efficient Inspection
- **Targeted Reading:** Locate the failing line or function first using grep. Avoid reading entire 3,000-line files.
- **Snippet Bounds:** Fetch only 20–50 lines around the target line.
- **Log Inspection:** Extract only the specific traceback lines and error message from task logs.

## 2. Token-Efficient Edits
- **Single Contiguous Edits:** Use `replace_file_content` for focused fixes.
- **Multi-Location Edits:** Use `multi_replace_file_content` with concise `ReplacementChunks`.
- **No Refactoring Side-Effects:** Do not modify whitespace, formatting, docstrings, or surrounding untouched code.

## 3. Isolated Testing & Verification
- Use `test_harness.py` for lightweight dry-runs instead of booting full live WebSocket or broker sessions:
  ```python
  from .test_harness import mock_ohlcv_data, mock_broker_order_response
  df = mock_ohlcv_data(30)
  ```
- Run targeted pytest invocations:
  ```bash
  pytest tests/test_module.py -k "test_function_name" --tb=short
  ```