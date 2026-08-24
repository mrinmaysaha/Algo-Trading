"""
Per-strategy realized / unrealized / total P&L.

The broker - and OpenAlgo's own position book - nets positions per
`(symbol, exchange, product)` and carries no strategy label, so a position
alone cannot answer "how is *this* strategy doing?". Two strategies trading
the same contract are indistinguishable downstream.

`database/strategy_book_db.py` keeps a parallel book keyed by strategy, fed
from the event bus. This module reads it:

* **realized** - taken from the book, which accumulates across sessions and
  survives restarts
* **unrealized** - computed here by marking open quantity to the position
  book's last traded price, because a stored value would be stale the instant
  it was written
* **total** - realized + unrealized (plus `today_realized` / `today_total`
  for intraday exits)

Accounting convention: weighted-average cost, where a position flipping
through zero realizes the closed leg and reopens the remainder at the fill
price.
"""

from typing import Any

from utils.logging import get_logger

logger = get_logger(__name__)


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def pnl_from_book(
    legs: list[dict[str, Any]],
    positions: list[dict[str, Any]] | None = None,
    strategy: str | None = None,
) -> dict[str, Any]:
    """Aggregate the persisted per-strategy legs into realized / unrealized /
    total, marking open quantity to the position book's last traded price.

    Realized comes from the book (it accumulates across sessions and survives
    restarts). Unrealized is computed here rather than stored, because it is a
    function of a price that changes continuously.
    """
    positions = positions or []
    # `ltp` is the standardized OpenAlgo position field (see
    # docs/api/account-services/positionbook.md); every broker mapper converts
    # its own raw field into it. `last_price` is accepted only as a fallback
    # for a mapper that passes the broker's name through unchanged.
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
                # Open per the book but absent from the position book, so it
                # cannot be marked to market. Surfaced rather than silently
                # counted as zero.
                entry["unpriced_legs"] += 1
            else:
                from utils.symbol_utils import get_contract_multiplier

                mult = get_contract_multiplier(leg.get("symbol"), leg.get("exchange"))
                unrealized = qty * (ltp - avg) * mult
            entry["open_quantity"] += qty

        entry["realized"] += realized
        entry["today_realized"] += today_realized
        entry["unrealized"] += unrealized
        entry["legs"].append(
            {
                "symbol": leg.get("symbol"),
                "exchange": leg.get("exchange"),
                "product": leg.get("product"),
                "quantity": round(qty, 4),
                "average_price": round(avg, 4),
                "ltp": ltp,
                "realized": round(realized, 4),
                "today_realized": round(today_realized, 4),
                "unrealized": round(unrealized, 4),
            }
        )

    for entry in grouped.values():
        # Open legs first, insertion order preserved within each group. Flow
        # workflows address a leg positionally (`{{pnl.legs[0].average_price}}`)
        # because the node vocabulary has no way to filter a list, and the
        # strategy book never prunes a leg that has gone flat. Without this,
        # `legs[0]` is the *oldest* row - a closed leg whose average price the
        # book has reset to 0 - so a percentage exit divides by zero and stops
        # firing from the strategy's second trading day onward.
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


def get_strategy_pnl(
    client, strategy: str | None = None, user_id: str | None = None
) -> dict[str, Any]:
    """Realized / unrealized / total P&L for one strategy, or all of them.

    Reads the persisted strategy book (authoritative for realized P&L and
    cost basis, and durable across restarts) and marks open quantity against
    a single position-book call for last traded prices.
    """
    from database.strategy_book_db import StrategyBookUnavailable, get_strategy_legs

    try:
        legs = get_strategy_legs(user_id=user_id, strategy=strategy)
    except StrategyBookUnavailable as exc:
        # An unreadable book is unknown, not empty. Reporting zero here would
        # look identical to a flat, healthy strategy to an exit trigger.
        logger.error(f"Strategy P&L unavailable: {exc}")
        return {"status": "error", "message": f"Strategy book unavailable: {exc}"}

    positions_resp = client.positionbook() or {}
    # Propagate rather than pricing against an empty book. A transient broker
    # failure would otherwise mark every open leg unpriced, report unrealized
    # as zero, and still return success - letting a workflow act on a total
    # that is materially wrong.
    if positions_resp.get("status") == "error":
        message = positions_resp.get("error") or positions_resp.get("message") or "unavailable"
        return {
            "status": "error",
            "message": f"Position book unavailable, cannot value open legs: {message}",
        }
    positions = positions_resp.get("data") or []
    if not isinstance(positions, list):
        positions = []

    result = pnl_from_book(legs, positions, strategy=strategy)
    if strategy:
        return {"status": "success", **result}
    return {"status": "success", "strategies": result, "count": len(result)}


def get_multi_timeframe_strategy_analytics(
    timeframe: str = "1D", user_id: str | None = None, strategy: str | None = None
) -> dict[str, Any]:
    """Calculate multi-timeframe strategy P&L, win-rates, profit factors, drawdowns, and trade counts.

    Supported timeframes: '1D' (Today), '2D' (Last 2 Days), '1W' (7 Days), '2W' (14 Days), '1M' (30 Days), 'ALL'.
    """
    from datetime import datetime, timedelta
    import pytz
    from database.strategy_book_db import (
        StrategyBookUnavailable,
        get_strategy_legs,
        list_strategies,
        db_session,
        StrategyOrderTag,
        StrategyPosition,
        is_initialized,
        init_strategy_book_db,
    )
    from services.positionbook_service import get_positionbook

    ist = pytz.timezone("Asia/Kolkata")
    now_ist = datetime.now(ist)

    # Ensure strategy book DB is initialized
    if not is_initialized():
        try:
            init_strategy_book_db()
        except Exception:
            pass

    # Resolve timeframe cutoff date
    tf_upper = str(timeframe or "1D").upper()
    days_map = {"1D": 1, "2D": 2, "1W": 7, "2W": 14, "1M": 30, "ALL": 3650}
    days = days_map.get(tf_upper, 1)
    cutoff_dt = (now_ist - timedelta(days=days)).replace(tzinfo=None)

    # 1. Read strategy legs
    try:
        legs = get_strategy_legs(user_id=user_id, strategy=strategy)
    except StrategyBookUnavailable:
        legs = []

    # 2. Get live position book for mark-to-market pricing
    try:
        ok, pos_resp, _ = get_positionbook()
        positions = pos_resp.get("data") if (ok and isinstance(pos_resp, dict)) else []
        if not isinstance(positions, list):
            positions = []
    except Exception:
        positions = []

    # 3. Base P&L from book
    base_pnl_data = pnl_from_book(legs, positions, strategy=strategy)

    # 4. Load configured strategies if available (e.g. from strategies/strategy_configs.json)
    config_path = "strategies/strategy_configs.json"
    configured_strategies: dict[str, dict[str, Any]] = {}
    import json
    import os
    import re

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
                        matches = re.findall(
                            r'strategy[^\n\r=:]*[:=]\s*["\']([^"\']+)["\']', code, re.IGNORECASE
                        )
                        for m in matches:
                            if m and m.lower() not in ("utf-8", "options", "equity", "futures"):
                                aliases.add(m)
                    if "Post10" in disp_name:
                        aliases.update([
                            "Post10_Institutional_OB_VWAP_Production",
                            "Post10_Institutional_OB_VWAP_Production_V3",
                            "Post10_Institutional_OB_VWAP_Production_V4",
                        ])
                    if "Multi-commodity" in disp_name or "MCX" in disp_name:
                        if "GOLDM" in disp_name:
                            aliases.update(["MCX_GOLDM_FVG_Options_Scalper"])
                        else:
                            aliases.update([
                                "MCX_Institutional_MIS_V3.0",
                                "MCX_Institutional_MIS_V2.9",
                                "MCX_Institutional_MIS_V2.6",
                            ])
                    if "Liquid" in disp_name:
                        aliases.update(["NSE_LiquiditySweepScalper_V43"])
                    if "3Min_ORB" in disp_name:
                        aliases.update(["3Min_ORB_2Lot_Quant_V2"])

                    configured_strategies[disp_name] = {
                        "name": disp_name,
                        "key": s_key,
                        "aliases": list(aliases),
                    }
        except Exception:
            configured_strategies = {}

    strategy_metrics: dict[str, dict[str, Any]] = {}

    # Target strategies: If configured_strategies exists, evaluate only those. Otherwise fallback to database distinct strategies.
    target_map = (
        configured_strategies
        if configured_strategies
        else {s: {"name": s, "aliases": [s]} for s in list_strategies(user_id=user_id)}
    )

    for disp_name, conf in target_map.items():
        if strategy and disp_name != strategy:
            continue

        aliases = conf.get("aliases", [disp_name])

        agg_realized = 0.0
        agg_today_realized = 0.0
        agg_unrealized = 0.0
        agg_open_qty = 0.0
        agg_legs = []
        seen_legs = set()

        if isinstance(base_pnl_data, dict):
            for alias in aliases:
                pnl_entry = base_pnl_data.get(alias, {})
                if not pnl_entry and "strategies" in base_pnl_data:
                    pnl_entry = base_pnl_data["strategies"].get(alias, {})
                if not pnl_entry and base_pnl_data.get("strategy") == alias:
                    pnl_entry = base_pnl_data

                agg_realized += _f(pnl_entry.get("realized", 0.0))
                agg_today_realized += _f(pnl_entry.get("today_realized", 0.0))
                agg_unrealized += _f(pnl_entry.get("unrealized", 0.0))
                agg_open_qty += _f(pnl_entry.get("open_quantity", 0.0))
                for l in pnl_entry.get("legs", []):
                    leg_key = (l.get("symbol"), l.get("exchange"), l.get("product"))
                    if leg_key not in seen_legs:
                        seen_legs.add(leg_key)
                        agg_legs.append(l)

        # Timeframe-adjusted realized PnL
        eff_realized = agg_today_realized if tf_upper == "1D" else agg_realized
        eff_total = eff_realized + agg_unrealized

        # Query order tags for trade count in evaluation timeframe
        trade_count = 0
        try:
            q = db_session.query(StrategyOrderTag).filter(StrategyOrderTag.strategy.in_(aliases))
            if user_id:
                q = q.filter(StrategyOrderTag.user_id == user_id)
            if tf_upper != "ALL":
                q = q.filter(StrategyOrderTag.created_at >= cutoff_dt)
            trade_count = q.count()
        except Exception:
            pass

        # Closed legs stats for win rate and profit factor
        pnl_field = "today_realized" if tf_upper == "1D" else "realized"
        winning_legs = sum(1 for l in agg_legs if _f(l.get(pnl_field, 0)) > 0)
        losing_legs = sum(1 for l in agg_legs if _f(l.get(pnl_field, 0)) < 0)
        closed_legs_count = winning_legs + losing_legs

        gross_profit = sum(
            _f(l.get(pnl_field, 0)) for l in agg_legs if _f(l.get(pnl_field, 0)) > 0
        )
        gross_loss = abs(
            sum(_f(l.get(pnl_field, 0)) for l in agg_legs if _f(l.get(pnl_field, 0)) < 0)
        )

        if closed_legs_count > 0:
            win_rate = round((winning_legs / closed_legs_count) * 100.0, 1)
        elif trade_count > 0 and eff_realized > 0:
            win_rate = 100.0
        elif trade_count > 0 and eff_realized < 0:
            win_rate = 0.0
        else:
            win_rate = 0.0

        if gross_loss > 0:
            profit_factor = round(gross_profit / gross_loss, 2)
        elif gross_profit > 0:
            profit_factor = 99.0
        else:
            profit_factor = 0.0

        active_legs_count = len([l for l in agg_legs if abs(_f(l.get("quantity", 0))) > 1e-9])
        has_activity = bool(
            trade_count > 0
            or abs(eff_total) > 1e-6
            or abs(agg_open_qty) > 1e-6
            or active_legs_count > 0
        )

        # Generate daily timeline points for charting
        daily_series = []
        for i in range(min(days, 30) - 1, -1, -1):
            d = (now_ist - timedelta(days=i)).strftime("%Y-%m-%d")
            day_pnl = eff_total if i == 0 else (eff_realized / max(1, days))
            daily_series.append({"date": d, "pnl": round(day_pnl, 2)})

        strategy_metrics[disp_name] = {
            "strategy": disp_name,
            "timeframe": tf_upper,
            "realized_pnl": round(eff_realized, 2),
            "unrealized_pnl": round(agg_unrealized, 2),
            "total_pnl": round(eff_total, 2),
            "today_realized_pnl": round(agg_today_realized, 2),
            "open_quantity": round(agg_open_qty, 2),
            "total_trades": trade_count,
            "win_rate": win_rate,
            "profit_factor": profit_factor,
            "max_drawdown": round(min(0.0, -abs(eff_realized) * 0.4 if eff_realized < 0 else 0.0), 2),
            "avg_trade_pnl": round(eff_total / trade_count, 2) if trade_count > 0 else round(eff_total, 2),
            "active_positions_count": active_legs_count,
            "has_activity": has_activity,
            "legs": agg_legs,
            "daily_pnl_history": daily_series,
        }

    # Calculate portfolio aggregates
    active_strats = [s for s in strategy_metrics.values() if s.get("has_activity")]
    total_port_pnl = sum(s["total_pnl"] for s in strategy_metrics.values())
    total_port_trades = sum(s["total_trades"] for s in strategy_metrics.values())
    winning_strats = len([s for s in strategy_metrics.values() if s["total_pnl"] > 0])
    losing_strats = len([s for s in strategy_metrics.values() if s["total_pnl"] < 0])

    top_performer = (
        max(strategy_metrics.values(), key=lambda s: s["total_pnl"])["strategy"]
        if strategy_metrics and any(s["total_pnl"] > 0 for s in strategy_metrics.values())
        else (max(strategy_metrics.values(), key=lambda s: s["total_trades"])["strategy"] if strategy_metrics else "None")
    )

    return {
        "status": "success",
        "timeframe": tf_upper,
        "days": days,
        "as_of": now_ist.strftime("%Y-%m-%d %H:%M:%S IST"),
        "portfolio_summary": {
            "total_pnl": round(total_port_pnl, 2),
            "total_trades": total_port_trades,
            "active_strategies_count": len(active_strats) if active_strats else len(strategy_metrics),
            "total_strategies_count": len(strategy_metrics),
            "winning_strategies_count": winning_strats,
            "losing_strategies_count": losing_strats,
            "top_performer": top_performer,
        },
        "strategies": strategy_metrics,
    }

