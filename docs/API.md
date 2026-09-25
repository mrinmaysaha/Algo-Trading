# OpenAlgo REST & WebSocket API Specification

## 1. Authentication & Security
- **API Key Header**: `X-API-Key: your_api_key_here`
- **Session Auth**: Supported for UI dashboard requests via encrypted HTTP-only session cookies.
- **Rate Limits**: 20 requests per second per IP (configurable via `limiter.py`).

---

## 2. Standard API Response Envelope

All REST endpoints return a unified JSON envelope:

### Success Response
```json
{
  "status": "success",
  "data": { ... },
  "message": "Operation completed successfully"
}
```

### Error Response
```json
{
  "status": "error",
  "error": {
    "code": "INSUFFICIENT_FUNDS",
    "message": "Required margin $1,420 exceeds available balance $850"
  }
}
```

---

## 3. Core REST Endpoints

### Place Order
- **URL**: `/api/v1/placeorder`
- **Method**: `POST`
- **Payload Schema**:
```json
{
  "symbol": "BTCUSD",
  "exchange": "DELTA",
  "action": "BUY",
  "product": "PERP",
  "pricetype": "MARKET",
  "quantity": 10,
  "price": 0.0,
  "trigger_price": 0.0,
  "strategy": "BTC_Liquidity_Sweep_Perp"
}
```
- **Response**:
```json
{
  "status": "success",
  "data": {
    "orderid": "ORD-20260920-881920",
    "status": "OPEN",
    "broker_order_id": "delta_live_199281"
  }
}
```

---

### Multi-Leg Options Order
- **URL**: `/api/v1/optionsmultiorder`
- **Method**: `POST`
- **Payload Schema**:
```json
{
  "strategy": "BTC_Daily_Iron_Condor",
  "orders": [
    { "symbol": "C-BTC-110000-200926", "action": "SELL", "quantity": 1, "pricetype": "MARKET" },
    { "symbol": "C-BTC-114000-200926", "action": "BUY", "quantity": 1, "pricetype": "MARKET" },
    { "symbol": "P-BTC-98000-200926", "action": "SELL", "quantity": 1, "pricetype": "MARKET" },
    { "symbol": "P-BTC-94000-200926", "action": "BUY", "quantity": 1, "pricetype": "MARKET" }
  ]
}
```

---

### Position Book
- **URL**: `/api/v1/positions`
- **Method**: `GET`
- **Response**:
```json
{
  "status": "success",
  "data": [
    {
      "symbol": "BTCUSD",
      "exchange": "DELTA",
      "product": "PERP",
      "quantity": 10,
      "average_price": 63420.5,
      "pnl": 345.8,
      "unrealized_pnl": 210.0
    }
  ]
}
```

---

### Funds & Margin
- **URL**: `/api/v1/funds`
- **Method**: `GET`
- **Response**:
```json
{
  "status": "success",
  "data": {
    "available_balance": 8450.25,
    "used_margin": 1550.00,
    "total_equity": 10000.25,
    "currency": "USD"
  }
}
```

---

## 4. WebSocket Streaming API

- **Domestic WebSocket URL**: `ws://localhost:8765`
- **Global Crypto WebSocket URL**: `ws://localhost:8766`

### Subscription Message
```json
{
  "action": "subscribe",
  "mode": "quote",
  "symbols": ["BTCUSD", "ETHUSD"]
}
```

### Broadcast Message Format
```json
{
  "type": "quote",
  "symbol": "BTCUSD",
  "ltp": 63450.50,
  "bid": 63450.00,
  "ask": 63451.00,
  "volume": 128450,
  "timestamp": 1789914000
}
```
