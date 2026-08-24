import importlib
from typing import Any, Dict, List, Optional, Tuple, Union

from database.auth_db import get_auth_token_broker
from utils.logging import get_logger

# Initialize logger
logger = get_logger(__name__)


def format_decimal(value):
    """Format numeric value to 2 decimal places"""
    if isinstance(value, (int, float)):
        return round(float(value), 2)
    return value


def format_position_data(position_data):
    """Format all numeric values in position data to 2 decimal places, except quantity fields"""
    # Fields that should remain as integers
    quantity_fields = {
        "quantity",
        "qty",
        "netqty",
        "net_qty",
        "buyqty",
        "buy_quantity",
        "sellqty",
        "sell_quantity",
        "daybuyqty",
        "daysellqty",
    }
    # Fields that must preserve full float precision (must NOT be rounded to 2dp).
    # lot_size can be as small as 0.001 (BTCUSD.P) — rounding to 2dp gives 0.0.
    passthrough_fields = {"lot_size"}

    if isinstance(position_data, list):
        return [
            {
                key: value
                if key.lower() in passthrough_fields
                else (
                    (int(value) if value == int(value) else value)
                    if (key.lower() in quantity_fields and isinstance(value, (int, float)))
                    else (format_decimal(value) if isinstance(value, (int, float)) else value)
                )
                for key, value in item.items()
            }
            for item in position_data
        ]
    return position_data


def import_broker_module(broker_name: str) -> dict[str, Any] | None:
    """
    Dynamically import the broker-specific positionbook modules.

    Args:
        broker_name: Name of the broker

    Returns:
        Dictionary of broker functions or None if import fails
    """
    try:
        # Import API module
        api_module = importlib.import_module(f"broker.{broker_name}.api.order_api")
        # Import mapping module
        mapping_module = importlib.import_module(f"broker.{broker_name}.mapping.order_data")
        return {
            "get_positions": api_module.get_positions,
            "map_position_data": mapping_module.map_position_data,
            "transform_positions_data": mapping_module.transform_positions_data,
        }
    except (ImportError, AttributeError) as error:
        logger.error(f"Error importing broker modules: {error}")
        return None


def get_positionbook_with_auth(
    auth_token: str, broker: str, original_data: dict[str, Any] = None
) -> tuple[bool, dict[str, Any], int]:
    """
    Get position book details using provided auth token.

    Args:
        auth_token: Authentication token for the broker API
        broker: Name of the broker
        original_data: Original request data (for sandbox mode, optional for internal calls)

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    # If in analyze mode AND we have original_data (API call), route to sandbox
    # If original_data is None (internal call), use live broker
    from database.settings_db import get_analyze_mode

    if get_analyze_mode() and original_data:
        from services.sandbox_service import sandbox_get_positions

        api_key = original_data.get("apikey")
        if not api_key:
            return (
                False,
                {
                    "status": "error",
                    "message": "API key required for sandbox mode",
                    "mode": "analyze",
                },
                400,
            )

        success, response_data, status_code = sandbox_get_positions(api_key, original_data)
        if success and isinstance(response_data.get("data"), list):
            _enrich_positions_with_strategy_tags(response_data["data"], api_key=api_key)
        return success, response_data, status_code

    broker_funcs = import_broker_module(broker)
    if broker_funcs is None:
        return False, {"status": "error", "message": "Broker-specific module not found"}, 404

    try:
        # Get positions data using broker's implementation
        positions_data = broker_funcs["get_positions"](auth_token)

        if "status" in positions_data and positions_data["status"] == "error":
            return (
                False,
                {
                    "status": "error",
                    "message": positions_data.get("message", "Error fetching positions data"),
                },
                500,
            )

        # Transform data using mapping functions
        positions_data = broker_funcs["map_position_data"](positions_data)
        positions_data = broker_funcs["transform_positions_data"](positions_data)

        # Format numeric values to 2 decimal places
        formatted_positions = format_position_data(positions_data)

        # Enrich positions with strategy tag
        _enrich_positions_with_strategy_tags(formatted_positions)

        return True, {"status": "success", "data": formatted_positions}, 200
    except Exception as e:
        logger.exception(f"Error processing positions data: {e}")
        return False, {"status": "error", "message": str(e)}, 500


def _enrich_positions_with_strategy_tags(
    formatted_positions: list[dict[str, Any]], api_key: str | None = None
) -> None:
    """
    Enriches positions with their originating strategy name across both live and sandbox modes.
    """
    if not formatted_positions or not isinstance(formatted_positions, list):
        return

    try:
        import os
        import re
        from database.strategy_book_db import (
            StrategyOrderTag,
            StrategyPosition,
            db_session as strat_db_session,
            get_strategy_legs,
            init_strategy_book_db,
            is_initialized,
        )

        if not is_initialized():
            init_strategy_book_db()

        legs = get_strategy_legs()
        leg_map = {}
        if legs:
            for l in legs:
                strat = l.get("strategy")
                if not strat or strat in ("UI Exit Position", "AUTO_SQUARE_OFF"):
                    continue
                sym = l.get("symbol")
                exch = l.get("exchange")
                prod = l.get("product")
                qty = abs(float(l.get("quantity") or 0))

                if (sym, exch, prod) not in leg_map or qty > 0:
                    leg_map[(sym, exch, prod)] = strat
                if (sym, exch) not in leg_map or qty > 0:
                    leg_map[(sym, exch)] = strat
                if sym not in leg_map or qty > 0:
                    leg_map[sym] = strat

        user_id = None
        if api_key:
            try:
                from database.auth_db import verify_api_key

                user_id = verify_api_key(api_key)
            except Exception:
                user_id = None

        for pos in formatted_positions:
            if not isinstance(pos, dict):
                continue
            if pos.get("strategy"):
                continue

            sym = pos.get("symbol")
            exch = pos.get("exchange")
            prod = pos.get("product")

            matched_strat = (
                leg_map.get((sym, exch, prod)) or leg_map.get((sym, exch)) or leg_map.get(sym)
            )

            # Sandbox trades/orders lookup
            if not matched_strat:
                try:
                    from database.sandbox_db import SandboxOrders, SandboxTrades

                    query = SandboxTrades.query.filter(
                        SandboxTrades.symbol == sym,
                        SandboxTrades.strategy != "UI Exit Position",
                        SandboxTrades.strategy != "AUTO_SQUARE_OFF",
                        SandboxTrades.strategy.isnot(None),
                        SandboxTrades.strategy != "",
                    )
                    if user_id:
                        query = query.filter(SandboxTrades.user_id == user_id)
                    trade = query.order_by(SandboxTrades.id.desc()).first()
                    if trade and trade.strategy:
                        matched_strat = trade.strategy

                    if not matched_strat:
                        o_query = SandboxOrders.query.filter(
                            SandboxOrders.symbol == sym,
                            SandboxOrders.strategy != "UI Exit Position",
                            SandboxOrders.strategy != "AUTO_SQUARE_OFF",
                            SandboxOrders.strategy.isnot(None),
                            SandboxOrders.strategy != "",
                        )
                        if user_id:
                            o_query = o_query.filter(SandboxOrders.user_id == user_id)
                        order = o_query.order_by(SandboxOrders.id.desc()).first()
                        if order and order.strategy:
                            matched_strat = order.strategy
                except Exception:
                    pass

            # Fallback 1: StrategyOrderTag exact symbol
            if not matched_strat:
                tag = (
                    strat_db_session.query(StrategyOrderTag)
                    .filter(
                        StrategyOrderTag.symbol == sym,
                        StrategyOrderTag.strategy != "UI Exit Position",
                        StrategyOrderTag.strategy != "AUTO_SQUARE_OFF",
                        StrategyOrderTag.strategy.isnot(None),
                        StrategyOrderTag.strategy != "",
                    )
                    .order_by(StrategyOrderTag.id.desc())
                    .first()
                )
                if tag:
                    matched_strat = tag.strategy

            # Fallback 2: StrategyPosition exact symbol
            if not matched_strat:
                pos_row = (
                    strat_db_session.query(StrategyPosition)
                    .filter(
                        StrategyPosition.symbol == sym,
                        StrategyPosition.strategy != "UI Exit Position",
                        StrategyPosition.strategy != "AUTO_SQUARE_OFF",
                        StrategyPosition.strategy.isnot(None),
                        StrategyPosition.strategy != "",
                    )
                    .order_by(StrategyPosition.id.desc())
                    .first()
                )
                if pos_row:
                    matched_strat = pos_row.strategy

            # Fallback 3: Fuzzy option series match
            if not matched_strat and sym:
                m = re.match(r"^([A-Z]+)(\d{2}[A-Z]{3}\d{2})(\d+)(CE|PE)$", sym)
                if m:
                    und, exp, strike, opt_type = m.groups()
                    prefix = f"{und}{exp}"
                    try:
                        from database.sandbox_db import SandboxTrades

                        t_query = SandboxTrades.query.filter(
                            SandboxTrades.symbol.like(f"{prefix}%{opt_type}"),
                            SandboxTrades.strategy != "UI Exit Position",
                            SandboxTrades.strategy != "AUTO_SQUARE_OFF",
                            SandboxTrades.strategy.isnot(None),
                            SandboxTrades.strategy != "",
                        )
                        if user_id:
                            t_query = t_query.filter(SandboxTrades.user_id == user_id)
                        trade = t_query.order_by(SandboxTrades.id.desc()).first()
                        if trade and trade.strategy:
                            matched_strat = trade.strategy
                    except Exception:
                        pass

                    if not matched_strat:
                        tag = (
                            strat_db_session.query(StrategyOrderTag)
                            .filter(
                                StrategyOrderTag.symbol.like(f"{prefix}%{opt_type}"),
                                StrategyOrderTag.strategy != "UI Exit Position",
                                StrategyOrderTag.strategy != "AUTO_SQUARE_OFF",
                                StrategyOrderTag.strategy.isnot(None),
                                StrategyOrderTag.strategy != "",
                            )
                            .order_by(StrategyOrderTag.id.desc())
                            .first()
                        )
                        if tag:
                            matched_strat = tag.strategy

            if matched_strat:
                norm_strat = matched_strat
                config_path = "strategies/strategy_configs.json"
                if os.path.exists(config_path):
                    try:
                        import json

                        with open(config_path, "r", encoding="utf-8") as f:
                            cfgs = json.load(f)
                            for s_key, s_val in cfgs.items():
                                disp_name = s_val.get("name") or s_key
                                script_file = os.path.splitext(
                                    os.path.basename(s_val.get("file_path") or "")
                                )[0]
                                clean_norm = re.sub(r"[^a-zA-Z0-9]", "", norm_strat).lower()
                                clean_disp = re.sub(r"[^a-zA-Z0-9]", "", disp_name).lower()
                                clean_key = re.sub(r"[^a-zA-Z0-9]", "", s_key).lower()
                                clean_script = (
                                    re.sub(r"[^a-zA-Z0-9]", "", script_file).lower()
                                    if script_file
                                    else ""
                                )

                                if (
                                    clean_norm == clean_disp
                                    or clean_norm == clean_key
                                    or (
                                        clean_script
                                        and clean_norm.startswith(clean_script[:8])
                                    )
                                    or clean_norm.startswith(clean_key[:8])
                                    or clean_norm.startswith(clean_disp[:8])
                                ):
                                    norm_strat = disp_name
                                    break
                    except Exception:
                        pass
                pos["strategy"] = norm_strat
    except Exception as tag_err:
        logger.debug(f"Could not enrich positionbook with strategy tags: {tag_err}")
    except Exception as e:
        logger.exception(f"Error processing positions data: {e}")
        return False, {"status": "error", "message": str(e)}, 500


def get_positionbook(
    api_key: str | None = None, auth_token: str | None = None, broker: str | None = None
) -> tuple[bool, dict[str, Any], int]:
    """
    Get position book details.
    Supports both API-based authentication and direct internal calls.

    Args:
        api_key: OpenAlgo API key (for API-based calls)
        auth_token: Direct broker authentication token (for internal calls)
        broker: Direct broker name (for internal calls)

    Returns:
        Tuple containing:
        - Success status (bool)
        - Response data (dict)
        - HTTP status code (int)
    """
    # Case 1: API-based authentication
    if api_key and not (auth_token and broker):
        AUTH_TOKEN, broker_name = get_auth_token_broker(api_key)
        if AUTH_TOKEN is None:
            return False, {"status": "error", "message": "Invalid openalgo apikey"}, 403
        original_data = {"apikey": api_key}
        return get_positionbook_with_auth(AUTH_TOKEN, broker_name, original_data)

    # Case 2: Direct internal call with auth_token and broker
    elif auth_token and broker:
        return get_positionbook_with_auth(auth_token, broker, None)

    # Case 3: Invalid parameters
    else:
        return (
            False,
            {
                "status": "error",
                "message": "Either api_key or both auth_token and broker must be provided",
            },
            400,
        )
