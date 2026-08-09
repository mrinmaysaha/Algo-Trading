"""
Lightweight mock harness to quickly test OpenAlgo code paths 
(Broker payloads, Order parsing, Strategy signals) without loading full context or live APIs.
"""
import sys
import pandas as pd
import numpy as np


def mock_ohlcv_data(bars: int = 30) -> pd.DataFrame:
    """Generates realistic synthetic OHLCV data for strategy/indicator testing."""
    dates = pd.date_range("2026-01-01 09:15", periods=bars, freq="5min")
    np.random.seed(42)
    base_price = 43500.0
    close_prices = base_price + np.cumsum(np.random.normal(0, 15, bars))
    open_prices = np.roll(close_prices, 1)
    open_prices[0] = base_price

    high_prices = np.maximum(open_prices, close_prices) + np.abs(np.random.normal(5, 3, bars))
    low_prices = np.minimum(open_prices, close_prices) - np.abs(np.random.normal(5, 3, bars))
    volumes = np.random.randint(500, 3000, bars).astype(float)

    return pd.DataFrame({
        "datetime": dates,
        "open": open_prices,
        "high": high_prices,
        "low": low_prices,
        "close": close_prices,
        "volume": volumes
    }).set_index("datetime")


def mock_broker_order_response(symbol: str = "BANKNIFTY", quantity: int = 15) -> dict:
    """Returns a standard OpenAlgo order response structure for adapter testing."""
    return {
        "status": "success",
        "order_id": "20260809-001",
        "message": "Order placed successfully",
        "data": {"symbol": symbol, "quantity": quantity, "order_type": "MARKET"}
    }


def mock_strategy_signal(action: str = "ENTER", option_type: str = "CE") -> dict:
    """Returns a mock strategy signal payload for execution engine testing."""
    return {
        "action": action,
        "option_type": option_type,
        "symbol": "NIFTY",
        "timestamp": "2026-08-09T09:20:00"
    }


if __name__ == "__main__":
    print("Harness ready. Import functions into isolated tests to avoid full execution logs.")