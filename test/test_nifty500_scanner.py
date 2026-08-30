import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from flask import Flask

from blueprints.scanner import scanner_bp, cached_scan_state
from services.nifty500_scanner_service import (
    Nifty500ScannerEngine,
    active_signals_registry,
    ist
)


@pytest.fixture
def client():
    app = Flask(__name__)
    app.config["TESTING"] = True
    app.register_blueprint(scanner_bp)
    return app.test_client()


def test_technical_indicators_calculation():
    """Verify VWAP, EMA, RSI, ADX, ATR, and Donchian indicator formulas."""
    dates = pd.date_range("2026-08-01 09:15", periods=35, freq="5min")
    data = {
        "datetime": dates,
        "open": np.linspace(100, 134, 35),
        "high": np.linspace(102, 136, 35),
        "low": np.linspace(98, 132, 35),
        "close": np.linspace(101, 135, 35),
        "volume": [1000] * 35
    }
    df = pd.DataFrame(data)
    df_calc = Nifty500ScannerEngine.calculate_technical_indicators(df)

    assert "ema20" in df_calc.columns
    assert "ema50" in df_calc.columns
    assert "vwap" in df_calc.columns
    assert "rsi" in df_calc.columns
    assert "adx" in df_calc.columns
    assert "atr" in df_calc.columns
    assert "donchian_high20" in df_calc.columns

    assert float(df_calc["rsi"].iloc[-1]) > 50.0
    assert float(df_calc["vwap"].iloc[-1]) > 0.0


def test_resolve_option_contract():
    """Test resolution of 1-Strike ITM for Intraday and ATM for Swing."""
    mock_opt1 = MagicMock(symbol="TATASTEEL28AUG26180CE", token="101", strike=180.0, expiry="28-AUG-26", lotsize=5500)
    mock_opt2 = MagicMock(symbol="TATASTEEL28AUG26185CE", token="102", strike=185.0, expiry="28-AUG-26", lotsize=5500)
    mock_opt3 = MagicMock(symbol="TATASTEEL28AUG26190CE", token="103", strike=190.0, expiry="28-AUG-26", lotsize=5500)

    with patch("database.symbol.db_session.query") as mock_query:
        mock_query.return_value.filter.return_value.all.return_value = [mock_opt1, mock_opt2, mock_opt3]

        # Spot at 186.0 -> ATM is 185, 1-Strike ITM is 180
        opt_intra = Nifty500ScannerEngine.resolve_option_contract("TATASTEEL", 186.0, setup_type="INTRADAY", option_type="CE")
        assert opt_intra is not None
        assert opt_intra["strike"] == 180.0
        assert opt_intra["strike_type"] == "1-Strike ITM CE"

        opt_swing = Nifty500ScannerEngine.resolve_option_contract("TATASTEEL", 186.0, setup_type="SWING", option_type="CE")
        assert opt_swing is not None
        assert opt_swing["strike"] == 185.0
        assert opt_swing["strike_type"] == "ATM CE"


def test_whatsapp_alert_format_and_inbound_execution():
    """Test WhatsApp formatting and 2-way command execution loop."""
    signal_id = "999"
    active_signals_registry[signal_id] = {
        "signal_id": signal_id,
        "symbol": "RELIANCE",
        "setup_type": "INTRADAY",
        "direction": "BUY",
        "timeframe": "5M",
        "spot_price": 3000.0,
        "sl": 2980.0,
        "tp1": 3030.0,
        "tp2": 3060.0,
        "rsi": 65.0,
        "adx": 25.0,
        "volume_surge": 2.5,
        "fo_eligible": True,
        "option_recommendation": {
            "symbol": "RELIANCE28AUG262980CE",
            "token": "9991",
            "exchange": "NFO",
            "strike": 2980.0,
            "strike_type": "1-Strike ITM CE",
            "expiry": "28-AUG-26",
            "lot_size": 250,
            "estimated_delta": 0.60,
            "opt_entry": 45.0,
            "opt_sl": 33.0,
            "opt_tp1": 63.0,
            "opt_tp2": 81.0
        },
        "created_at": datetime.now(ist),
        "timestamp": "2026-08-30 10:00:00 IST"
    }

    # Format check
    card = Nifty500ScannerEngine.format_whatsapp_alert(active_signals_registry[signal_id])
    assert f"SIGNAL #{signal_id}" in card
    assert "RELIANCE" in card
    assert f"BUY {signal_id}" in card

    # Inbound Command execution mock
    mock_place_order = MagicMock(return_value=(True, {"orderid": "ORD123"}, 200))
    with patch.dict("sys.modules", {"services.place_order_service": MagicMock(place_order=mock_place_order)}):
        res_text = Nifty500ScannerEngine.execute_inbound_whatsapp_command("BUY 999 2L", "+919876543210")
        assert "OPTION ORDER EXECUTED VIA WHATSAPP" in res_text
        assert "RELIANCE28AUG262980CE" in res_text


def test_scanner_api_endpoints(client):
    """Test Flask Blueprint routes for scanner."""
    # Test GET signals
    resp = client.get("/api/scanner/signals?filter=ALL")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["status"] == "success"
    assert "signals" in data

    # Test Webhook Verification
    resp_webhook_verify = client.get("/api/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=openalgo_webhook_secret&hub.challenge=test_challenge")
    assert resp_webhook_verify.status_code == 200
    assert resp_webhook_verify.data.decode("utf-8") == "test_challenge"

    # Test 1-Click Order
    mock_place_order = MagicMock(return_value=(True, {"orderid": "ORD777"}, 200))
    with patch.dict("sys.modules", {"services.place_order_service": MagicMock(place_order=mock_place_order)}):
        resp_order = client.post("/api/scanner/execute_1click", json={
            "symbol": "TATASTEEL",
            "exchange": "NSE",
            "quantity": 50,
            "order_type": "BUY",
            "product": "MIS"
        })
        assert resp_order.status_code == 200
        data_order = resp_order.get_json()
        assert data_order["status"] == "success"
