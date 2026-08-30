"""
services/strategy_pnl_service.py
Per-strategy realized / unrealized / total P&L with statutory tax accumulation.
"""

import os
import json
import re
from datetime import datetime, timedelta, time as dt_time
from typing import Any

import pandas as pd
import pytz
from utils.logging import get_logger

logger = get_logger(__name__)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_trade_timestamp(timestamp_str, fallback_date=None):
    """Safely parse trade timestamps across all broker formats into IST datetime."""
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
                dt = datetime.combine(
                    today,
                    dt_time(int(parts[0]), int(parts[1]), int(parts[2]) if len(parts) > 2 else 0),
                )
                return ist.localize(dt)
        except (ValueError, IndexError):
            pass

    try:
        dt = pd.to_datetime(timestamp_str)
        return dt.tz_localize(ist) if dt.tz is None else dt.tz_convert(ist)
    except Exception:
        return None


def pnl_from_book(
    legs: list[dict[str, Any]],
    positions: list[dict[str, Any]] | None = None,
    strategy: str | None = None,
) -> dict[str, Any]:
    positions = positions or []
    ltp_by_key = {
        (p.get("symbol"), p.get("exchange"), p.get("product")): _f(
            p.get("ltp") if p.get("ltp") is not None else p.get("last_price")
        )
        for p in positions
    }

    grouped: dict[str, dict[str, Any]] = {}
    for leg in legs:
        name = leg.get("strategy") or "untagged"
        if strategy and name != strategy:
            continue
        entry = grouped.setdefault(
            name,
            {
                "strategy": name,
                "realized": 0.0,
                "today_realized": 0.0,
                "unrealized": 0.0,
                "total": 0.0,
                "open_quantity": 0.0,
                "unpriced_legs": 0,
                "legs": [],
            },
        )

        qty = _f(leg.get("quantity"))
        avg = _f(leg.get("average_price"))
        realized = _f(leg.get("realized_pnl"))
        today_realized = _f(leg.get("today_realized_pnl"))

        key = (leg.get("symbol"), leg.get("exchange"), leg.get("product"))
        ltp = ltp_by_key.get(key)
        unrealized = 0.0
        if abs(qty) > 1e-9:
            if ltp is None:
                entry["unpriced_legs"] += 1
            else:
                from utils.symbol_utils import get_contract_multiplier
                mult = get_contract_multiplier(leg.get("symbol"), leg.get("exchange"))
                unrealized = qty * (ltp - avg) * mult
            entry["open_quantity"] += qty

        entry["realized"] += realized
        entry["today_realized"] += today_realized
        entry["unrealized"] += unrealized
        entry["legs"].append({
            "symbol": leg.get("symbol"),
            "exchange": leg.get("exchange"),
            "product": leg.get("product"),
            "quantity": round(qty, 4),
            "average_price": round(avg, 4),
            "ltp": ltp,
            "realized": round(realized, 4),
            "today_realized": round(today_realized, 4),
            "unrealized": round(unrealized, 4),
        })

    for entry in grouped.values():
        entry["legs"].sort(key=lambda leg: abs(leg["quantity"]) <= 1e-9)
        entry["realized"] = round(entry["realized"], 4)
        entry["today_realized"] = round(entry["today_realized"], 4)
        entry["unrealized"] = round(entry["unrealized"], 4)
        entry["total"] = round(entry["realized"] + entry["unrealized"], 4)
        entry["today_total"] = round(entry["today_realized"] + entry["unrealized"], 4)
        entry["open_quantity"] = round(entry["open_quantity"], 4)

    if strategy:
        return grouped.get(
            strategy,
            {
                "strategy": strategy,
                "realized": 0.0,
                "today_realized": 0.0,
                "unrealized": 0.0,
                "total": 0.0,
                "today_total": 0.0,
                "open_quantity": 0.0,
                "unpriced_legs": 0,
                "legs": [],
            },
        )
    return grouped


def get_strategy_pnl(client, strategy: str | None = None, user_id: str | None = None) -> dict[str, Any]:
    from database.strategy_book_db import StrategyBookUnavailable, get_strategy_legs

    try:
        legs = get_strategy_legs(user_id=user_id, strategy=strategy)
    except StrategyBookUnavailable as exc:
        logger.error(f"Strategy P&L unavailable: {exc}")
        return {"status": "error", "message": f"Strategy book unavailable: {exc}"}

    positions_resp = client.positionbook() or {}
    if positions_resp.get("status") == "error":
        message = positions_resp.get("error") or positions_resp.get("message") or "unavailable"
        return {"status": "error", "message": f"Position book unavailable: {message}"}
    
    positions = positions_resp.get("data") or []
    if not isinstance(positions, list):
        positions = []

    result = pnl_from_book(legs, positions, strategy=strategy)
    if strategy:
        return {"status": "success", **result}
    return {"status": "success", "strategies": result, "count": len(result)}


def get_multi_timeframe_strategy_analytics(
    timeframe: str = "1D",
    user_id: str | None = None,
    strategy: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Calculate multi-timeframe strategy P&L with exact statutory tax accumulation and custom date filtering."""
    from database.strategy_book_db import (
        StrategyBookUnavailable, get_strategy_legs, list_strategies,
        db_session, is_initialized, init_strategy_book_db,
    )
    from services.positionbook_service import get_positionbook
    from services.accounting_engine import IndianFOAccountingEngine

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)

    if not is_initialized():
        try:
            init_strategy_book_db()
        except Exception:
            pass

    # Calendar day boundaries
    tf_upper = str(timeframe or "1D").upper()
    days_map = {"1D": 1, "2D": 2, "1W": 7, "7D": 7, "2W": 14, "15D": 15, "1M": 30, "30D": 30, "ALL": 3650}

    if tf_upper == "1D":
        start_dt = now_ist.replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
        end_dt = now_ist.replace(tzinfo=None)
        days = 1
    elif tf_upper == "CUSTOM" and start_date and end_date:
        try:
            start_dt = datetime.strptime(start_date, "%Y-%m-%d")
            end_dt = datetime.strptime(f"{end_date} 23:59:59", "%Y-%m-%d %H:%M:%S")
            days = max(1, (end_dt.date() - start_dt.date()).days + 1)
        except Exception:
            days = 7
            start_dt = (now_ist - timedelta(days=6)).replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
            end_dt = now_ist.replace(tzinfo=None)
    else:
        days = days_map.get(tf_upper, 1)
        start_dt = (now_ist - timedelta(days=days - 1)).replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
        end_dt = now_ist.replace(tzinfo=None)

    try:
        legs = get_strategy_legs(user_id=user_id, strategy=strategy)
    except StrategyBookUnavailable:
        legs = []

    try:
        ok, pos_resp, _ = get_positionbook()
        positions = pos_resp.get("data") if (ok and isinstance(pos_resp, dict)) else []
        if not isinstance(positions, list):
            positions = []
    except Exception:
        positions = []

    ltp_by_key = {
        (p.get("symbol"), p.get("exchange"), p.get("product")): _f(
            p.get("ltp") if p.get("ltp") is not None else p.get("last_price")
        )
        for p in positions
    }

    # Tradebook ingestion with time filtering
    strategy_trades_map: dict[str, list[dict[str, Any]]] = {}
    try:
        from services.tradebook_service import get_tradebook
        ok_tb, tb_resp, _ = get_tradebook()
        raw_trades = tb_resp.get("data") if (ok_tb and isinstance(tb_resp, dict)) else []
        if isinstance(raw_trades, list):
            for tr in raw_trades:
                raw_ts = tr.get("trade_timestamp") or tr.get("timestamp") or tr.get("fill_time")
                parsed_tz = parse_trade_timestamp(raw_ts) if raw_ts else None
                parsed_dt = parsed_tz.replace(tzinfo=None) if parsed_tz else None

                if parsed_dt is not None:
                    if not (start_dt <= parsed_dt <= end_dt):
                        continue

                strat_name = tr.get("strategy") or "untagged"
                strategy_trades_map.setdefault(strat_name, []).append(tr)
    except Exception:
        strategy_trades_map = {}

    # Sandbox trades ingestion
    try:
        from database.sandbox_db import SandboxTrades
        sb_trades = db_session.query(SandboxTrades)
        if user_id:
            sb_trades = sb_trades.filter(SandboxTrades.user_id == user_id)
        for st in sb_trades.all():
            raw_ts = st.trade_timestamp
            parsed_tz = parse_trade_timestamp(raw_ts) if raw_ts else None
            parsed_dt = parsed_tz.replace(tzinfo=None) if parsed_tz else None

            if parsed_dt is not None:
                if not (start_dt <= parsed_dt <= end_dt):
                    continue

            strat_name = st.strategy or "untagged"
            tr_dict = {
                "symbol": st.symbol,
                "exchange": st.exchange,
                "product": st.product,
                "action": st.action,
                "quantity": st.quantity,
                "price": float(st.price or 0),
                "strategy": strat_name,
                "trade_timestamp": str(st.trade_timestamp),
            }
            strategy_trades_map.setdefault(strat_name, []).append(tr_dict)
    except Exception:
        pass

    # Built-in fallback alias configurations
    configured_strategies: dict[str, dict[str, Any]] = {
        "Post10_Institutional_OB_VWAP": {
            "name": "Post10_Institutional_OB_VWAP",
            "aliases": {
                "Post10_Institutional_OB_VWAP",
                "Post10_Institutional_OB_VWAP_Production",
                "Post10_Institutional_OB_VWAP_Production_V3",
                "Post10_Institutional_OB_VWAP_Production_V4",
            },
        },
        "3Min_ORB_Quant": {
            "name": "3Min_ORB_Quant",
            "aliases": {
                "3Min_ORB_Quant",
                "3Min_ORB_2Lot_Quant_V2",
                "3Min_ORB_Quant_20260801205330",
            },
        },
        "SMC_FVG_ZeroLag_Options": {
            "name": "SMC_FVG_ZeroLag_Options",
            "aliases": {
                "SMC_FVG_ZeroLag_Options",
                "SMC_FVG_ZeroLag_Options_20260817232106",
            },
        },
        "Prime Indicator Scalper Options": {
            "name": "Prime Indicator Scalper Options",
            "aliases": {
                "Prime Indicator Scalper Options",
                "Prime_Indicator_Scalper_Options",
            },
        },
        "Liquid Sweep Options": {
            "name": "Liquid Sweep Options",
            "aliases": {
                "Liquid Sweep Options",
                "liquid_sweep_options_20260808185609",
                "NSE_LiquiditySweepScalper_V43",
            },
        },
        "Multi-commodity Institutional": {
            "name": "Multi-commodity Institutional",
            "aliases": {
                "Multi-commodity Institutional",
                "MCX_Institutional_MIS_V3.0",
                "MCX_Institutional_MIS_V2.9",
                "MCX_Institutional_MIS_V2.6",
                "MCX_GOLDM_FVG_Options_Scalper",
            },
        },
    }

    # Overlay strategy_configs.json if present
    config_path = "strategies/strategy_configs.json"
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfgs = json.load(f)
                for s_key, s_val in cfgs.items():
                    disp_name = s_val.get("name") or s_key
                    aliases = {disp_name, s_key}
                    file_path = s_val.get("file_path") or ""
                    if file_path and os.path.exists(file_path):
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as fp:
                            code = fp.read()
                        matches = re.findall(r'strategy[^\n\r=:]*[:=]\s*["\']([^"\']+)["\']', code, re.IGNORECASE)
                        for m in matches:
                            if m and m.lower() not in ("utf-8", "options", "equity", "futures"):
                                aliases.add(m)
                    if disp_name in configured_strategies:
                        configured_strategies[disp_name]["aliases"].update(aliases)
                    else:
                        configured_strategies[disp_name] = {"name": disp_name, "aliases": aliases}
        except Exception:
            pass

    all_known_strategies = set(list_strategies(user_id=user_id)) | set(strategy_trades_map.keys()) | set(configured_strategies.keys())
    target_strategies = [strategy] if (strategy and strategy != "ALL") else sorted(list(all_known_strategies))

    strategy_metrics: dict[str, dict[str, Any]] = {}

    for strat_key in target_strategies:
        disp_name = strat_key
        aliases = {strat_key}
        if strat_key in configured_strategies:
            disp_name = strat_key
            aliases = configured_strategies[strat_key]["aliases"]
        else:
            for cfg_name, cfg_info in configured_strategies.items():
                if strat_key in cfg_info["aliases"]:
                    disp_name = cfg_name
                    aliases = cfg_info["aliases"]
                    break

        if disp_name in strategy_metrics and disp_name != strat_key:
            continue

        matched_trades: list[dict[str, Any]] = []
        for alias in aliases:
            if alias in strategy_trades_map:
                matched_trades.extend(strategy_trades_map[alias])

        matched_trades.sort(key=lambda x: str(x.get("trade_timestamp") or x.get("timestamp") or x.get("fill_time") or ""))
        trade_count = len(matched_trades)

        symbol_trade_groups = {}
        for t in matched_trades:
            k = (t.get("symbol"), t.get("exchange") or "NFO", t.get("product") or "MIS")
            symbol_trade_groups.setdefault(k, []).append(t)

        strat_gross_pnl = 0.0
        strat_net_pnl = 0.0
        strat_unrealized_mtm = 0.0
        strat_charges = {
            "brokerage": 0.0, "stt": 0.0, "exchange_charges": 0.0,
            "stamp_duty": 0.0, "sebi_charges": 0.0, "gst": 0.0, "total": 0.0
        }
        trade_legs = []
        closed_trades_records = []
        trade_open_qty = 0.0

        daily_pnl_map = {}
        if tf_upper == "CUSTOM" and start_date and end_date:
            curr_d = start_dt.date()
            end_d = end_dt.date()
            while curr_d <= end_d and len(daily_pnl_map) < 90:
                daily_pnl_map[curr_d.strftime("%Y-%m-%d")] = 0.0
                curr_d += timedelta(days=1)
        else:
            for i in range(min(days, 30) - 1, -1, -1):
                d_str = (now_ist - timedelta(days=i)).strftime("%Y-%m-%d")
                daily_pnl_map[d_str] = 0.0

        for (sym, exch, prod), tr_list in symbol_trade_groups.items():
            from utils.symbol_utils import get_contract_multiplier
            mult = get_contract_multiplier(sym, exch)

            buy_qty = sum(_f(t.get("quantity") or t.get("qty")) for t in tr_list if str(t.get("action") or t.get("trade_type")).upper() == "BUY")
            buy_val = sum(_f(t.get("price")) * _f(t.get("quantity") or t.get("qty")) for t in tr_list if str(t.get("action") or t.get("trade_type")).upper() == "BUY")
            sell_qty = sum(_f(t.get("quantity") or t.get("qty")) for t in tr_list if str(t.get("action") or t.get("trade_type")).upper() == "SELL")
            sell_val = sum(_f(t.get("price")) * _f(t.get("quantity") or t.get("qty")) for t in tr_list if str(t.get("action") or t.get("trade_type")).upper() == "SELL")

            closed_qty = min(buy_qty, sell_qty)
            net_qty = buy_qty - sell_qty
            trade_open_qty += net_qty

            leg_realized = 0.0
            leg_tax_charges = 0.0
            if closed_qty > 0 and tr_list:
                avg_buy = (buy_val / buy_qty) if buy_qty > 0 else 0.0
                avg_sell = (sell_val / sell_qty) if sell_qty > 0 else 0.0
                first_action = str(tr_list[0].get("action") or tr_list[0].get("trade_type") or "BUY").upper()

                entry_p = avg_buy if first_action == "BUY" else avg_sell
                exit_p = avg_sell if first_action == "BUY" else avg_buy
                is_opt = ("CE" in str(sym) or "PE" in str(sym)) and "FUT" not in str(sym)

                buy_orders = len([t for t in tr_list if str(t.get("action") or t.get("trade_type")).upper() == "BUY"])
                sell_orders = len([t for t in tr_list if str(t.get("action") or t.get("trade_type")).upper() == "SELL"])
                round_trip_count = max(1, min(buy_orders, sell_orders))
                custom_brok_per_order = round_trip_count * IndianFOAccountingEngine.BROKERAGE_PER_ORDER

                tax_calc = IndianFOAccountingEngine.calculate_closed_trade_pnl(
                    entry_price=entry_p,
                    exit_price=exit_p,
                    qty=int(closed_qty * mult),
                    direction=first_action,
                    brokerage_per_order=custom_brok_per_order,
                    is_option=is_opt
                )

                leg_realized = tax_calc["gross_pnl"]
                leg_tax_charges = tax_calc["total_charges"]
                strat_gross_pnl += tax_calc["gross_pnl"]
                strat_net_pnl += tax_calc["net_pnl"]

                for fee_k in ["brokerage", "stt", "exchange_charges", "stamp_duty", "sebi_charges", "gst"]:
                    strat_charges[fee_k] += tax_calc[fee_k]
                strat_charges["total"] += tax_calc["total_charges"]

                # Extract execution timestamps for frontend trade receipt
                entry_ts_raw = tr_list[0].get("trade_timestamp") or tr_list[0].get("timestamp") or tr_list[0].get("fill_time") or ""
                exit_ts_raw = tr_list[-1].get("trade_timestamp") or tr_list[-1].get("timestamp") or tr_list[-1].get("fill_time") or ""

                closed_trades_records.append({
                    "symbol": sym,
                    "strategy": disp_name,
                    "direction": first_action,
                    "quantity": int(closed_qty * mult),
                    "entry_price": round(entry_p, 2),
                    "exit_price": round(exit_p, 2),
                    "entry_time": str(entry_ts_raw),
                    "exit_time": str(exit_ts_raw),
                    "gross_pnl": tax_calc["gross_pnl"],
                    "net_pnl": tax_calc["net_pnl"],
                    "total_charges": tax_calc["total_charges"],
                    "brokerage": tax_calc["brokerage"],
                    "stt": tax_calc["stt"],
                    "exchange_charges": tax_calc["exchange_charges"],
                    "stamp_duty": tax_calc["stamp_duty"],
                    "sebi_charges": tax_calc["sebi_charges"],
                    "gst": tax_calc["gst"]
                })

                # Accumulate realized PnL into the daily timeline
                exit_dt = parse_trade_timestamp(exit_ts_raw)
                exit_d_str = exit_dt.strftime("%Y-%m-%d") if exit_dt else now_ist.strftime("%Y-%m-%d")
                if exit_d_str in daily_pnl_map:
                    daily_pnl_map[exit_d_str] += tax_calc["net_pnl"]

            leg_unrealized = 0.0
            leg_net_unrealized = 0.0
            ltp = ltp_by_key.get((sym, exch, prod))
            if abs(net_qty) > 1e-9 and ltp is not None:
                entry_avg = (
                    (buy_val / buy_qty)
                    if net_qty > 0 and buy_qty > 0
                    else ((sell_val / sell_qty) if sell_qty > 0 else 0.0)
                )
                open_dir = "BUY" if net_qty > 0 else "SELL"
                is_opt = ("CE" in str(sym) or "PE" in str(sym)) and "FUT" not in str(sym)
                mtm_calc = IndianFOAccountingEngine.calculate_open_position_mtm(
                    entry_price=entry_avg,
                    current_ltp=ltp,
                    qty=int(abs(net_qty) * mult),
                    direction=open_dir,
                    is_option=is_opt
                )
                leg_unrealized = mtm_calc["gross_mtm"]
                leg_net_unrealized = mtm_calc["net_mtm"]
                strat_unrealized_mtm += leg_unrealized

            trade_legs.append({
                "symbol": sym,
                "exchange": exch,
                "product": prod,
                "quantity": round(net_qty, 4),
                "average_price": round(
                    (buy_val / buy_qty) if net_qty > 0 and buy_qty > 0 else ((sell_val / sell_qty) if net_qty < 0 and sell_qty > 0 else 0.0), 4
                ),
                "ltp": ltp,
                "realized": round(leg_realized, 4),
                "today_realized": round(leg_realized, 4),
                "unrealized": round(leg_unrealized, 4),
                "net_unrealized": round(leg_net_unrealized, 4),
                "tax_charges": round(leg_tax_charges, 4),
            })

        winning_trades = sum(1 for t in closed_trades_records if t["net_pnl"] > 0)
        win_rate = round((winning_trades / len(closed_trades_records)) * 100.0, 1) if closed_trades_records else 0.0

        # Mathematical Profit Factor: Gross Wins / Gross Losses
        gross_wins = sum(t["gross_pnl"] for t in closed_trades_records if t["gross_pnl"] > 0)
        gross_losses = abs(sum(t["gross_pnl"] for t in closed_trades_records if t["gross_pnl"] < 0))
        if gross_losses > 0:
            profit_factor = round(gross_wins / gross_losses, 2)
        elif gross_wins > 0:
            profit_factor = 99.0
        else:
            profit_factor = 0.0

        # Sequence-based maximum drawdown
        cum_pnl = 0.0
        peak = 0.0
        max_dd = 0.0
        for tr in closed_trades_records:
            cum_pnl += tr["net_pnl"]
            if cum_pnl > peak:
                peak = cum_pnl
            dd = cum_pnl - peak
            if dd < max_dd:
                max_dd = dd

        eff_realized = strat_net_pnl
        eff_total = eff_realized + strat_unrealized_mtm
        active_legs_count = len([l for l in trade_legs if abs(_f(l.get("quantity", 0))) > 1e-9])
        has_activity = bool(trade_count > 0 or abs(eff_total) > 1e-6 or active_legs_count > 0)

        # Include unrealized MTM into today's timeline
        today_iso = now_ist.strftime("%Y-%m-%d")
        if today_iso in daily_pnl_map:
            daily_pnl_map[today_iso] += strat_unrealized_mtm

        daily_series = [{"date": d, "pnl": round(daily_pnl_map[d], 2)} for d in sorted(daily_pnl_map.keys())]

        strategy_metrics[disp_name] = {
            "strategy": disp_name,
            "timeframe": tf_upper,
            "realized_pnl": round(eff_realized, 2),
            "unrealized_pnl": round(strat_unrealized_mtm, 2),
            "total_pnl": round(eff_total, 2),
            "gross_pnl": round(strat_gross_pnl, 2),
            "net_pnl": round(strat_net_pnl, 2),
            "total_charges": round(strat_charges["total"], 2),
            "tax_breakdown": {k: round(v, 2) for k, v in strat_charges.items()},
            "open_quantity": round(trade_open_qty, 2),
            "total_trades": len(closed_trades_records) or trade_count,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown": round(max_dd, 2),
            "avg_trade_pnl": round(eff_total / (len(closed_trades_records) or 1), 2),
            "active_positions_count": active_legs_count,
            "has_activity": has_activity,
            "legs": trade_legs,
            "closed_trades": closed_trades_records,
            "daily_pnl_history": daily_series,
        }

    active_strats = [s for s in strategy_metrics.values() if s.get("has_activity")]
    total_port_gross = sum(s["gross_pnl"] for s in strategy_metrics.values())
    total_port_charges = sum(s["total_charges"] for s in strategy_metrics.values())
    total_port_net = sum(s["net_pnl"] for s in strategy_metrics.values())
    total_port_pnl = sum(s["total_pnl"] for s in strategy_metrics.values())
    total_port_trades = sum(s["total_trades"] for s in strategy_metrics.values())
    winning_strats = len([s for s in strategy_metrics.values() if s["total_pnl"] > 0])
    losing_strats = len([s for s in strategy_metrics.values() if s["total_pnl"] < 0])

    port_wins = sum(s["gross_pnl"] for s in strategy_metrics.values() if s["gross_pnl"] > 0)
    port_losses = abs(sum(s["gross_pnl"] for s in strategy_metrics.values() if s["gross_pnl"] < 0))
    port_profit_factor = round(port_wins / port_losses, 2) if port_losses > 0 else (99.0 if port_wins > 0 else 0.0)

    port_win_trades = sum(sum(1 for t in s.get("closed_trades", []) if t["net_pnl"] > 0) for s in strategy_metrics.values())
    port_win_rate = round((port_win_trades / total_port_trades) * 100.0, 1) if total_port_trades > 0 else 0.0

    top_performer = (
        max(strategy_metrics.values(), key=lambda s: s["total_pnl"])["strategy"]
        if strategy_metrics and any(s["total_pnl"] > 0 for s in strategy_metrics.values())
        else (max(strategy_metrics.values(), key=lambda s: s["total_trades"])["strategy"] if strategy_metrics else "None")
    )

    all_trades = []
    for s in strategy_metrics.values():
        all_trades.extend(s.get("closed_trades", []))

    return {
        "status": "success",
        "timeframe": tf_upper,
        "days": days,
        "as_of": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "portfolio_summary": {
            "gross_profit": round(total_port_gross, 2),
            "total_deductions": round(total_port_charges, 2),
            "net_realized_profit": round(total_port_net, 2),
            "total_pnl": round(total_port_pnl, 2),
            "total_trades": total_port_trades,
            "win_rate": f"{port_win_rate}%",
            "profit_factor": port_profit_factor,
            "active_strategies_count": len(active_strats) if active_strats else len(strategy_metrics),
            "total_strategies_count": len(strategy_metrics),
            "winning_strategies_count": winning_strats,
            "losing_strategies_count": losing_strats,
            "top_performer": top_performer,
        },
        "strategies": strategy_metrics,
        "all_trades": all_trades,
    }
