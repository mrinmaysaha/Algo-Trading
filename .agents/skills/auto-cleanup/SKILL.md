---
name: auto-cleanup
description: Automatically purge temporary scratch scripts, test CSVs, extracted data dumps, and ephemeral test files created during analysis, debugging, and backtesting workflows so only production artifacts remain.
---

# Auto-Cleanup Skill & Hygiene Protocol

This skill enforces strict workspace hygiene by purging all ephemeral test files immediately after executing diagnostic, backtesting, or debugging workflows.

## 1. Scratch & Ephemeral File Isolation
- Whenever running ad-hoc calculations, grid sweeps, or temporary backtests, always write to the persistent agent scratch directory: `<appDataDir>\brain\<conversation-id>\scratch\` or standard system temp directories.
- Never pollute the root workspace or `strategies/` directory with one-off runner scripts or temporary `.csv` / `.txt` dumps.

## 2. Immediate Post-Task Cleanup Sequence
Run the following PowerShell cleanup command immediately upon completion of any backtest, optimization sweep, or data inspection:

```powershell
# Remove workspace scratch directory if created
if (Test-Path 'scratch') { Remove-Item -Recurse -Force 'scratch' }

# Remove temporary backtest CSVs and diagnostic artifacts
Get-ChildItem -Path '.' -Filter '*test*.csv' -ErrorAction SilentlyContinue | Remove-Item -Force
Get-ChildItem -Path '.' -Filter '*backtest_results*.csv' -ErrorAction SilentlyContinue | Remove-Item -Force
Get-ChildItem -Path '.' -Filter '*fine_tune*.csv' -ErrorAction SilentlyContinue | Remove-Item -Force
Get-ChildItem -Path '.' -Filter '*intraday_results*.csv' -ErrorAction SilentlyContinue | Remove-Item -Force
```

## 3. Allowed Production Artifacts
Only the following files should remain in the repository:
1. Production Strategy scripts in `strategies/scripts/<strategy_name>.py`
2. Configuration registry in `strategies/strategy_configs.json`
3. Supervisor registration in `strategies/portfolio_supervisor.py`
4. Standard repository unit tests in `test/test_*.py`
