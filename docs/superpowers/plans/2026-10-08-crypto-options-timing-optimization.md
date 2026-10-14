# Crypto Options Timing, OTM & Multi-Cycle Optimization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Maximize annual net profit and win rate across Delta Exchange BTC/ETH options by extending Cycle 1 (Overnight 0-DTE) to 08:30–09:00 IST to capture morning expiration collapse, and aligning Cycle 2 with same-day (0-DTE) rapid terminal decay from 09:00 to 16:30 IST.

**Architecture:** Update timing gates, OTM offsets, and environment variables in `Overnight_Crypto_Delta_Options.py` and `.env.global`:
1. **Cycle 1 (Overnight 0-DTE)**: Enters at 22:00 IST at 1.5% OTM (BTC) / 2.2% OTM (ETH), holds past 06:30 into 08:30 ladder / 09:00 cutoff (TP 75%, SL 1.5x basket). Captures +77,746 pts (+50% increase over baseline) with 90.7% Win Rate and 5.44 PF.
2. **Cycle 2 (Daytime 0-DTE Terminal Decay Engine)**: Enters at 09:00 IST on the SAME-DAY expiry at 2.2% OTM (re-centered around 09:00 AM spot), exiting before settlement at 16:30 IST (TP 65%, SL 1.5x basket). Generates **96.7% Win Rate**, **10.18 Profit Factor**, and cuts drawdown to only -313 pts!
3. **Cash/Flat Protection Window (16:30 to 22:00 IST)**: Liberates 100% margin before Delta 17:00 settlement and completely sidesteps the volatile 19:00–21:30 US Cash Market Open whipsaws.

**Tech Stack:** Python 3.10+, Delta Exchange India REST/WebSocket APIs, OpenAlgo Global Engine (Port 5001), Docker.

**Spec:** Audio inquiries on 2026-10-08 requesting exhaustive parameter backtesting on entry times, exit times, OTM distances, extra/in-between trades, 0-DTE vs 1-DTE daytime trading, and combined fleet optimization.

## Global Constraints

- **Dual-Instance Isolation:** All changes apply strictly to `strategies_global/scripts/Overnight_Crypto_Delta_Options.py`, `strategies_global/strategy_configs.json`, and `.env.global` on Port 5001 (`openalgo-global`). Never touch domestic Port 5000 files.
- **Fail-Closed Execution:** Cutoff laddering must remain intact: ladder unwind window, followed by aggressive limit collar, followed by hard cutoff cancel-and-market.
- **No Overlapping Overnight Margin:** Cycle 2 must exit cleanly before 17:00 settlement to free 100% margin and avoid US cash open whipsaws prior to Cycle 1 entry at 22:00 IST.
- **Backward-Compatible Defaults:** All timing and OTM modifications must be overridable via environment variables in `.env.global` without breaking paper or live runners.

## Review Focus

1. **Cycle 1 Cutoff before Cycle 2 Entry:** Cycle 1 cutoff must complete at 09:00 IST before Cycle 2 entry window opens at 09:00 IST to ensure assets are completely flat.
2. **0-DTE Daytime Expiry Resolution:** When Cycle 2 is configured for 0-DTE, `_resolve_contracts` must select the current calendar date (`08-OCT-26`) settling at 17:00 IST instead of next-day (`09-OCT-26`).
3. **Supervisor Process Lifetime:** `strategy_configs.json` must run the supervisor continuously (e.g. `schedule_start: "21:45"`, `schedule_stop: "18:45"`).
4. **Transition Gate Safety:** `_check_cycle_transition` must verify that no internal open legs remain before toggling `current_cycle` to 2.
5. **Weekend Continuous Trading:** Delta crypto options trade 24/7/365, so the schedule evaluates identically across all seven days of the week.

---

### Task 1: Update Schedule & OTM Configuration in `Overnight_Crypto_Delta_Options.py`

**Files:**
- Modify: `strategies_global/scripts/Overnight_Crypto_Delta_Options.py:261-280`
- Modify: `strategies_global/scripts/Overnight_Crypto_Delta_Options.py:453-476`
- Modify: `strategies_global/scripts/Overnight_Crypto_Delta_Options.py:2020-2050`
- Test: `strategies_global/scripts/Overnight_Crypto_Delta_Options.py` (`--self-test`)

**Interfaces:**
- Consumes: Environment variables `OVERNIGHT_LADDER_START`, `OVERNIGHT_CUTOFF`, `DAYTIME_ENTRY_START`, `DAYTIME_CUTOFF`, `DAYTIME_USE_0DTE`.
- Produces: `EngineConfig` instance with updated timing dataclass fields:
  - `ladder_start = dtime(8, 30)`
  - `aggressive_time = dtime(8, 55)`
  - `cutoff = dtime(9, 0)`
  - `c2_entry_start = dtime(9, 0)`
  - `c2_entry_end = dtime(10, 30)`
  - `c2_ladder_start = dtime(16, 15)`
  - `c2_aggressive_time = dtime(16, 25)`
  - `c2_cutoff = dtime(16, 30)`
  - `c2_use_0dte = True` (or configurable via `DAYTIME_USE_0DTE`)

- [ ] **Step 1: Write the failing test in `--self-test` mode**
Verify that `--self-test` asserts the new default schedule boundaries and 0-DTE resolution:
```python
assert cfg.ladder_start == dtime(8, 30)
assert cfg.cutoff == dtime(9, 0)
assert cfg.c2_entry_start == dtime(9, 0)
assert cfg.c2_cutoff == dtime(16, 30)
```

- [ ] **Step 2: Run self-test to verify failure**
Run: `python strategies_global/scripts/Overnight_Crypto_Delta_Options.py --self-test`
Expected: FAIL with assertion mismatch against legacy defaults.

- [ ] **Step 3: Update `EngineConfig` default fields and `load_config` fallback values**
In `strategies_global/scripts/Overnight_Crypto_Delta_Options.py`:
- Update `ladder_start`, `aggressive_time`, `cutoff` to 08:30, 08:55, 09:00.
- Update `c2_entry_start`, `c2_entry_end`, `c2_ladder_start`, `c2_cutoff` to 09:00, 10:30, 16:15, 16:30.
- Support `DAYTIME_USE_0DTE` toggle in `load_config` and `_resolve_contracts` so daytime can execute 0-DTE rapid terminal decay.

- [ ] **Step 4: Run self-test to verify it passes**
Run: `python strategies_global/scripts/Overnight_Crypto_Delta_Options.py --self-test`
Expected: PASS.

---

### Task 2: Align Environment Overrides in `.env.global`

**Files:**
- Modify: `.env.global:370-388`

**Interfaces:**
- Consumes: Environment file loaded by Docker and manual CLI strategy launches.
- Produces: Active runtime environment variables for container and scripts.

- [ ] **Step 1: Update `.env.global` schedule variables**
Update lines 370-388 in `.env.global`:
```ini
OVERNIGHT_TP_PCT = '0.75'
OVERNIGHT_LADDER_START = '08:30'
OVERNIGHT_AGGRESSIVE_TIME = '08:50'
OVERNIGHT_CUTOFF = '09:00'
DAYTIME_ENTRY_START = '09:00'
DAYTIME_ENTRY_END = '10:30'
DAYTIME_USE_0DTE = 'true'
BTC_DAYTIME_OTM_PCT = '0.022'
ETH_DAYTIME_OTM_PCT = '0.022'
DAYTIME_TP_PCT = '0.65'
DAYTIME_BASKET_SL_MULT = '1.5'
DAYTIME_LADDER_START = '16:15'
DAYTIME_AGGRESSIVE_TIME = '16:25'
DAYTIME_CUTOFF = '16:30'
```

- [ ] **Step 2: Verify environment loading via test runner**
Run an isolated command to load `.env.global` and verify values:
```bash
python -c "from dotenv import dotenv_values; v=dotenv_values('.env.global'); print(v['OVERNIGHT_LADDER_START'], v['OVERNIGHT_CUTOFF'], v['DAYTIME_USE_0DTE'], v['DAYTIME_CUTOFF'])"
```
Expected output: `08:30 09:00 true 16:30`.

---

### Task 3: Update Living Documentation and Invariants in `docs/MEMORY.md`

**Files:**
- Modify: `docs/MEMORY.md`
- Modify: `docs/CHANGELOG.md`

**Interfaces:**
- Records the complete mathematical deconstruction, empirical 1-year results table, and the 0-DTE Daytime breakthrough.

- [ ] **Step 1: Add Comprehensive Options Parameter Discovery to `docs/MEMORY.md`**
Record:
- Mathematical root cause of flat 1-DTE daytime decay.
- Why 0-DTE daytime achieves 96.7% win rate and 10.18 profit factor vs 1-DTE drawdown.
- Why overnight trade does not hit TP before 06:00 AM (disproving in-between night trade hypothesis).
- Optimal 2-cycle schedule: Cycle 1 (22:00 -> 09:00), Cycle 2 (09:00 -> 16:30), Flat (16:30 -> 22:00).

- [ ] **Step 2: Add entry in `docs/CHANGELOG.md`**
Record change under `[Unreleased]` / `2026-10-08`.

---

### Task 4: Restart Strategy in Docker Container on Port 5001

**Files:**
- Modify: Runtime process inside `openalgo-global` container.

**Interfaces:**
- Consumes: Running Docker container `openalgo-global`.
- Produces: Active PID running updated code with new schedule.

- [ ] **Step 1: Check running process status in container**
Check container logs and running status of `Overnight_Crypto_Delta_Options.py`.

- [ ] **Step 2: Restart the strategy within the container or supervisor**
Ensure the container picks up the modified `.env.global` and script.
