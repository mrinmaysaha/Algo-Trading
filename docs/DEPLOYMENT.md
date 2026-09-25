# OpenAlgo Deployment & Operations Guide

## 1. Dual-Container Production Architecture

OpenAlgo runs in production using two isolated Docker containers managed via Docker Compose or Systemd:

```
┌────────────────────────────────────────────────────────┐
│                      Host Server                       │
│                                                        │
│   ┌────────────────────────┐  ┌────────────────────┐   │
│   │ Container:             │  │ Container:         │   │
│   │ openalgo-angel         │  │ openalgo-global    │   │
│   │                        │  │                    │   │
│   │ HTTP: 5000             │  │ HTTP: 5001         │   │
│   │ WebSocket: 8765        │  │ WebSocket: 8766    │   │
│   │ Market: NSE/BSE/MCX    │  │ Market: Delta Crypto│   │
│   │ Env: .env              │  │ Env: .env.global   │   │
│   │ DB: openalgo.db        │  │ DB: openalgo_delta │   │
│   └────────────────────────┘  └────────────────────┘   │
└────────────────────────────────────────────────────────┘
```

---

## 2. Docker Compose Configuration (`docker-compose.yaml`)

```yaml
version: '3.8'

services:
  openalgo-angel:
    build: .
    container_name: openalgo-angel
    restart: unless-stopped
    ports:
      - "5000:5000"
      - "8765:8765"
    env_file:
      - .env
    volumes:
      - ./data:/app/data
      - ./strategies:/app/strategies

  openalgo-global:
    build: .
    container_name: openalgo-global
    restart: unless-stopped
    ports:
      - "5001:5001"
      - "8766:8766"
    env_file:
      - .env.global
    volumes:
      - ./strategies_global:/app/strategies_global
    environment:
      - PORT=5001
      - WS_PORT=8766
```

---

## 3. Operational Commands

### Start / Restart Instances
```bash
# Start both containers in background
docker-compose up -d

# Restart only crypto instance after strategy updates
docker-compose restart openalgo-global

# Check container logs
docker logs -f openalgo-global --tail 100
```

### Inspecting Running Strategy Daemons
Inside `openalgo-global`:
```bash
docker exec -it openalgo-global ps aux | grep python
```
