"""
Python Strategy Backtester API.
"""

import os
from flask import jsonify, request, send_file
from flask_restx import Namespace, Resource
from marshmallow import Schema, fields, validate

from backtesting.engine import run_python_strategy_backtest
from utils.logging import get_logger

api = Namespace("python_strategy_backtest", description="Python Strategy Backtester API")
logger = get_logger(__name__)

class PythonStrategyBacktestSchema(Schema):
    strategy_id = fields.Str(required=True)
    symbols = fields.List(fields.Str(), required=True, validate=validate.Length(min=1))
    interval = fields.Str(load_default="15m")
    lookback_days = fields.Int(load_default=60)
    initial_capital = fields.Float(load_default=100000.0)

@api.route("")
class PythonStrategyBacktesterResource(Resource):
    def post(self):
        """Run a backtest on a Python strategy."""
        schema = PythonStrategyBacktestSchema()
        errors = schema.validate(request.json)
        if errors:
            return {"status": "error", "message": "Validation failed", "errors": errors}, 400

        data = schema.load(request.json)
        strategy_id = data["strategy_id"]
        
        # Path to strategy script
        strategy_path = os.path.join("strategies", "scripts", strategy_id)
        if not os.path.exists(strategy_path):
            return {"status": "error", "message": f"Strategy {strategy_id} not found"}, 404

        api_key = os.getenv("OPENALGO_API_KEY", "")
        host_server = os.getenv("HOST_SERVER") or os.getenv("OPENALGO_HOST", "http://127.0.0.1:5000")

        try:
            result = run_python_strategy_backtest(
                strategy_path=strategy_path,
                symbols=data["symbols"],
                interval=data["interval"],
                lookback_days=data["lookback_days"],
                initial_capital=data["initial_capital"],
                api_key=api_key,
                host_server=host_server
            )
            return result
        except Exception as e:
            logger.exception("Python strategy backtest failed")
            return {"status": "error", "message": str(e)}, 500

@api.route("/tearsheet/<path:filename>")
class PythonStrategyTearsheetResource(Resource):
    def get(self, filename):
        """Serve the OpenStatz HTML tearsheet."""
        file_path = os.path.join("backtesting", "runs", filename)
        if not os.path.exists(file_path):
            return {"status": "error", "message": "Tearsheet not found"}, 404
        return send_file(os.path.abspath(file_path), mimetype="text/html")
