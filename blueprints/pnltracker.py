import threading
import time as time_module
from datetime import datetime, timedelta
from datetime import time as dt_time
from importlib import import_module

import numpy as np
import pandas as pd
import pytz
from flask import Blueprint, jsonify, redirect, render_template, request, session, url_for
from flask_cors import cross_origin

from database.auth_db import get_api_key_for_tradingview, get_auth_token
from services.history_service import get_history
from services.tradebook_service import get_tradebook
from utils.logging import get_logger
from utils.session import check_session_validity

logger = get_logger(__name__)


def parse_trade_timestamp(timestamp_str, fallback_date=None):
    ist = pytz.timezone("Asia/Kolkata")
    if timestamp_str is None:
        return None

    if isinstance(timestamp_str, (int, float)):
        try:
            dt = pd.to_datetime(timestamp_str, unit="s")
            return dt.tz_localize("UTC").tz_convert(ist) if dt.tz is None else dt.tz_convert(ist)
        except Exception:
            return None

    if not isinstance(timestamp_str, str):
        return None

    timestamp_str = timestamp_str.strip()
    if not timestamp_str:
        return None

    formats = [
        "%d-%b-%Y %H:%M:%S",
        "%H:%M:%S %d-%m-%Y",
        "%d-%m-%Y %H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]
    for fmt in formats:
        try:
            return ist.localize(datetime.strptime(timestamp_str, fmt))
        except ValueError:
            continue

    if ":" in timestamp_str and " " not in timestamp_str:
        try:
            parts = timestamp_str.split(":")
            if len(parts) >= 2 and len(parts[0]) <= 2:
                today = fallback_date or datetime.now(ist).date()
                dt = datetime.combine(today, dt_time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0))
                return ist.localize(dt)
        except (ValueError, IndexError):
            pass

    try:
        dt = pd.to_datetime(timestamp_str)
        return dt.tz_localize(ist) if dt.tz is None else dt.tz_convert(ist)
    except Exception:
        return None


class RateLimiter:
    def __init__(self, calls_per_second=2):
        self.calls_per_second = calls_per_second
        self.min_interval = 1.0 / calls_per_second
        self.last_call_time = 0
        self.lock = threading.Lock()

    def wait(self):
        with self.lock:
            current_time = time_module.time()
            elapsed = current_time - self.last_call_time
            if elapsed < self.min_interval:
                time_module.sleep(self.min_interval - elapsed)
            self.last_call_time = time_module.time()


history_rate_limiter = RateLimiter(calls_per_second=2)
pnltracker_bp = Blueprint("pnltracker_bp", __name__, url_prefix="/")


def convert_timestamp_to_ist(df, symbol=""):
    ist = pytz.timezone("Asia/Kolkata")
    try:
        if "timestamp" in df.columns:
            try:
                df["datetime"] = pd.to_datetime(df["timestamp"], unit="s", utc=True).dt.tz_convert(ist)
            except Exception:
                try:
                    df["datetime"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True).dt.tz_convert(ist)
                except Exception:
                    df["datetime"] = pd.to_datetime(df["timestamp"])
                    df["datetime"] = df["datetime"].dt.tz_localize("UTC").dt.tz_convert(ist) if df["datetime"].dt.tz is None else df["datetime"].dt.tz_convert(ist)
        elif "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"])
            df["datetime"] = df["datetime"].dt.tz_localize("UTC").dt.tz_convert(ist) if df["datetime"].dt.tz is None else df["datetime"].dt.tz_convert(ist)
        else:
            return None

        df.set_index("datetime", inplace=True)
        return df.sort_index()
    except Exception:
        return None


@pnltracker_bp.route("/pnltracker/legacy")
@check_session_validity
def pnltracker():
    return render_template("pnltracker.html")


@pnltracker_bp.route("/pnltracker/api/pnl", methods=["POST"])
@cross_origin()
@check_session_validity
def get_pnl_data():
    try:
        broker = session.get("broker")
        if not broker:
            return jsonify({"status": "error", "message": "Broker not set in session"}), 400

        login_username = session["user"]
        auth_token = get_auth_token(login_username)
        if auth_token is None:
            return jsonify({"status": "error", "message": "Authentication required"}), 401

        api_key = get_api_key_for_tradingview(login_username)
        if not api_key:
            return jsonify({"status": "error", "message": "API key not configured."}), 401

        ist = pytz.timezone("Asia/Kolkata")
        today_str = datetime.now(ist).date().strftime("%Y-%m-%d")

        success, tradebook_response, status_code = get_tradebook(api_key=api_key)
        if not success:
            return jsonify(tradebook_response), status_code

        trades = tradebook_response.get("data", [])
        from services.positionbook_service import get_positionbook

        current_positions = {}
        try:
            success_pos, positions_response, _ = get_positionbook(api_key=api_key)
            if success_pos and "data" in positions_response:
                for pos in positions_response.get("data", []):
                    key = f"{pos['symbol']}_{pos['exchange']}"
                    current_positions[key] = {
                        "quantity": float(pos.get("quantity", 0)),
                        "average_price": float(pos.get("average_price", 0)),
                        "ltp": float(pos.get("ltp", 0)),
                        "pnl": float(pos.get("pnl", 0)),
                    }
        except Exception:
            pass

        if not trades and not current_positions:
            return jsonify({
                "status": "success",
                "data": {
                    "current_mtm": 0, "max_mtm": 0, "max_mtm_time": None,
                    "min_mtm": 0, "min_mtm_time": None, "max_drawdown": 0,
                    "pnl_series": [], "drawdown_series": [],
                },
            }), 200

        portfolio_pnl = None
        first_trade_time = None

        for trade in trades:
            trade_ts = trade.get("timestamp") or trade.get("fill_timestamp") or trade.get("fill_time")
            if trade_ts:
                t_time = parse_trade_timestamp(trade_ts)
                if t_time and (first_trade_time is None or t_time < first_trade_time):
                    first_trade_time = t_time

        if first_trade_time is None:
            first_trade_time = datetime.now(ist).replace(hour=9, minute=15, second=0, microsecond=0)

        today_str = first_trade_time.date().strftime("%Y-%m-%d")

        symbol_trades = {}
        for trade in trades:
            sym = trade.get("symbol", "")
            exch = trade.get("exchange", "")
            if not sym or not exch:
                continue
            symbol_key = f"{sym}_{exch}"
            trade_ts = trade.get("timestamp") or trade.get("fill_timestamp") or trade.get("fill_time")
            trade["parsed_time"] = parse_trade_timestamp(trade_ts) if trade_ts else None
            symbol_trades.setdefault(symbol_key, []).append(trade)

        for symbol_key, trades_list in symbol_trades.items():
            if not trades_list:
                continue

            trades_list.sort(key=lambda x: x.get("parsed_time") or datetime.min.replace(tzinfo=pytz.UTC))
            symbol = trades_list[0].get("symbol", "")
            exchange = trades_list[0].get("exchange", "")

            net_position = 0
            position_windows = []

            for trade in trades_list:
                try:
                    executed_price = float(trade.get("average_price", 0))
                    action = trade.get("action", "")
                    trade_time = trade.get("parsed_time")
                    qty = float(trade.get("quantity", 0))
                    if qty == 0 and executed_price > 0:
                        trade_val = float(trade.get("trade_value", 0))
                        qty = 1 if trade_val == executed_price else (trade_val / executed_price if trade_val > 0 else 0)
                    if qty <= 0:
                        continue
                except Exception:
                    continue

                if action == "BUY":
                    position_windows.append({"start_time": trade_time, "end_time": None, "qty": qty, "price": executed_price, "action": "BUY", "exit_price": None})
                    net_position += qty
                else:
                    if net_position > 0:
                        remaining_qty = qty
                        for window in position_windows:
                            if window["action"] == "BUY" and window["end_time"] is None and remaining_qty > 0:
                                close_qty = min(window["qty"], remaining_qty)
                                if close_qty == window["qty"]:
                                    window["end_time"] = trade_time
                                    window["exit_price"] = executed_price
                                else:
                                    window["qty"] -= close_qty
                                    closed_win = window.copy()
                                    closed_win["qty"] = close_qty
                                    closed_win["end_time"] = trade_time
                                    closed_win["exit_price"] = executed_price
                                    position_windows.append(closed_win)
                                remaining_qty -= close_qty
                        net_position -= qty
                    else:
                        position_windows.append({"start_time": trade_time, "end_time": None, "qty": qty, "price": executed_price, "action": "SELL", "exit_price": None})
                        net_position -= qty

            try:
                history_rate_limiter.wait()
                success_hist, hist_response, _ = get_history(symbol=symbol, exchange=exchange, interval="1m", start_date=today_str, end_date=today_str, api_key=api_key)
                if success_hist and "data" in hist_response:
                    df_hist = pd.DataFrame(hist_response["data"])
                    if not df_hist.empty:
                        df_hist = convert_timestamp_to_ist(df_hist, symbol)
                        if df_hist is not None:
                            current_time = datetime.now(ist)
                            if first_trade_time:
                                df_hist = df_hist[df_hist.index >= first_trade_time]
                            df_hist = df_hist[df_hist.index <= current_time]
                            df_hist = df_hist[["close"]].copy()
                            df_hist.rename(columns={"close": f"{symbol}_price"}, inplace=True)
                            df_hist[f"{symbol}_pnl"] = 0.0

                            cumulative_realized_pnl = 0.0
                            position_windows_sorted = sorted(position_windows, key=lambda x: x["start_time"] if x["start_time"] else datetime.min.replace(tzinfo=pytz.UTC))

                            for window in position_windows_sorted:
                                if window["start_time"] is None:
                                    continue
                                start = window["start_time"]
                                end = window["end_time"] if window["end_time"] else current_time
                                mask = (df_hist.index >= start) & (df_hist.index <= end)
                                is_closed = window["end_time"] is not None and window.get("exit_price") is not None

                                if mask.any():
                                    if window["action"] == "BUY":
                                        df_hist.loc[mask, f"{symbol}_pnl"] += (df_hist.loc[mask, f"{symbol}_price"] - window["price"]) * window["qty"]
                                    else:
                                        df_hist.loc[mask, f"{symbol}_pnl"] += (window["price"] - df_hist.loc[mask, f"{symbol}_price"]) * window["qty"]

                                if is_closed:
                                    realized = (window["exit_price"] - window["price"]) * window["qty"] if window["action"] == "BUY" else (window["price"] - window["exit_price"]) * window["qty"]
                                    cumulative_realized_pnl += realized

                                if window["end_time"] is not None:
                                    future_mask = df_hist.index > window["end_time"]
                                    if future_mask.any():
                                        df_hist.loc[future_mask, f"{symbol}_pnl"] = cumulative_realized_pnl
                                    elif cumulative_realized_pnl != 0 and len(df_hist) > 0:
                                        df_hist.loc[df_hist.index[-1], f"{symbol}_pnl"] = cumulative_realized_pnl

                            portfolio_pnl = df_hist[[f"{symbol}_pnl"]].copy() if portfolio_pnl is None else portfolio_pnl.join(df_hist[[f"{symbol}_pnl"]], how="outer")
            except Exception:
                continue

        if portfolio_pnl is not None:
            portfolio_pnl = portfolio_pnl.ffill().fillna(0)
            portfolio_pnl["Total_PnL"] = portfolio_pnl.sum(axis=1)
            portfolio_pnl["Peak"] = portfolio_pnl["Total_PnL"].cummax()
            portfolio_pnl["Drawdown"] = portfolio_pnl["Total_PnL"] - portfolio_pnl["Peak"]

            latest_mtm = portfolio_pnl["Total_PnL"].iloc[-1] if not portfolio_pnl.empty else 0
            max_mtm = portfolio_pnl["Total_PnL"].max() if not portfolio_pnl.empty else 0
            min_mtm = portfolio_pnl["Total_PnL"].min() if not portfolio_pnl.empty else 0
            max_drawdown = portfolio_pnl["Drawdown"].min() if not portfolio_pnl.empty else 0

            pnl_series = []
            drawdown_series = []
            for idx, row in portfolio_pnl.iterrows():
                try:
                    ts_ms = int(idx.tz_convert("UTC").timestamp() * 1000) if hasattr(idx, "tz") and idx.tz is not None else int(idx.timestamp() * 1000)
                    pnl_series.append({"time": ts_ms, "value": round(float(row.get("Total_PnL", 0)), 2)})
                    drawdown_series.append({"time": ts_ms, "value": round(float(row.get("Drawdown", 0)), 2)})
                except Exception:
                    continue

            return jsonify({
                "status": "success",
                "data": {
                    "current_mtm": round(latest_mtm, 2),
                    "max_mtm": round(max_mtm, 2),
                    "max_mtm_time": portfolio_pnl["Total_PnL"].idxmax().strftime("%H:%M") if not portfolio_pnl.empty else None,
                    "min_mtm": round(min_mtm, 2),
                    "min_mtm_time": portfolio_pnl["Total_PnL"].idxmin().strftime("%H:%M") if not portfolio_pnl.empty else None,
                    "max_drawdown": round(max_drawdown, 2),
                    "pnl_series": pnl_series,
                    "drawdown_series": drawdown_series,
                },
            }), 200

        return jsonify({
            "status": "success",
            "data": {
                "current_mtm": 0, "max_mtm": 0, "max_mtm_time": None,
                "min_mtm": 0, "min_mtm_time": None, "max_drawdown": 0,
                "pnl_series": [], "drawdown_series": [],
            },
        }), 200
    except Exception as e:
        logger.exception(f"Error calculating intraday PnL: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


# ==============================================================================
# ASYMMETRIC TAX & ACCRUED PnL ENDPOINTS (SEBI 2026 NORMS)
# ==============================================================================
@pnltracker_bp.route("/api/pnl/positions_breakdown", methods=["GET"])
@cross_origin()
def get_positions_pnl_breakdown():
    """SECTION 1: Open Positions with Gross MTM vs Estimated Net MTM."""
    from services.accounting_engine import IndianFOAccountingEngine
    from services.positionbook_service import get_positionbook
    from database.strategy_book_db import get_strategy_legs
    from utils.symbol_utils import get_contract_multiplier

    user_id = session.get("user_id")
    results = []
    total_gross_mtm = 0.0
    total_net_mtm = 0.0
    total_accrued_charges = 0.0

    try:
        ok_pb, pb_resp, _ = get_positionbook()
        positions = pb_resp.get("data") if (ok_pb and isinstance(pb_resp, dict)) else []
        if not isinstance(positions, list):
            positions = []
    except Exception:
        positions = []

    try:
        legs = get_strategy_legs(user_id=user_id)
    except Exception:
        legs = []

    strategy_by_key = {}
    for leg in legs:
        sym = leg.get("symbol") if isinstance(leg, dict) else getattr(leg, "symbol", "")
        exch = leg.get("exchange") if isinstance(leg, dict) else getattr(leg, "exchange", "NFO")
        prod = leg.get("product") if isinstance(leg, dict) else getattr(leg, "product", "MIS")
        strat = leg.get("strategy") if isinstance(leg, dict) else getattr(leg, "strategy", "Live Account")
        strategy_by_key[(sym, exch, prod)] = strat

    for pos in positions:
        qty = float(pos.get("quantity") or pos.get("net_quantity") or 0.0)
        if abs(qty) < 1e-6:
            continue

        sym = str(pos.get("symbol", ""))
        exch = str(pos.get("exchange", "NFO"))
        prod = str(pos.get("product", "MIS"))
        strat_name = strategy_by_key.get((sym, exch, prod), "Live Account")

        entry_price = float(pos.get("average_price") or pos.get("buy_price") or pos.get("price") or 0.0)
        ltp = float(pos.get("ltp") if pos.get("ltp") is not None else pos.get("last_price") or entry_price)
        direction = "BUY" if qty > 0 else "SELL"
        is_opt = ("CE" in sym or "PE" in sym) and "FUT" not in sym

        mult = get_contract_multiplier(sym, exch)
        total_contract_qty = abs(int(qty * mult))

        calc = IndianFOAccountingEngine.calculate_open_position_mtm(
            entry_price=entry_price,
            current_ltp=ltp,
            qty=total_contract_qty,
            direction=direction,
            is_option=is_opt
        )

        total_gross_mtm += calc["gross_mtm"]
        total_net_mtm += calc["net_mtm"]
        total_accrued_charges += calc["accrued_and_exit_charges"]

        results.append({
            "trade_id": str(pos.get("position_id") or f"{sym}_{int(qty)}"),
            "strategy_name": strat_name,
            "symbol": sym,
            "direction": direction,
            "quantity": total_contract_qty,
            "entry_price": entry_price,
            "current_ltp": ltp,
            "entry_time": str(pos.get("entry_time") or datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            "gross_mtm": calc["gross_mtm"],
            "net_mtm": calc["net_mtm"],
            "charges_breakdown": calc
        })

    # DuckDB fallback with NaN safety
    if not results:
        try:
            import duckdb
            import os
            db_path = os.getenv("DUCKDB_PATH", os.path.abspath("db/historify.duckdb"))
            if os.path.exists(db_path):
                con = duckdb.connect(db_path, read_only=True)
                tables = [t[0] for t in con.execute("SHOW TABLES").fetchall()]
                if "strategy_trades" in tables:
                    pos_df = con.execute("SELECT * FROM strategy_trades WHERE status = 'OPEN'").df()
                    for _, row in pos_df.iterrows():
                        entry_p = float(row.get("entry_price", 0))
                        raw_ltp = row.get("current_ltp")
                        ltp = float(raw_ltp) if pd.notna(raw_ltp) and float(raw_ltp) > 0 else entry_p
                        
                        calc = IndianFOAccountingEngine.calculate_open_position_mtm(
                            entry_price=entry_p,
                            current_ltp=ltp,
                            qty=int(row.get("quantity", 1)),
                            direction=str(row.get("direction", "BUY")),
                            is_option=("CE" in str(row.get("symbol", "")) or "PE" in str(row.get("symbol", ""))) and "FUT" not in str(row.get("symbol", ""))
                        )
                        total_gross_mtm += calc["gross_mtm"]
                        total_net_mtm += calc["net_mtm"]
                        total_accrued_charges += calc["accrued_and_exit_charges"]
                        results.append({
                            "trade_id": str(row.get("trade_id", "")),
                            "strategy_name": str(row.get("strategy_name", "")),
                            "symbol": str(row.get("symbol", "")),
                            "direction": str(row.get("direction", "BUY")),
                            "quantity": int(row.get("quantity", 1)),
                            "entry_price": entry_p,
                            "current_ltp": ltp,
                            "entry_time": str(row.get("entry_time", "")),
                            "gross_mtm": calc["gross_mtm"],
                            "net_mtm": calc["net_mtm"],
                            "charges_breakdown": calc
                        })
                con.close()
        except Exception as e:
            logger.debug(f"[POSITIONS DUCKDB READ] {e}")

    return jsonify({
        "status": "success",
        "summary": {
            "total_open_positions": len(results),
            "total_gross_mtm": round(total_gross_mtm, 2),
            "total_estimated_net_mtm": round(total_net_mtm, 2),
            "total_accrued_charges": round(total_accrued_charges, 2)
        },
        "positions": results
    })


@pnltracker_bp.route("/api/pnl/historical_report", methods=["GET"])
@cross_origin()
def get_historical_strategy_pnl_report():
    """SECTION 2: Historical Realized PnL with dynamic timeframe filtering."""
    from services.strategy_pnl_service import get_multi_timeframe_strategy_analytics

    filter_type = request.args.get("filter_type", "7D").upper()
    strategy_filter = request.args.get("strategy", None)
    start_date_str = request.args.get("start_date", None)
    end_date_str = request.args.get("end_date", None)

    data = get_multi_timeframe_strategy_analytics(
        timeframe=filter_type,
        strategy=strategy_filter,
        start_date=start_date_str,
        end_date=end_date_str
    )

    strats = data.get("strategies", {})
    strategies_summary = []

    for name, s in strats.items():
        if strategy_filter and strategy_filter != "ALL" and name != strategy_filter:
            continue

        strategies_summary.append({
            "strategy_name": name,
            "total_trades": s.get("total_trades", 0),
            "win_rate": f"{s.get('win_rate', 0.0)}%",
            "profit_factor": s.get("profit_factor", 0.0),
            "max_drawdown": s.get("max_drawdown", 0.0),
            "gross_pnl": s.get("gross_pnl", 0.0),
            "total_charges": s.get("total_charges", 0.0),
            "net_pnl": s.get("net_pnl", 0.0),
            "unrealized_pnl": s.get("unrealized_pnl", 0.0),
            "total_pnl": s.get("total_pnl", 0.0),
            "tax_breakdown": s.get("tax_breakdown", {})
        })

    return jsonify({
        "status": "success",
        "filter_applied": filter_type,
        "portfolio_totals": data.get("portfolio_summary", {}),
        "strategies_breakdown": strategies_summary,
        "trades": data.get("all_trades", [])
    })
