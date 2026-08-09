# backtesting/config/asset_registry.py
"""
Indian Derivatives Asset Registry, Historical Lot Sizes, DTE Math & Config Validation.
Tuned specifically for the Indian Market (NSE, BSE, MCX) and IST timezone.
"""
import datetime
from typing import Dict, Tuple, List, Any


# Date-versioned lot size history for Indian F&O contracts (SEBI revisions)
LOT_SIZE_HISTORY: Dict[str, List[Tuple[str, str, int]]] = {
    "NIFTY": [
        ("2020-01-01", "2021-06-30", 75),
        ("2021-07-01", "2024-04-25", 50),
        ("2024-04-26", "2024-11-19", 25),
        ("2024-11-20", "2099-12-31", 75),
    ],
    "BANKNIFTY": [
        ("2020-01-01", "2023-07-02", 25),
        ("2023-07-03", "2024-11-19", 15),
        ("2024-11-20", "2099-12-31", 30),
    ],
    "FINNIFTY": [
        ("2021-01-01", "2024-11-19", 40),
        ("2024-11-20", "2099-12-31", 65),
    ],
    "MIDCPNIFTY": [
        ("2022-01-01", "2024-11-19", 75),
        ("2024-11-20", "2099-12-31", 120),
    ],
    "SENSEX": [
        ("2023-01-01", "2024-11-19", 10),
        ("2024-11-20", "2099-12-31", 20),
    ],
    "CRUDEOIL": [("2020-01-01", "2099-12-31", 100)],
    "NATURALGAS": [("2020-01-01", "2099-12-31", 1250)],
    "GOLD": [("2020-01-01", "2099-12-31", 100)],
    "SILVER": [("2020-01-01", "2099-12-31", 30)],
}

INDIAN_ASSET_SPECS = {
    "NIFTY": {"exchange": "NSE_INDEX", "strike_step": 50, "pricing_model": "BSM", "default_iv": 0.16},
    "BANKNIFTY": {"exchange": "NSE_INDEX", "strike_step": 100, "pricing_model": "BSM", "default_iv": 0.18},
    "FINNIFTY": {"exchange": "NSE_INDEX", "strike_step": 50, "pricing_model": "BSM", "default_iv": 0.17},
    "MIDCPNIFTY": {"exchange": "NSE_INDEX", "strike_step": 25, "pricing_model": "BSM", "default_iv": 0.18},
    "SENSEX": {"exchange": "BSE_INDEX", "strike_step": 100, "pricing_model": "BSM", "default_iv": 0.16},
    "CRUDEOIL": {"exchange": "MCX_COMMODITY", "strike_step": 50, "pricing_model": "BLACK76", "default_iv": 0.32},
    "NATURALGAS": {"exchange": "MCX_COMMODITY", "strike_step": 5, "pricing_model": "BLACK76", "default_iv": 0.45},
    "GOLD": {"exchange": "MCX_COMMODITY", "strike_step": 100, "pricing_model": "BLACK76", "default_iv": 0.14},
    "SILVER": {"exchange": "MCX_COMMODITY", "strike_step": 250, "pricing_model": "BLACK76", "default_iv": 0.22},
}


def get_historical_lot_size(symbol: str, date_str: str, config_override: int = None) -> int:
    """Returns exact date-versioned lot size for Indian derivatives."""
    if config_override and config_override > 0:
        return config_override

    sym_upper = symbol.upper()
    if sym_upper in LOT_SIZE_HISTORY:
        for start, end, lot in LOT_SIZE_HISTORY[sym_upper]:
            if start <= date_str <= end:
                return lot
        return LOT_SIZE_HISTORY[sym_upper][-1][2]
    return 25


def calculate_exact_dte(bar_timestamp: datetime.datetime, expiry_date: datetime.date) -> float:
    """Calculates exact fractional DTE down to 15:30 IST Indian market close."""
    if hasattr(bar_timestamp, "tzinfo") and bar_timestamp.tzinfo is not None:
        bar_timestamp = bar_timestamp.replace(tzinfo=None)
    if isinstance(expiry_date, datetime.datetime):
        expiry_date = expiry_date.date()
    expiry_dt = datetime.datetime.combine(expiry_date, datetime.time(15, 30))
    diff_seconds = (expiry_dt - bar_timestamp).total_seconds()
    return max(0.0001, diff_seconds / 86400.0)


def validate_strategy_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Validates configuration parameters to prevent runtime failures."""
    validated = config.copy()
    validated["sl_atr_mult"] = float(config.get("sl_atr_mult", 1.2))
    validated["tp_atr_mult"] = float(config.get("tp_atr_mult", 3.0))
    validated["max_total_trades_per_day"] = int(config.get("max_total_trades_per_day", 10))
    validated["max_trades_per_index"] = int(config.get("max_trades_per_index", 3))
    validated["max_daily_loss"] = float(config.get("max_daily_loss", 5000.0))
    return validated
