# OpenAlgo Product & Design Specification

## Product Vision & Core Value
OpenAlgo provides individual traders, proprietary desks, and quantitative developers with an institutional-grade, self-hosted algorithmic trading engine. It eliminates lock-in to single brokers, unifies order execution syntax across domestic Indian exchanges (NSE, BSE, MCX) and international crypto derivatives (Delta Exchange), and guarantees deterministic, fail-closed execution.

### Target Users
1. **Quantitative Traders**: Requiring fast VectorBT backtesting, automated signal execution, and custom indicator computation.
2. **Options System Traders**: Running multi-leg 0DTE delta-neutral structures (short straddles, iron condors) with strict SL/TP and rollback protection.
3. **Discretionary / Semi-Algo Traders**: Utilizing TradingView webhook alerts or the browser charting terminal (`/trading`).

---

## Feature Inventory & Status

| Feature Domain | Feature Description | Status | User Story |
|---|---|---|---|
| **Multi-Broker Gateway** | Unified REST API mapping 36+ Indian brokers & Delta Exchange | **Done** | As a trader, I want to write strategy code once and route orders to any broker without rewriting API integration logic. |
| **0DTE Crypto Options Engine** | Automated daily Iron Condors on BTC/ETH with parallel wing execution and per-leg SL | **Done** | As an options seller, I want to collect theta decay automatically with concurrent wing fills and crash recovery. |
| **Dual-Instance Architecture** | Physical separation of Domestic (Port 5000) and Crypto (Port 5001) | **Done** | As an operator, I want crypto strategies isolated from domestic trading to prevent cross-contamination. |
| **4H Trend + 1H Liquidity Grab** | High-expectancy perpetual futures engine targeting +$20k profit over 180 days | **In-Progress** | As a futures trader, I want to avoid high-frequency fee drag by entering high-conviction 1H liquidity sweeps aligned with 4H macro trends. |
| **Flow Visual Automation** | No-code node graph builder at `/flow` for webhook routing | **Done** | As a non-programmer, I want to wire TradingView alerts to bracket orders visually. |
| **Real-Time Quote Feed** | WebSocket feed proxy with sub-millisecond tick broadcast | **Done** | As a chart user, I want live bid/ask/LTP updates without page reloads. |
| **Dynamic Portfolio Supervisor** | Multi-strategy portfolio supervisor with daily drawdown circuit breakers | **Planned** | As a fund manager, I want an umbrella risk cap that kills all strategies if daily drawdown exceeds 3%. |

---

## Acceptance Criteria for Key Features

### 1. Dual-Leg / Multi-Leg Option Execution
- **Concurrent Wing Placement**: Protective wings must be submitted simultaneously via thread pools, not sequentially, reducing market exposure from 25s to < 2s.
- **Fail-Closed Rollback**: If short legs fail to execute after wings are filled, wings must be automatically unwound. If unwind fails, positions must be marked `STUCK` and alerted, NEVER silently orphaned.
- **Exact-Fill Accounting**: PnL and risk caps must compute from executed average fill prices, never theoretical midpoints.

### 2. High-Expectancy Perpetual Futures Engine (BTC & ETH)
- **Timeframe Alignment**: Strategy must only evaluate closed 1H candles with 4H trend direction (50 EMA vs 200 EMA).
- **Rejection Verification**: Candlestick must exhibit confirmed rejection wick ($\ge 30\%$ on BTC, $\ge 25\%$ on ETH).
- **Fee Efficiency**: Maximum trade frequency must not exceed 4 trades/week per symbol to keep round-trip taker fees below 15% of gross edge.

---

## Non-Functional Requirements (NFR)
1. **Low Latency**: REST order ingestion to broker socket dispatch under 25ms.
2. **Reliability & Uptime**: Self-healing worker daemons managed via Supervisor/Docker with zero unhandled crash loops.
3. **Security**: Encrypted API secrets on disk, TOTP two-factor authentication, CSP header hardening, and no credential leakage in logs.
4. **Data Integrity**: WAL-mode SQLite database writes with transaction rollbacks on failure.
