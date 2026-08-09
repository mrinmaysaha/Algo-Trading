"""
Python Strategy Backtester API.
"""

import os
import json
from flask import jsonify, request, send_file, Response
from flask_restx import Namespace, Resource
from marshmallow import Schema, fields, validate

from backtesting.engine import run_python_strategy_backtest
from utils.logging import get_logger
from database.auth_db import get_auth_token_broker, verify_api_key

api = Namespace("python_strategy_backtest", description="Python Strategy Backtester API")
logger = get_logger(__name__)

class PythonStrategyBacktestSchema(Schema):
    strategy_id = fields.Str(required=True)
    symbols = fields.List(fields.Str(), required=True, validate=validate.Length(min=1))
    interval = fields.Str(load_default="15m")
    lookback_days = fields.Int(load_default=60)
    initial_capital = fields.Float(load_default=100000.0)
    source = fields.Str(load_default="db", validate=validate.OneOf(["db", "api"]))
    apikey = fields.Str(required=False, allow_none=True)

@api.route("/run", strict_slashes=False)
class PythonStrategyBacktesterResource(Resource):
    def post(self):
        """Run a backtest on a Python strategy."""
        try:
            req_json = request.get_json(silent=True) or (request.json if hasattr(request, "json") else {}) or {}
            schema = PythonStrategyBacktestSchema()
            errors = schema.validate(req_json)
            if errors:
                return {"status": "error", "message": "Validation failed", "errors": errors}, 400

            data = schema.load(req_json)
            strategy_id = str(data["strategy_id"]).strip()
            
            # Path to strategy script
            strategy_filename = f"{strategy_id}.py" if not strategy_id.endswith(".py") else strategy_id
            strategy_path = os.path.join("strategies", "scripts", strategy_filename)
            
            exchange = "NSE"
            config_file = os.path.join("strategies", "strategy_configs.json")
            if os.path.exists(config_file):
                try:
                    with open(config_file, "r") as f:
                        configs = json.load(f)
                        
                        lookup_id = strategy_id.replace(".py", "")
                        if lookup_id in configs:
                            cfg_entry = configs[lookup_id]
                            exchange = cfg_entry.get("exchange", "NSE")
                            if not os.path.exists(strategy_path):
                                if cfg_entry.get("file_path") and os.path.exists(cfg_entry["file_path"]):
                                    strategy_path = cfg_entry["file_path"]
                                elif cfg_entry.get("file_name") and os.path.exists(os.path.join("strategies", "scripts", cfg_entry["file_name"])):
                                    strategy_path = os.path.join("strategies", "scripts", cfg_entry["file_name"])
                except Exception as e:
                    logger.error(f"Error reading strategy_configs.json: {e}")

            if not os.path.exists(strategy_path):
                return {"status": "error", "message": f"Strategy '{strategy_id}' not found on server at {strategy_path}"}, 404

            source = data.get("source", "db")
            api_key = data.get("apikey", "")
            host_server = request.host_url.rstrip("/") if (request and hasattr(request, "host_url") and request.host_url) else (os.getenv("HOST_SERVER") or os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000"))
            
            auth_token = feed_token = broker = None
            if source == "api":
                if not api_key or verify_api_key(api_key) is None:
                    return {"status": "error", "message": "Invalid openalgo apikey"}, 403
                
                auth_token, feed_token, broker = get_auth_token_broker(api_key, include_feed_token=True)
                if auth_token is None:
                    return {"status": "error", "message": "No broker session for source='api'. Log in to your broker, or use source='db' to backtest from local history."}, 403

            result = run_python_strategy_backtest(
                strategy_path=strategy_path,
                symbols=data["symbols"],
                interval=data["interval"],
                lookback_days=data["lookback_days"],
                initial_capital=data["initial_capital"],
                api_key=api_key,
                host_server=host_server,
                exchange=exchange,
                source=source,
                auth_token=auth_token,
                feed_token=feed_token,
                broker=broker
            )
            return jsonify(result)
        except Exception as e:
            import traceback
            tb_str = traceback.format_exc()
            logger.exception("Python strategy backtest failed")
            return {"status": "error", "message": str(e), "traceback": tb_str}, 500

@api.route("/tearsheet/<path:filename>")
class PythonStrategyTearsheetResource(Resource):
    def get(self, filename):
        """Serve the OpenStatz HTML tearsheet."""
        file_path = os.path.join("backtesting", "runs", filename)
        if not os.path.exists(file_path):
            return {"status": "error", "message": "Tearsheet not found"}, 404
        return send_file(os.path.abspath(file_path), mimetype="text/html")
