# OpenAlgo Data Model Reference

## Entity-Relationship Diagram

```mermaid
erDiagram
    USERS ||--o{ ORDERS : places
    USERS ||--o{ STRATEGIES : configures
    USERS ||--o{ API_KEYS : owns
    ORDERS ||--o{ TRADES : generates
    ORDERS ||--o{ POSITIONS : aggregates
    STRATEGIES ||--o{ ORDERS : triggers
    STRATEGIES ||--o{ STRATEGY_LOGS : journals

    USERS {
        int id PK
        string username
        string password_hash
        string totp_secret
        datetime created_at
    }

    API_KEYS {
        int id PK
        int user_id FK
        string key_hash
        string permissions
        datetime created_at
        datetime last_used_at
    }

    STRATEGIES {
        int id PK
        string strategy_name UK
        string script_path
        string status
        int pid
        string schedule_cron
        string params_json
        datetime last_run_at
    }

    ORDERS {
        int id PK
        string order_id UK
        string broker_order_id
        string strategy_name FK
        string symbol
        string exchange
        string action
        string product
        string price_type
        float quantity
        float price
        float trigger_price
        string status
        datetime placed_at
        datetime updated_at
    }

    TRADES {
        int id PK
        string trade_id UK
        string order_id FK
        string symbol
        float filled_qty
        float fill_price
        float brokerage_fee
        datetime trade_time
    }

    POSITIONS {
        int id PK
        string symbol UK
        string exchange
        string product
        float net_quantity
        float average_buy_price
        float average_sell_price
        float realized_pnl
        float unrealized_pnl
        datetime updated_at
    }

    STRATEGY_LOGS {
        int id PK
        string strategy_name FK
        string log_level
        string message
        datetime timestamp
    }
```

---

## 1. Database Storage Engines

OpenAlgo uses two distinct SQLite databases configured in WAL (Write-Ahead Logging) mode:
1. **Primary Database (`openalgo.db` on Port 5000 / `openalgo_delta.db` on Port 5001)**:
   - Stores users, API authentication credentials, orderbook history, trade logs, strategy configuration registries, and execution audit trails.
2. **Historify Database (`historify.db` / `sandbox_delta.db`)**:
   - Optimized append-only time-series store for raw market ticks, L2 depth snapshots, and synthesized 1m/5m/15m/1H historical OHLCV bars.

---

## 2. Core SQLite Tables & Schemas

### `orders` Table
```sql
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    order_id TEXT UNIQUE NOT NULL,
    broker_order_id TEXT,
    strategy_name TEXT,
    symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    action TEXT NOT NULL CHECK(action IN ('BUY', 'SELL')),
    product TEXT NOT NULL,
    price_type TEXT NOT NULL CHECK(price_type IN ('MARKET', 'LIMIT', 'SL', 'SL-M')),
    quantity REAL NOT NULL,
    price REAL DEFAULT 0.0,
    trigger_price REAL DEFAULT 0.0,
    status TEXT NOT NULL CHECK(status IN ('PENDING', 'OPEN', 'COMPLETE', 'CANCELLED', 'REJECTED')),
    rejection_reason TEXT,
    placed_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_orders_symbol ON orders(symbol);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_strategy ON orders(strategy_name);
```

### `strategy_configs.json` (Registration Schema)
```json
{
  "strategy_name": "BTC_Liquidity_Sweep_Perp",
  "script_name": "BTC_Liquidity_Sweep_Perp.py",
  "enabled": true,
  "execution_mode": "live",
  "schedule": "0 * * * *",
  "max_capital_risk": 500.0,
  "parameters": {
    "symbol": "BTCUSD",
    "timeframe": "1h",
    "macro_filter": "4h_50_200_ema",
    "swing_lookback_bars": 24,
    "min_rejection_wick": 0.30,
    "risk_reward_target": 2.5,
    "risk_dollars_per_trade": 100.0
  }
}
```

---

## 3. Persistent Strategy State Journal Schema (`.json`)

To prevent orphaned option legs or unrecovered perpetual positions after server crashes, strategies write atomic state checkpoints:

```json
{
  "date": "2026-09-20",
  "strategy": "BTC_Daily_Iron_Condor",
  "state": "ACTIVE",
  "basket_invariant_checked": true,
  "legs": {
    "short_call": {
      "symbol": "C-BTC-110000-200926",
      "strike": 110000,
      "type": "CE",
      "qty": 1,
      "order_id": "ORD_SC_1001",
      "fill_price": 450.5,
      "sl_price": 901.0,
      "status": "FILLED"
    },
    "long_call_wing": {
      "symbol": "C-BTC-114000-200926",
      "strike": 114000,
      "type": "CE",
      "qty": 1,
      "order_id": "ORD_LC_1002",
      "fill_price": 85.0,
      "status": "FILLED"
    }
  },
  "net_credit_collected": 365.5,
  "current_pnl": 120.0,
  "last_updated": "2026-09-20T18:30:00Z"
}
```
