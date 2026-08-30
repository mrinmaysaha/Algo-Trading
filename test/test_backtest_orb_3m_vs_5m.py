import os
import sys
from datetime import datetime, timedelta
import pandas as pd

# Add workspace root to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtesting.engine import run_python_strategy_backtest
from database.historify_db import get_ohlcv
from backtesting.universal_runner import UniversalStrategyRunner


def run_orb_comparison():
    strategy_path = "strategies/scripts/3Min_ORB_Quant_20260801205330.py"
    symbol = "BANKNIFTY"
    lookback_days = 60
    initial_capital = 100000.0

    print("=" * 90)
    print(f"3-MINUTE ORB QUANT STRATEGY BACKTEST AUDIT: 3m vs 5m on {symbol} ({lookback_days} Days)")
    print("=" * 90)

    # -------------------------------------------------------------
    # 1. RUN 3-MINUTE BACKTEST (Engine vs Direct DuckDB)
    # -------------------------------------------------------------
    print("\n>>> [A] EXECUTING 3-MINUTE (3m) BACKTEST...")
    res_engine_3m = run_python_strategy_backtest(
        strategy_path=strategy_path,
        symbols=[symbol],
        interval="3m",
        lookback_days=lookback_days,
        initial_capital=initial_capital,
        api_key="",
        host_server="http://127.0.0.1:5000",
        exchange="NSE_INDEX",
        source="db"
    )

    metrics_3m = res_engine_3m.get("metrics", {}) or res_engine_3m.get("performance", {})
    trades_3m = res_engine_3m.get("trades", [])

    # Direct 3m Replay
    end_date = datetime.now().date()
    start_date = end_date - timedelta(days=lookback_days)
    start_ts = int(datetime.combine(start_date, datetime.min.time()).timestamp())
    end_ts = int(datetime.combine(end_date, datetime.max.time()).timestamp())

    df_raw_3m = get_ohlcv(symbol, "NSE_INDEX", "3m", start_ts, end_ts)
    if not df_raw_3m.empty:
        df_raw_3m["datetime"] = pd.to_datetime(df_raw_3m["timestamp"], unit="s")
        df_raw_3m = df_raw_3m.sort_values("datetime").reset_index(drop=True)
        try:
            df_raw_3m = df_raw_3m.set_index("datetime", drop=False).between_time("09:15", "15:30").reset_index(drop=True)
        except Exception:
            pass

        runner_3m = UniversalStrategyRunner(
            strategy_instance=strategy_path,
            config=res_engine_3m.get("parameters", {})
        )
        direct_trades_3m = runner_3m.run_simulation(symbol, df_raw_3m)
    else:
        direct_trades_3m = []

    assert len(trades_3m) == len(direct_trades_3m), f"3m Discrepancy: Engine={len(trades_3m)}, Direct={len(direct_trades_3m)}"
    print(f" [3m PARITY PASS] Engine ({len(trades_3m)} trades) == Direct DuckDB Replay ({len(direct_trades_3m)} trades)")

    # -------------------------------------------------------------
    # 2. RUN 5-MINUTE BACKTEST (Engine vs Direct DuckDB)
    # -------------------------------------------------------------
    print("\n>>> [B] EXECUTING 5-MINUTE (5m) BACKTEST...")
    res_engine_5m = run_python_strategy_backtest(
        strategy_path=strategy_path,
        symbols=[symbol],
        interval="5m",
        lookback_days=lookback_days,
        initial_capital=initial_capital,
        api_key="",
        host_server="http://127.0.0.1:5000",
        exchange="NSE_INDEX",
        source="db"
    )

    metrics_5m = res_engine_5m.get("metrics", {}) or res_engine_5m.get("performance", {})
    trades_5m = res_engine_5m.get("trades", [])

    # Direct 5m Replay
    df_raw_5m = get_ohlcv(symbol, "NSE_INDEX", "5m", start_ts, end_ts)
    if not df_raw_5m.empty:
        df_raw_5m["datetime"] = pd.to_datetime(df_raw_5m["timestamp"], unit="s")
        df_raw_5m = df_raw_5m.sort_values("datetime").reset_index(drop=True)
        try:
            df_raw_5m = df_raw_5m.set_index("datetime", drop=False).between_time("09:15", "15:30").reset_index(drop=True)
        except Exception:
            pass

        runner_5m = UniversalStrategyRunner(
            strategy_instance=strategy_path,
            config=res_engine_5m.get("parameters", {})
        )
        direct_trades_5m = runner_5m.run_simulation(symbol, df_raw_5m)
    else:
        direct_trades_5m = []

    assert len(trades_5m) == len(direct_trades_5m), f"5m Discrepancy: Engine={len(trades_5m)}, Direct={len(direct_trades_5m)}"
    print(f" [5m PARITY PASS] Engine ({len(trades_5m)} trades) == Direct DuckDB Replay ({len(direct_trades_5m)} trades)")

    # -------------------------------------------------------------
    # 3. SIDE-BY-SIDE 3m vs 5m COMPARATIVE SUMMARY
    # -------------------------------------------------------------
    print("\n" + "=" * 90)
    print(f"{'Performance Metric':<30}{'3-Minute Interval (3m)':<30}{'5-Minute Interval (5m)':<30}")
    print("=" * 90)
    print(f"{'Mother Candle Window':<30}{'09:15 - 09:18 IST (3 min)':<30}{'09:15 - 09:20 IST (5 min)':<30}")
    print(f"{'Candles Processed':<30}{f'{len(df_raw_3m):,} bars':<30}{f'{len(df_raw_5m):,} bars':<30}")
    print(f"{'Total Trades':<30}{str(metrics_3m.get('Total Trades', len(trades_3m))):<30}{str(metrics_5m.get('Total Trades', len(trades_5m))):<30}")
    print(f"{'Winning Trades':<30}{str(metrics_3m.get('Winning Trades', 0)):<30}{str(metrics_5m.get('Winning Trades', 0)):<30}")
    print(f"{'Losing Trades':<30}{str(metrics_3m.get('Losing Trades', 0)):<30}{str(metrics_5m.get('Losing Trades', 0)):<30}")
    print(f"{'Win Rate':<30}{str(metrics_3m.get('Win Rate [%]', 0)) + '%':<30}{str(metrics_5m.get('Win Rate [%]', 0)) + '%':<30}")
    print(f"{'Gross Profit':<30}{'₹{:,.2f}'.format(metrics_3m.get('Gross Profit', 0)):<30}{'₹{:,.2f}'.format(metrics_5m.get('Gross Profit', 0)):<30}")
    print(f"{'Gross Loss':<30}{'₹{:,.2f}'.format(metrics_3m.get('Gross Loss', 0)):<30}{'₹{:,.2f}'.format(metrics_5m.get('Gross Loss', 0)):<30}")
    print(f"{'Total Charges / Taxes':<30}{'₹{:,.2f}'.format(metrics_3m.get('Total Charges [₹]', 0)):<30}{'₹{:,.2f}'.format(metrics_5m.get('Total Charges [₹]', 0)):<30}")
    print(f"{'Net Realized PnL':<30}{'₹{:,.2f}'.format(metrics_3m.get('Total PnL [₹]', 0)):<30}{'₹{:,.2f}'.format(metrics_5m.get('Total PnL [₹]', 0)):<30}")
    print(f"{'Profit Factor':<30}{str(metrics_3m.get('Profit Factor', 0)):<30}{str(metrics_5m.get('Profit Factor', 0)):<30}")
    print(f"{'Max Drawdown':<30}{str(metrics_3m.get('Max Drawdown [%]', 0)) + '%':<30}{str(metrics_5m.get('Max Drawdown [%]', 0)) + '%':<30}")
    print("=" * 90)

    # 4. Print First 5 Trades for 3m
    print("\n[Sample 3m Trades]:")
    for t in trades_3m[:5]:
        print(f" Trade #{t.get('trade_id')}: {t.get('option_type')} | Entry: {t.get('entry_time')} @ {t.get('entry_spot'):.2f} -> Exit: {t.get('exit_time')} @ {t.get('exit_spot'):.2f} | Net: ₹{t.get('net_pnl_rs'):+.2f} ({t.get('exit_reason')})")

    # 5. Print First 5 Trades for 5m
    print("\n[Sample 5m Trades]:")
    for t in trades_5m[:5]:
        print(f" Trade #{t.get('trade_id')}: {t.get('option_type')} | Entry: {t.get('entry_time')} @ {t.get('entry_spot'):.2f} -> Exit: {t.get('exit_time')} @ {t.get('exit_spot'):.2f} | Net: ₹{t.get('net_pnl_rs'):+.2f} ({t.get('exit_reason')})")


if __name__ == "__main__":
    run_orb_comparison()
