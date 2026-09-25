# OpenAlgo Testing & Verification Guide

## 1. Testing Philosophy
OpenAlgo controls real capital and lives in live financial markets. Testing must adhere to the **Red-Green-Refactor** cycle and the **Verify Before Stating Claims** rule. Never claim a strategy is profitable, an API route is secure, or a concurrency bug is solved without executable verification.

---

## 2. Test Suites & Execution Commands

### Run Full Fast Unit Test Suite
```bash
pytest test/ -k "not integration" -q --tb=short
```

### Run Crypto Options Concurrency & Rollback Tests
```bash
pytest test/test_crypto_iron_condor_hardened.py -v
```

### Run Strategy Backtests with VectorBT Harness
```bash
python -m pytest test/test_strategies.py -s
```

---

## 3. Mocking & Sandbox Isolation Standards

1. **Exchange Mocking**:
   - Live orders must never be placed during routine CI/CD runs.
   - Use `sandbox/` or mock broker adapters (`broker/mock_broker.py`) returning deterministic order IDs, fills, and depth quotes.
2. **Network Failures**:
   - Always test timeouts (`requests.exceptions.Timeout`), connection refused (`requests.exceptions.ConnectionError`), and HTTP 429 rate limit responses.
3. **Log & Clean Discipline**:
   - Test suites must clean up any temporary `.sqlite`, `.json`, or `.csv` files created during execution.
