---
name: crypto-delta-instance
description: OpenAlgo dual-instance environment router. Automatically activates whenever the user discusses Crypto, Bitcoin (BTC), Ethereum (ETH), Dogecoin (DOGE), Solana (SOL), perpetual futures, crypto options, or Delta Exchange. Enforces strict port and container routing: Port 5001 for Delta Exchange / Crypto, and Port 5000 for Indian domestic markets (NSE, BSE, MCX).
---

# OpenAlgo Dual-Instance Architecture & Routing Skill

This repository operates **two distinct, parallel OpenAlgo environments** running in isolated Docker containers with dedicated databases, ports, API credentials, and strategy directories.

Under no circumstances should crypto strategies, symbols, or queries be routed to Port 5000, and domestic Indian market strategies must never be routed to Port 5001.

---

## 1. Instance Specification Matrix

| Attribute | Instance 1: Domestic Markets (Angel One) | Instance 2: Global Crypto (Delta Exchange) |
|---|---|---|
| **Container Name** | `openalgo-angel` | `openalgo-global` |
| **HTTP / REST Port** | **`5000`** (`http://127.0.0.1:5000`) | **`5001`** (`http://127.0.0.1:5001`) |
| **WebSocket Port** | **`8765`** (`ws://127.0.0.1:8765`) | **`8766`** (`ws://127.0.0.1:8766`) |
| **Market Coverage** | **NSE, BSE, MCX, NFO, BFO, CDS** | **CRYPTO, Perpetuals, Crypto Options** |
| **Supported Assets** | NIFTY, BANKNIFTY, SENSEX, CRUDEOIL, GOLD, Equities | BTC, ETH, DOGE, SOL, XRP, Delta Exchange Perpetuals |
| **Broker Client** | Angel One (`angel`) | Delta Exchange (`deltaexchange`) |
| **Config File** | `.env` | `.env.global` |
| **Strategy Scripts Folder** | `strategies/scripts/` | `strategies_global/scripts/` |
| **Strategy Registry** | `strategies/strategy_configs.json` | `strategies_global/strategy_configs.json` |
| **OpenAlgo API Key Env** | `OPENALGO_API_KEY` (from `.env`) | `OPENALGO_API_KEY_CRYPTO` (from `.env.global`) |
| **API Key Value** | Configured in `.env` | `56609a7a820eaadf566b44babb61609fa55c25100996723f31a0b048f0c86721` |
| **Docker Volume Mount** | `./strategies:/app/strategies` | `./strategies_global:/app/strategies` |

---

## 2. Mandatory Routing Invariants

### Rule 1: Crypto Keyword Trigger -> Port 5001
Whenever the user or task mentions:
- `crypto`, `cryptocurrency`, `bitcoin`, `btc`, `ethereum`, `eth`, `dogecoin`, `doge`, `solana`, `sol`, `delta`, `delta exchange`, `perpetual`, or `perp`

You MUST:
1. Direct all REST API calls and SDK clients to `http://127.0.0.1:5001`.
2. Connect WebSocket streams to `ws://127.0.0.1:8766`.
3. Use the Crypto OpenAlgo API key (`56609a7a820eaadf566b44babb61609fa55c25100996723f31a0b048f0c86721`).
4. Read and place production strategy scripts exclusively in `strategies_global/scripts/`.
5. Register strategy scheduler configs exclusively in `strategies_global/strategy_configs.json`.
6. Inspect logs and container status using `openalgo-global`.

### Rule 2: Domestic Keyword Trigger -> Port 5000
Whenever the user or task mentions:
- `nse`, `bse`, `mcx`, `nfo`, `bfo`, `nifty`, `banknifty`, `sensex`, `finnifty`, `midcpnifty`, `crudeoil`, `goldm`, `silver`, `angel`, `angel one`

You MUST:
1. Direct all REST API calls and SDK clients to `http://127.0.0.1:5000`.
2. Connect WebSocket streams to `ws://127.0.0.1:8765`.
3. Use the domestic OpenAlgo API key from `.env`.
4. Read and place production strategy scripts exclusively in `strategies/scripts/`.
5. Register strategy scheduler configs exclusively in `strategies/strategy_configs.json`.
6. Inspect logs and container status using `openalgo-angel`.

---

## 3. Strategy Script Standard Initialization Idiom

Every crypto strategy script must use container-aware host and credential discovery:

```python
import os

def init_crypto_client():
    from openalgo import api
    # Within Docker container: port 5000 internal. On Windows host: port 5001 external.
    default_host = "http://127.0.0.1:5000" if os.path.exists("/.dockerenv") else "http://127.0.0.1:5001"
    host = os.getenv("OPENALGO_HOST_CRYPTO") or os.getenv("OPENALGO_HOST", default_host)
    api_key = os.getenv("OPENALGO_API_KEY_CRYPTO") or os.getenv("OPENALGO_API_KEY", "56609a7a820eaadf566b44babb61609fa55c25100996723f31a0b048f0c86721")
    return api(api_key=api_key, host=host)
```

> [!IMPORTANT]
> In the official `openalgo` PyPI SDK, position lookup is performed via `client.positionbook()`. Do NOT call `client.position()` or `client.positions()`.

---

## 4. Diagnostic & Health Verification Commands

```powershell
# Verify Port 5001 (Delta Crypto Instance)
Invoke-RestMethod -Uri "http://127.0.0.1:5001/auth/check-setup" -Method Get

# Verify Port 5000 (Angel Domestic Instance)
Invoke-RestMethod -Uri "http://127.0.0.1:5000/auth/check-setup" -Method Get

# Query Crypto Positionbook on Port 5001
Invoke-RestMethod -Uri "http://127.0.0.1:5001/api/v1/positionbook" -Method Post -Headers @{"Content-Type"="application/json"} -Body '{"apikey":"56609a7a820eaadf566b44babb61609fa55c25100996723f31a0b048f0c86721"}'
```

---

## 5. Official Delta Exchange India Fee & Tax Invariants (MANDATORY FOR ALL BACKTESTS)

When performing backtesting, simulations, or live order sizing on Port 5001 (Delta Exchange India):

### 1. Options Fee Schedule & Capping Rules
- **Base Rate:** **0.010%** of Notional Value.
- **CRITICAL FEE CAP:** **Capped at 3.5% of the Option Premium!**
  $$\text{Fee Charged} = \min(0.010\% \times \text{Notional}, 3.5\% \times \text{Option Premium})$$
  *Never calculate options fees as a flat percentage of notional without applying the 3.5% premium cap. Doing so artificially inflates simulated fees by 10x to 50x.*
- **Indian GST:** Exactly **18% GST** on all exchange trading fees.
- **Typical Round-Trip Iron Condor Cost (4 legs, 60 contracts):**
  - BTC Daily IC: **~$0.38 USD (~₹34 INR)** per complete trade (~14% of gross profit).
  - ETH Daily IC: **~$0.44 USD (~₹39 INR)** per complete trade (~24% of gross profit).

### 2. Futures (Perpetuals) Fee Schedule
- **Maker (Limit Entry at or inside touch):** **0.020%** of Notional Value.
- **Taker (Market Exit on SL/TP):** **0.050%** of Notional Value.
- **Indian GST:** Exactly **18% GST** on exchange fees.
- **Slippage Drag:** ~0.020% per market fill (0% for limit maker orders).
- **Total Round-Trip Friction (Maker Buy + Taker Sell):**
  $$\text{Total Fee} = (0.020\% + 0.050\%) \times 1.18 = \mathbf{0.0826\% \text{ of Notional}}$$

### 3. Canonical Calculator Module
Always import or reference [`broker/deltaexchange/delta_fees.py`](file:///c:/Users/mrinm/Algo_tading/openalgo/broker/deltaexchange/delta_fees.py) for programmatic fee validation across all backtest scripts and live strategies.

