"""
blueprints/scanner.py
================================================================================
Flask Blueprint for Nifty 500 Scanner, 1-Click Execution & WhatsApp Inbound Webhook
================================================================================
"""

import threading
from datetime import datetime
import pytz
from flask import Blueprint, jsonify, render_template, request, session, current_app
from flask_cors import cross_origin

from utils.logging import get_logger
from utils.session import check_session_validity
from database.auth_db import get_api_key_for_tradingview
from services.nifty500_scanner_service import (
    Nifty500ScannerEngine,
    NIFTY_500_UNIVERSE,
    active_signals_registry
)

logger = get_logger(__name__)
scanner_bp = Blueprint("scanner_bp", __name__, url_prefix="/")

# In-memory cached state for instant frontend delivery
cached_scan_state = {
    "last_updated": None,
    "signals": [],
    "is_scanning": False
}


def _run_scanner_daemon():
    """Background worker that screens the universe concurrently and dispatches alerts."""
    cached_scan_state["is_scanning"] = True
    logger.info("[SCANNER] Initiating multi-threaded Nifty 500 scan...")

    try:
        api_key = get_api_key_for_tradingview("admin")
        found_signals = Nifty500ScannerEngine.scan_universe_concurrently(
            symbols=NIFTY_500_UNIVERSE,
            max_workers=8,
            api_key=api_key
        )

        for sig in found_signals:
            # Auto-dispatch WhatsApp alert
            try:
                msg = Nifty500ScannerEngine.format_whatsapp_alert(sig)
                Nifty500ScannerEngine.dispatch_whatsapp_broadcast(msg)
            except Exception as w_err:
                logger.debug(f"[SCANNER WHATSAPP DISPATCH] {w_err}")

        cached_scan_state["signals"] = found_signals
        cached_scan_state["last_updated"] = datetime.now(pytz.timezone("Asia/Kolkata")).strftime("%Y-%m-%d %H:%M:%S IST")
        logger.info(f"[SCANNER] Scan completed. {len(found_signals)} signals found.")
    except Exception as e:
        logger.exception(f"[SCANNER ERROR] {e}")
    finally:
        cached_scan_state["is_scanning"] = False


@scanner_bp.route("/scanner")
@check_session_validity
def scanner_dashboard():
    """Renders the main Nifty 500 scanner dashboard."""
    return render_template("nifty500_scanner.html")


@scanner_bp.route("/api/scanner/signals", methods=["GET"])
@cross_origin()
def get_signals():
    """Returns signals filtered by setup type."""
    filter_mode = request.args.get("filter", "ALL").upper()
    signals = cached_scan_state["signals"]

    if filter_mode == "INTRADAY":
        filtered = [s for s in signals if s["setup_type"] == "INTRADAY"]
    elif filter_mode == "SWING":
        filtered = [s for s in signals if s["setup_type"] == "SWING"]
    elif filter_mode == "OPTIONS":
        filtered = [s for s in signals if s.get("option_recommendation") is not None]
    else:
        filtered = signals

    return jsonify({
        "status": "success",
        "last_updated": cached_scan_state["last_updated"],
        "is_scanning": cached_scan_state["is_scanning"],
        "count": len(filtered),
        "signals": filtered
    })


@scanner_bp.route("/api/scanner/run", methods=["POST"])
@cross_origin()
def trigger_scan_manually():
    """Triggers an asynchronous multi-threaded universe scan."""
    if cached_scan_state["is_scanning"]:
        return jsonify({"status": "warning", "message": "Scan already running in background"}), 429

    worker = threading.Thread(target=_run_scanner_daemon, daemon=True)
    worker.start()
    return jsonify({"status": "success", "message": "Universe scan initiated"}), 202


@scanner_bp.route("/api/scanner/execute_1click", methods=["POST"])
@cross_origin()
def execute_1click_order():
    """Places an immediate 1-Click order through OpenAlgo's unified order service."""
    try:
        body = request.get_json() or {}
        symbol = body.get("symbol")
        exchange = body.get("exchange", "NFO")
        qty = int(body.get("quantity", 1))
        order_type = body.get("order_type", "BUY")
        price_type = body.get("price_type", "MARKET")
        product = body.get("product", "MIS")
        signal_data = body.get("signal_data", {})

        if not symbol:
            return jsonify({"status": "error", "message": "Symbol is required"}), 400

        api_key = get_api_key_for_tradingview("admin") or session.get("api_key", "")

        order_payload = {
            "apikey": api_key,
            "symbol": symbol,
            "exchange": exchange,
            "action": order_type,
            "quantity": qty,
            "pricetype": price_type,
            "product": product
        }

        from services.place_order_service import place_order
        success, order_res, status_code = place_order(order_payload)

        if not success:
            err_msg = order_res.get("message") if isinstance(order_res, dict) else str(order_res)
            return jsonify({"status": "error", "message": err_msg}), status_code or 400

        # Broadcast confirmation to WhatsApp
        if signal_data:
            try:
                msg = (
                    f"⚡ *1-CLICK ORDER EXECUTED VIA OPENALGO*\n\n"
                    f"📦 *Contract:* {symbol} ({exchange})\n"
                    f"🔢 *Quantity:* {qty} | *Product:* {product}\n"
                    f"💵 *Spot Trigger:* ₹{signal_data.get('spot_price')}\n"
                    f"🛑 *Stop Loss:* ₹{signal_data.get('sl')}\n"
                    f"🎯 *Target 1:* ₹{signal_data.get('tp1')}\n"
                    f"🎯 *Target 2:* ₹{signal_data.get('tp2')}\n"
                )
                Nifty500ScannerEngine.dispatch_whatsapp_broadcast(msg)
            except Exception as w_err:
                logger.debug(f"[EXECUTION WHATSAPP NOTICE] {w_err}")

        return jsonify({
            "status": "success",
            "message": f"Order executed for {symbol}",
            "broker_response": order_res
        }), 200

    except Exception as e:
        logger.exception(f"[1-CLICK EXECUTION ERROR] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500


@scanner_bp.route("/api/whatsapp/webhook", methods=["GET", "POST"])
@cross_origin()
def whatsapp_inbound_webhook():
    """
    Two-Way Inbound WhatsApp Webhook.
    Handles verification challenges (GET) and incoming message commands (POST).
    """
    # 1. Verification Handshake (Meta Cloud API / Webhook secret)
    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        if mode == "subscribe" and token == "openalgo_webhook_secret":
            return challenge, 200
        return "Forbidden", 403

    # 2. Inbound Command Processing
    try:
        data = request.get_json() or {}
        incoming_msg = ""
        sender_phone = ""

        # Meta WhatsApp Cloud API format parsing
        entry = data.get("entry", [])
        if entry:
            changes = entry[0].get("changes", [])
            if changes:
                val = changes[0].get("value", {})
                messages = val.get("messages", [])
                if messages:
                    incoming_msg = messages[0].get("text", {}).get("body", "")
                    sender_phone = messages[0].get("from", "")

        # Fallback to direct parameters (Twilio / Custom Gateway / Webhook payload)
        if not incoming_msg:
            incoming_msg = request.values.get("Body") or request.values.get("text") or data.get("message", "")
            sender_phone = request.values.get("From") or data.get("phone", "")

        if incoming_msg:
            logger.info(f"[WHATSAPP INBOUND] Command: '{incoming_msg}' from {sender_phone}")
            api_key = get_api_key_for_tradingview("admin")
            reply_text = Nifty500ScannerEngine.execute_inbound_whatsapp_command(incoming_msg, sender_phone, api_key=api_key)

            # Reply back using OpenAlgo's WhatsApp bot
            try:
                from services.whatsapp_bot_service import whatsapp_bot_service
                if whatsapp_bot_service.is_ready():
                    whatsapp_bot_service.send_sync(to=sender_phone, text=reply_text)
            except Exception as r_err:
                logger.debug(f"[WHATSAPP REPLY NOTICE]: {r_err}")

        return jsonify({"status": "received"}), 200

    except Exception as e:
        logger.exception(f"[WHATSAPP INBOUND ERROR] {e}")
        return jsonify({"status": "error", "message": str(e)}), 500
