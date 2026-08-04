---
name: tdd-master
description: Test-Driven Development (TDD) methodology rules - Red-Green-Refactor cycle, unit test isolation, and test-first implementation.
argument-hint: "[feature or module name]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# TDD Master Skill

Use this skill when implementing new features or bug fixes using Test-Driven Development.

## TDD Workflow (Red-Green-Refactor)

1. **RED**:
   - Write failing unit tests first that specify the required behavior/contract.
   - Assert expected outputs, exception handling, and edge cases.
   - Execute the test suite and confirm it fails as expected before writing code.

2. **GREEN**:
   - Write the minimum implementation required to pass the failing tests.
   - Do not write extra unneeded code or speculate future requirements.
   - Run the test suite and verify all tests pass cleanly.

3. **REFACTOR**:
   - Clean up code structure, improve variable naming, and optimize performance.
   - Ensure the test suite continues to pass after refactoring.

## Best Practices
- Keep tests isolated and fast (mock external network requests, database connections, and third-party APIs).
- Maintain 1:1 parity between test module names and source module names (`test_<module_name>.py`).
