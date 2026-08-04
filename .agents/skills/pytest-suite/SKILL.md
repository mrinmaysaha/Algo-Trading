---
name: pytest-suite
description: Comprehensive Pytest suite generation, fixtures, mocking, parametrized tests, and coverage analysis.
argument-hint: "[target module or test file]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Pytest Suite Skill

Use this skill to create, manage, and execute automated test suites using `pytest`.

## Core Guidelines

1. **Test Organization**:
   - Place tests in the `test/` directory.
   - Name test files `test_<name>.py` and test functions `test_<scenario>()`.

2. **Fixtures & Mocking**:
   - Use `@pytest.fixture` for reusable setup/teardown (e.g. API clients, database sessions, temp dirs).
   - Use `monkeypatch` or `unittest.mock.patch` to mock external HTTP calls, WebSocket feeds, and time dependencies.

3. **Parametrization**:
   - Use `@pytest.mark.parametrize("input,expected", [...])` to test multiple inputs concisely.

4. **Execution & Verification**:
   - Run tests using `uv run pytest test/ -v` or `pytest test/`.
   - Ensure clean exit code 0 without warnings or hanging background threads.
