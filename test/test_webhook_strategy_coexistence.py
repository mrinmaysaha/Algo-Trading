import json
import pytest
from flask import Flask
from unittest.mock import patch, MagicMock

from blueprints.strategy_module import strategy_module_bp
from blueprints.strategy import webhook_strategy_bp
from database.strategy_db import (
    create_strategy,
    get_strategy,
    get_strategy_by_webhook_id,
    add_symbol_mapping,
    delete_strategy,
)
from limiter import limiter

import pytz
from datetime import datetime
from blueprints.auth import auth_bp

@pytest.fixture
def app(monkeypatch):
    from database.strategy_db import init_db
    init_db()
    monkeypatch.setattr(limiter, "enabled", False)
    application = Flask(__name__)
    application.config.update(
        TESTING=True,
        SECRET_KEY="webhook-strategy-tests",
        PROPAGATE_EXCEPTIONS=True,
    )
    application.register_blueprint(auth_bp)
    application.register_blueprint(strategy_module_bp)
    application.register_blueprint(webhook_strategy_bp)
    return application

@pytest.fixture
def client(app):
    c = app.test_client()
    with c.session_transaction() as sess:
        sess["logged_in"] = True
        sess["user"] = "tester"
        sess["login_time"] = datetime.now(pytz.timezone("Asia/Kolkata")).isoformat()
    return c

def test_legacy_webhook_coexistence(client):
    # 1. Create a test legacy webhook strategy
    import uuid
    webhook_token = str(uuid.uuid4())
    strat = create_strategy(
        name="TradingView_Coexist_Test",
        webhook_id=webhook_token,
        user_id=1,
        is_intraday=False,
        trading_mode="LONG",
        start_time=None,
        end_time=None,
        squareoff_time=None,
        platform="tradingview",
    )
    assert strat is not None
    strat_id = strat.id

    # Add a symbol mapping
    mapping_id = add_symbol_mapping(
        strategy_id=strat_id,
        symbol="NIFTY",
        exchange="NFO",
        quantity=50,
        product_type="MIS"
    )
    assert mapping_id is not None

    try:
        # Mock order placement and API key lookup
        with patch("blueprints.strategy.queue_order") as mock_queue, \
             patch("blueprints.strategy.get_api_key_for_tradingview", return_value="MOCK_API_KEY"):
            mock_queue.return_value = {"status": "success", "order_id": "MOCK123"}

            # 2. Test hitting the legacy webhook via /strategy/webhook/<token> (Upstream route)
            resp = client.post(
                f"/strategy/webhook/{webhook_token}",
                data=json.dumps({
                    "action": "BUY",
                    "symbol": "NIFTY",
                    "price": 25000,
                }),
                content_type="application/json"
            )
            assert resp.status_code == 200
            data = resp.get_json()
            assert data is not None
            assert "Order queued successfully" in data.get("message", "")

            # 3. Test hitting /webhook-strategy/webhook/<token> directly
            resp2 = client.post(
                f"/webhook-strategy/webhook/{webhook_token}",
                data=json.dumps({
                    "action": "BUY",
                    "symbol": "NIFTY",
                    "price": 25000,
                }),
                content_type="application/json"
            )
            assert resp2.status_code == 200
            data2 = resp2.get_json()
            assert data2 is not None
            assert "Order queued successfully" in data2.get("message", "")

            # 4. Test analytics fallback routes
            with patch("blueprints.strategy_portfolio.get_strategy_analytics_api") as mock_analytics:
                from flask import Response
                mock_analytics.return_value = (Response('{"status":"success"}', mimetype="application/json"), 200)
                resp_ana1 = client.get("/strategy/api/analytics?timeframe=1D")
                assert resp_ana1.status_code == 200

                resp_ana2 = client.get("/webhook-strategy/api/analytics?timeframe=1D")
                assert resp_ana2.status_code == 200
    finally:
        # Clean up
        delete_strategy(strat_id)
        assert get_strategy(strat_id) is None
