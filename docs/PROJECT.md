# OpenAlgo Project Manifest

## Project Overview
OpenAlgo is an open-source, multi-broker algorithmic trading platform and execution gateway engineered for low-latency automated trading, backtesting, and portfolio risk management. It provides unified REST/WebSocket APIs, no-code visual workflow builders (`/flow`), self-hosted strategy runners (`/python`), and institutional-grade risk guards across Indian domestic markets (NSE, BSE, MCX) and international crypto derivatives (Delta Exchange).

- **Current Status**: Production active. Dual-instance deployment running Indian domestic markets on Port 5000 and global crypto derivatives on Port 5001.
- **Repository**: [marketcalls/openalgo](https://github.com/marketcalls/openalgo) | **Documentation**: [docs.openalgo.in](https://docs.openalgo.in)

---

## Documentation Quick Index
Before writing code or debugging, consult the living knowledge base:

| Document | Purpose |
|---|---|
| [INDEX.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/INDEX.md) | Canonical documentation map and directory index |
| [ARCHITECTURE.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/ARCHITECTURE.md) | System topology, Mermaid diagrams, module responsibilities, dual-instance invariants |
| [DESIGN.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/DESIGN.md) | Product vision, core feature backlog, acceptance criteria, user stories |
| [DESIGN_SYSTEM.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/DESIGN_SYSTEM.md) | Frontend UI tokens, color palettes, component hierarchy, interaction rules |
| [DATA_MODEL.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/DATA_MODEL.md) | SQLite & Historify database schemas, ER diagrams, state JSON contracts |
| [API.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/API.md) | REST API endpoints, request/response envelopes, WebSockets feed schemas |
| [CONVENTIONS.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/CONVENTIONS.md) | Coding standards, token hygiene, PR workflows, error handling protocols |
| [ROADMAP.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/ROADMAP.md) | Prioritized feature roadmap and development milestones |
| [CHANGELOG.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/CHANGELOG.md) | Chronological record of features, bug fixes, and breaking changes |
| [TESTING.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/TESTING.md) | Test suite execution, mocking standards, pytest guidelines |
| [DEPLOYMENT.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/DEPLOYMENT.md) | Docker compose configurations, port mapping, production supervision |
| [MEMORY.md](file:///c:/Users/mrinm/Algo_tading/openalgo/docs/MEMORY.md) | Living agent memory: active strategy PIDs, critical gotchas, backtest learnings |

---

## Tech Stack & Dependencies

- **Language & Runtime**: Python 3.10+ (Recommended Python 3.12)
- **Web Framework**: Flask (REST API, blueprints, session auth) / FastAPI extensions
- **Data & Computation**: Pandas, NumPy, VectorBT, OpenAlgo TA primitive indicators (Rust-backed)
- **Database & Storage**: SQLite 3, Historify time-series tick engine
- **Real-Time Streaming**: WebSockets (`websockets`, `websocket-client`)
- **Frontend / Dashboard**: HTML5, Vanilla JavaScript, CSS3 Design Tokens, Tailwind CSS, Plotly
- **Infrastructure & Containerization**: Docker, Docker Compose, Gunicorn, Supervisor

---

## Running Locally

### 1. Domestic Instance (Port 5000 — Indian Markets)
```bash
# Set environment
cp .sample.env .env
# Run domestic instance
python app.py
# Default UI: http://localhost:5000 | WebSocket: ws://localhost:8765
```

### 2. Global Crypto Instance (Port 5001 — Delta Exchange)
```bash
# Run global crypto instance
PORT=5001 python app.py
# Default UI: http://localhost:5001 | WebSocket: ws://localhost:8766
```

### 3. Docker Multi-Instance
```bash
docker-compose up -d
```

---

## Current Sprint & Immediate Priorities
1. **Perpetual Futures Engine Upgrade**: Deploy 4H Macro Trend + 1H Liquidity Grab models on BTCUSD and ETHUSD to achieve +$20,000 target over 180 days.
2. **0DTE Crypto Iron Condor Hardening**: Verify concurrent leg execution, strike resolution, and fail-closed state recovery.
3. **Continuous Documentation Maintenance**: Keep all docs synchronized after every architectural and algorithmic adjustment.
