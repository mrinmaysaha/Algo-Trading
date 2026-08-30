# backtesting/universal_runner.py
"""
Master Multi-Asset Backtesting Orchestrator & Universal Strategy Runner.
Implements Indian F&O multi-symbol risk enforcement, BSM/Black-76 option pricing,
and production-grade trade execution replay.
"""
import datetime
import os
import re
import ast
from typing import Dict, List, Optional, Any
import numpy as np
import pandas as pd

from backtesting.config.asset_registry import (
    INDIAN_ASSET_SPECS,
    get_historical_lot_size,
    calculate_exact_dte,
    validate_strategy_config
)
from backtesting.pricing.option_models import IndianOptionPricingEngine
from backtesting.execution.tax_engine import IndianTaxEngine
from backtesting.adapters.technical_engine import TechnicalEngine
from backtesting.adapters.strategy_protocol import (
    StrategyProtocol,
    LiveStrategyAdapter,
    GenericIndicatorStrategy,
    Signal
)
from backtesting.analytics.stats_engine import (
    PerformanceAnalytics,
    StockMockPnLMatrix,
    build_run_manifest,
    get_assumptions_disclosure
)


class DataUnavailableError(Exception):
    """Raised when market data is unavailable. Enforces zero fake-data generation."""
    pass


class PortfolioRiskManager:
    """Enforces multi-symbol intraday risk guardrails inside backtests."""

    def __init__(self, config: Dict):
        self.max_total_trades_per_day = config.get("max_total_trades_per_day", 10)
        self.max_trades_per_index = config.get("max_trades_per_index", 3)
        self.max_daily_loss = config.get("max_daily_loss", 5000.0)

        self.current_date: Optional[datetime.date] = None
        self.total_trades_today = 0
        self.realized_pnl_today = 0.0
        self.index_trades_today: Dict[str, int] = {}
        self.trading_halted_today = False

    def reset_if_new_day(self, date_val: datetime.date):
        if self.current_date != date_val:
            self.current_date = date_val
            self.total_trades_today = 0
            self.realized_pnl_today = 0.0
            self.index_trades_today = {}
            self.trading_halted_today = False

    def can_open_trade(self, symbol: str) -> bool:
        if self.trading_halted_today:
            return False
        if self.total_trades_today >= self.max_total_trades_per_day:
            return False
        if self.index_trades_today.get(symbol, 0) >= self.max_trades_per_index:
            return False
        if self.realized_pnl_today <= -abs(self.max_daily_loss):
            self.trading_halted_today = True
            return False
        return True

    def record_trade_result(self, symbol: str, net_pnl: float):
        self.total_trades_today += 1
        self.index_trades_today[symbol] = self.index_trades_today.get(symbol, 0) + 1
        self.realized_pnl_today += net_pnl
        if self.realized_pnl_today <= -abs(self.max_daily_loss):
            self.trading_halted_today = True


class UniversalStrategyRunner:
    """Master Multi-Asset Backtesting Orchestrator."""

    def __init__(self, strategy_instance: Any, config: Dict = None, slippage_pts: float = 0.5):
        if config is None:
            config = {}
        if isinstance(strategy_instance, str) and os.path.exists(strategy_instance):
            self.strategy_path = strategy_instance
            with open(strategy_instance, "r", encoding="utf-8") as f:
                self.code = f.read()
            strategy_instance = None
        else:
            self.strategy_path = ""
            self.code = ""

        self.config = validate_strategy_config(config)
        self.slippage_pts = slippage_pts
        self.risk_manager = PortfolioRiskManager(self.config)

        self.adapter: StrategyProtocol = LiveStrategyAdapter(strategy_instance, self.config, strategy_path=self.strategy_path)

    def extract_parameters(self) -> dict:
        """Extracts CONFIG dictionary or os.getenv(...) parameters dynamically."""
        params = dict(self.config)
        if self.code:
            matches = re.finditer(r'os\.getenv\(\s*["\']([A-Z_0-9]+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)', self.code)
            for match in matches:
                key, val = match.groups()
                if key in ["OPENALGO_API_KEY", "HOST_SERVER", "OPENALGO_HOST", "WEBSOCKET_URL", "WEBSOCKET_HOST", "WEBSOCKET_PORT", "STRATEGY_NAME"]:
                    continue
                if val.isdigit():
                    params[key] = int(val)
                elif val.replace('.', '', 1).isdigit() and val.count('.') < 2:
                    params[key] = float(val)
                elif val.lower() in ['true', 'false']:
                    params[key] = val.lower() == 'true'
                else:
                    params[key] = val
        return params

    def compute_option_premium(
        self, symbol: str, underlying_price: float, strike: float, dte_days: float, option_type: str
    ) -> float:
        spec = INDIAN_ASSET_SPECS.get(symbol.upper(), {"pricing_model": "BSM", "default_iv": 0.18})
        if spec["pricing_model"] == "BLACK76":
            return IndianOptionPricingEngine.price_mcx_commodity_option(
                futures_price=underlying_price,
                strike=strike,
                dte_days=dte_days,
                iv=spec["default_iv"],
                option_type=option_type
            )
        else:
            return IndianOptionPricingEngine.price_nse_index_option(
                spot=underlying_price,
                strike=strike,
                dte_days=dte_days,
                iv=spec["default_iv"],
                option_type=option_type
            )

    def run_simulation(self, symbol: str, df: pd.DataFrame, params: dict = None) -> List[Dict]:
        if params and isinstance(params, dict):
            self.config.update(params)
            self.config = validate_strategy_config(self.config)

        sym_upper = symbol.upper()
        spec = INDIAN_ASSET_SPECS.get(sym_upper, {"exchange": "NSE_INDEX", "strike_step": 50})

        if df.empty:
            raise DataUnavailableError(f"Market history DataFrame for {symbol} is empty.")

        # Normalize Schema
        df = df.copy()
        if "datetime" not in df.columns:
            if "timestamp" in df.columns:
                df["datetime"] = pd.to_datetime(df["timestamp"])
            else:
                df["datetime"] = pd.to_datetime(df.index)

        df["datetime"] = pd.to_datetime(df["datetime"])
        df = df.sort_values("datetime").reset_index(drop=True)

        df_calc = TechnicalEngine.calculate_all(df, self.config)
        if df_calc is None or df_calc.empty:
            raise DataUnavailableError(f"Insufficient candles to compute indicators for {symbol}.")

        trades = []
        trade_id = 1
        in_pos = False
        pos_state: Optional[Dict] = None

        sl_raw = self.config.get("sl_atr_mult") or self.config.get("atr_sl_mult") or 1.2
        tp_raw = self.config.get("tp_atr_mult") or self.config.get("atr_tp_mult") or 4.0
        sl_atr_mult = float(sl_raw) if sl_raw is not None else 1.2
        tp_atr_mult = float(tp_raw) if tp_raw is not None else 4.0

        is_mcx = spec.get("exchange") == "MCX" or sym_upper in ["GOLDM", "SILVERM", "CRUDEOILM", "NATGASMINI", "NATURALGASMINI"]
        default_start = datetime.time(9, 15) if is_mcx else datetime.time(9, 30)
        default_end = datetime.time(23, 0) if is_mcx else datetime.time(14, 30)
        default_sqoff = datetime.time(23, 25) if is_mcx else datetime.time(15, 15)

        start_time = self.config.get("start_time", default_start)
        end_time = self.config.get("end_time", default_end)
        square_off_time = self.config.get("square_off_time", default_sqoff)

        reg_lot_override = self.config.get("indices_registry", {}).get(sym_upper, {}).get("lot_size")
        default_lots = self.config.get("indices_registry", {}).get(sym_upper, {}).get("default_lots", 1)

        # Synchronous Replay Loop
        for i in range(len(df_calc) - 1):
            curr_bar = df_calc.iloc[i]
            next_bar = df_calc.iloc[i + 1]

            t_curr = pd.to_datetime(curr_bar["datetime"])
            date_curr = t_curr.date()
            now_time = t_curr.time()

            self.risk_manager.reset_if_new_day(date_curr)

            if now_time < start_time:
                continue

            lot_sz = get_historical_lot_size(sym_upper, date_curr.strftime("%Y-%m-%d"), reg_lot_override) * default_lots

            from backtesting.config.asset_registry import resolve_expiry_date
            expiry_date = resolve_expiry_date(sym_upper, date_curr)
            dte_days = calculate_exact_dte(t_curr.to_pydatetime(), expiry_date)

            # --- 1. EVALUATE EXITS FOR OPEN POSITIONS ---
            if in_pos and pos_state is not None:
                c_high = float(curr_bar["high"])
                c_low = float(curr_bar["low"])
                c_close = float(curr_bar["close"])
                atr_val = float(curr_bar["atr"])

                updated_sl, tsl_act = self.adapter.update_trailing_sl(pos_state, c_high, c_low, atr_val)
                pos_state["stop_loss"] = updated_sl
                pos_state["tsl_activated"] = tsl_act

                exit_triggered = False
                exit_spot = c_close
                exit_reason = "SIGNAL"

                next_bar_time = pd.to_datetime(next_bar["datetime"])
                is_last_bar_of_session = (i == len(df_calc) - 2) or (next_bar_time.date() > date_curr) or (now_time >= square_off_time)

                if is_last_bar_of_session:
                    exit_triggered = True
                    exit_spot = c_close
                    exit_reason = f"SESSION_SQUARE_OFF ({square_off_time.strftime('%H:%M')} IST)"
                else:
                    sl_hit = (pos_state["position"] == "CE" and c_low <= pos_state["stop_loss"]) or \
                             (pos_state["position"] == "PE" and c_high >= pos_state["stop_loss"])
                    tp_hit = (pos_state["position"] == "CE" and c_high >= pos_state["take_profit"]) or \
                             (pos_state["position"] == "PE" and c_low <= pos_state["take_profit"])

                    # Priority given to SL if both limits breached in same candle
                    if sl_hit:
                        exit_triggered = True
                        exit_spot = pos_state["stop_loss"]
                        exit_reason = "STEP_TSL_HIT" if pos_state["tsl_activated"] else "INITIAL_SL_HIT"
                    elif tp_hit:
                        exit_triggered = True
                        exit_spot = pos_state["take_profit"]
                        exit_reason = "TARGET_HIT"

                if exit_triggered:
                    is_futures = pos_state.get("instrument_type") == "FUTURES" or self.config.get("instrument_type") == "FUTURES"
                    if is_futures:
                        spot_diff = (exit_spot - pos_state["entry_spot"]) if pos_state["position"] in ["CE", "LONG", "BUY"] else (pos_state["entry_spot"] - exit_spot)
                        gross_pnl_rs = spot_diff * lot_sz
                        fill_exit = exit_spot
                        entry_fill = pos_state["entry_spot"]
                        costs = {"total_charges": round(0.0003 * (entry_fill + fill_exit) * lot_sz, 2)}
                    else:
                        raw_exit = self.compute_option_premium(sym_upper, exit_spot, pos_state["strike"], dte_days, pos_state["position"])
                        fill_exit = max(0.05, raw_exit - self.slippage_pts)
                        entry_fill = pos_state["entry_premium"]
                        gross_pnl_rs = (fill_exit - entry_fill) * lot_sz
                        costs = IndianTaxEngine.calculate_charges(entry_fill, fill_exit, lot_sz, exchange=spec["exchange"])

                    net_pnl_rs = round(gross_pnl_rs - costs["total_charges"], 2)

                    self.risk_manager.record_trade_result(sym_upper, net_pnl_rs)

                    option_pnl_pts = round(fill_exit - entry_fill, 2)
                    spot_pnl_pts = round(exit_spot - pos_state["entry_spot"], 2) if pos_state["position"] == "CE" else round(pos_state["entry_spot"] - exit_spot, 2)

                    trades.append({
                        "trade_id": trade_id,
                        "symbol": sym_upper,
                        "exchange": spec["exchange"],
                        "strike": pos_state["strike"],
                        "option_type": pos_state["position"],
                        "direction": f"Call ({pos_state['position']})" if pos_state["position"] == "CE" else f"Put ({pos_state['position']})",
                        "action": f"BUY {pos_state['position']}",
                        "entry_time": pos_state["entry_time"].strftime("%Y-%m-%d %H:%M"),
                        "exit_time": t_curr.strftime("%Y-%m-%d %H:%M"),
                        "entry_spot": pos_state["entry_spot"],
                        "exit_spot": exit_spot,
                        "entry_price": pos_state["entry_spot"],
                        "exit_price": exit_spot,
                        "entry_premium": entry_fill,
                        "exit_premium": fill_exit,
                        "pnl_pts": option_pnl_pts,
                        "spot_pnl_pts": spot_pnl_pts,
                        "gross_pnl_rs": round(gross_pnl_rs, 2),
                        "charges_rs": costs["total_charges"],
                        "net_pnl_rs": net_pnl_rs,
                        "pnl": net_pnl_rs,
                        "result": "WIN" if net_pnl_rs > 0 else "LOSS",
                        "exit_reason": exit_reason,
                    })

                    trade_id += 1
                    in_pos = False
                    pos_state = None

            # --- 2. EVALUATE ENTRIES (Fill at Next-Bar Open) ---
            next_bar_dt = pd.to_datetime(next_bar["datetime"])
            if not in_pos and now_time <= end_time and next_bar_dt.date() == date_curr and self.risk_manager.can_open_trade(sym_upper):
                hist_slice = df_calc.iloc[max(0, i - 50): i + 1]
                sig_obj = self.adapter.evaluate_entry_signal(hist_slice, sym_upper)

                if sig_obj and sig_obj.action == "ENTER" and sig_obj.option_type in ["CE", "PE"]:
                    signal = sig_obj.option_type
                    fill_spot = float(next_bar["open"])
                    fill_time = pd.to_datetime(next_bar["datetime"])
                    atr_entry = float(curr_bar["atr"])

                    strike_step = spec["strike_step"]
                    atm_strike = round(fill_spot / strike_step) * strike_step

                    raw_entry = self.compute_option_premium(sym_upper, fill_spot, atm_strike, dte_days, signal)
                    fill_entry = raw_entry + self.slippage_pts

                    if signal == "CE":
                        sl_price = fill_spot - (atr_entry * sl_atr_mult)
                        tp_price = fill_spot + (atr_entry * tp_atr_mult)
                    else:
                        sl_price = fill_spot + (atr_entry * sl_atr_mult)
                        tp_price = fill_spot - (atr_entry * tp_atr_mult)

                    in_pos = True
                    pos_state = {
                        "position": signal,
                        "strike": atm_strike,
                        "entry_spot": fill_spot,
                        "entry_time": fill_time,
                        "entry_premium": fill_entry,
                        "stop_loss": sl_price,
                        "take_profit": tp_price,
                        "last_step_high": fill_spot if signal == "CE" else None,
                        "last_step_low": fill_spot if signal == "PE" else None,
                        "tsl_activated": False,
                    }

        return trades


def run_production_backtest(
    strategy_config: Dict,
    strategy_instance: Any,
    symbol_data_map: Dict[str, pd.DataFrame],
    strategy_path: str = "",
    initial_capital: float = 100000.0,
    slippage_pts: float = 0.5
) -> Dict:
    """Primary Multi-Asset Orchestrator delivering complete UI payload."""
    runner = UniversalStrategyRunner(strategy_instance, strategy_config, slippage_pts=slippage_pts)

    all_trades = []
    portfolio_breakdown = []
    price_charts = {}

    symbols_processed = list(symbol_data_map.keys())

    for symbol, df_hist in symbol_data_map.items():
        sym_trades = runner.run_simulation(symbol, df_hist)
        all_trades.extend(sym_trades)

        sym_pnl = sum(t["net_pnl_rs"] for t in sym_trades)
        portfolio_breakdown.append({
            "symbol": symbol,
            "trades": len(sym_trades),
            "pnl": round(sym_pnl, 2),
            "win_rate": round(sum(1 for t in sym_trades if t["result"] == "WIN") / len(sym_trades) * 100.0, 2) if sym_trades else 0.0,
            "return_pct": round((sym_pnl / (initial_capital / max(1, len(symbols_processed)))) * 100.0, 2)
        })

        candles = []
        step = 1 if len(df_hist) <= 5000 else max(1, len(df_hist) // 5000)
        for idx_row, row in df_hist.iloc[::step].iterrows():
            candles.append({
                "time": str(row.get("datetime", idx_row))[:16],
                "open": round(float(row.get("open", 0)), 2),
                "high": round(float(row.get("high", 0)), 2),
                "low": round(float(row.get("low", 0)), 2),
                "close": round(float(row.get("close", 0)), 2),
                "volume": int(row.get("volume", 0))
            })

        signals = []
        for t in sym_trades:
            signals.append({
                "time": t["entry_time"],
                "type": f"buy_{t['option_type'].lower()}",
                "label": f"BUY {t['option_type']}",
                "price": t["entry_spot"],
                "symbol": symbol
            })

        price_charts[symbol] = {"candles": candles, "signals": signals}

    all_trades = sorted(all_trades, key=lambda x: x["entry_time"])
    for idx, t in enumerate(all_trades, 1):
        t["trade_id"] = idx

    first_df = list(symbol_data_map.values())[0] if symbol_data_map else pd.DataFrame()
    start_date = str(first_df["datetime"].iloc[0])[:10] if not first_df.empty and "datetime" in first_df.columns else "2026-01-01"
    end_date = str(first_df["datetime"].iloc[-1])[:10] if not first_df.empty and "datetime" in first_df.columns else "2026-12-31"

    metrics = PerformanceAnalytics.calculate_metrics(all_trades, initial_capital, start_date, end_date)
    pnl_matrix = StockMockPnLMatrix(all_trades, initial_capital)

    equity_curve = []
    drawdown_curve = []
    peak = initial_capital
    curr_eq = initial_capital

    date_series = pd.bdate_range(start=start_date, end=end_date)
    trade_df = pd.DataFrame(all_trades) if all_trades else pd.DataFrame()

    for dt in date_series:
        dt_str = dt.strftime("%Y-%m-%d")
        day_pnl = trade_df[trade_df["exit_time"].str.slice(0, 10) == dt_str]["net_pnl_rs"].sum() if not trade_df.empty else 0.0

        curr_eq += day_pnl
        if curr_eq > peak:
            peak = curr_eq
        dd = round(((peak - curr_eq) / peak) * 100.0, 2) if peak > 0 else 0.0

        equity_curve.append({"date": dt_str, "value": round(curr_eq, 2)})
        drawdown_curve.append({"date": dt_str, "drawdown": -dd})

    data_meta = {
        "symbols": symbols_processed,
        "start_date": start_date,
        "end_date": end_date,
        "total_candles": sum(len(df) for df in symbol_data_map.values())
    }

    manifest = build_run_manifest(strategy_path, strategy_config, data_meta)
    assumptions = get_assumptions_disclosure(slippage_pts)

    from backtesting.analytics.stats_engine import sanitize_json_types

    return sanitize_json_types({
        "status": "success",
        "symbol": " / ".join(symbols_processed),
        "symbols": symbols_processed,
        "metrics": metrics,
        "performance": metrics,
        "monthly_pnl_matrix": pnl_matrix.generate_matrix().to_dict(),
        "heatmap_html": pnl_matrix.to_html_heatmap(),
        "parameters": strategy_config,
        "equity_curve": equity_curve,
        "drawdown_curve": drawdown_curve,
        "trades": all_trades,
        "price_charts": price_charts,
        "portfolio_breakdown": portfolio_breakdown,
        "assumptions": assumptions,
        "manifest": manifest
    })
