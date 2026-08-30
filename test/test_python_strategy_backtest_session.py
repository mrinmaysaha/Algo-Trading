import pytest
from unittest.mock import patch, MagicMock
import pandas as pd
from flask import Flask
from blueprints.python_strategy import python_strategy_bp
from backtesting.engine import run_python_strategy_backtest

@pytest.fixture
def app_client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(python_strategy_bp, url_prefix="/python")
    return app.test_client()

def test_api_run_backtest_source_api_no_session(app_client):
    """Test that source='api' returns HTTP 403 when broker session is absent."""
    with patch("database.auth_db.verify_api_key", return_value="user1"), \
         patch("database.auth_db.get_auth_token_broker", return_value=(None, None, None)):
        resp = app_client.post("/python/api/run-backtest", json={
            "strategy_id": "3Min_ORB_Quant_20260801205330",
            "symbols": ["BANKNIFTY"],
            "interval": "3m",
            "lookback_days": 5,
            "initial_capital": 100000,
            "source": "api",
            "apikey": "valid_key"
        })
        assert resp.status_code == 403
        data = resp.get_json()
        assert data["status"] == "error"
        assert "No active broker session" in data["message"]

def test_run_python_strategy_backtest_3m_resampling():
    """Test yfinance 3m interval mapping and resampling in backtest engine."""
    with patch("services.history_service.get_history", return_value=(False, {}, None)), \
         patch("yfinance.download") as mock_yf:
        
        # Mock 1-minute DataFrame returned by yfinance
        times = pd.date_range("2026-08-03 09:15", "2026-08-03 09:30", freq="1min")
        raw_df = pd.DataFrame({
            "datetime": times,
            "open": range(100, 116),
            "high": range(105, 121),
            "low": range(95, 111),
            "close": range(102, 118),
            "volume": [1000] * 16
        })
        mock_yf.return_value = raw_df

        res = run_python_strategy_backtest(
            strategy_path="strategies/scripts/3Min_ORB_Quant_20260801205330.py",
            symbols=["BANKNIFTY"],
            interval="3m",
            lookback_days=1,
            initial_capital=100000,
            api_key="",
            host_server="http://127.0.0.1:5000",
            exchange="NSE",
            source="db"
        )
        assert res is not None
        mock_yf.assert_called_once()
        _, kwargs = mock_yf.call_args
        assert kwargs.get("interval") == "1m"
