---
name: playwright-browser-test
description: End-to-end browser testing, UI user-flow validation, element interactions, and visual inspection using Playwright.
argument-hint: "[url or page route]"
allowed-tools: Read, Write, Edit, Bash, Glob, Grep
---

# Playwright Browser Test Skill

Use this skill to design and run end-to-end web browser tests for UI applications.

## Guidelines

1. **Selector Strategy**:
   - Use user-facing role selectors (`page.get_by_role("button", name="Submit")`), labels (`get_by_label`), or explicit `data-testid` attributes.
   - Avoid brittle XPath or deep CSS class chains.

2. **Async / Auto-Waiting**:
   - Rely on Playwright's built-in auto-waiting for element visibility and interactivity.
   - Avoid hardcoded `time.sleep()` calls.

3. **Validation & Assertions**:
   - Assert page title, heading text, URL redirects, and API network responses.
   - Take screenshots or recordings on test failure for easy visual inspection.
