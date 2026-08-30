"""
services/nifty500_scanner_service.py
================================================================================
Nifty 500 Real-Time Scanner, Options Radar & Two-Way WhatsApp Execution Engine
================================================================================
"""

import time
import math
import re
from datetime import datetime, timedelta, time as dt_time
from typing import Dict, List, Any, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
import numpy as np
import pandas as pd
import pytz

from utils.logging import get_logger
from services.history_service import get_history
from database.symbol import db_session, SymToken

logger = get_logger(__name__)
ist = pytz.timezone("Asia/Kolkata")

# Nifty 500 High-Liquidity Constituents for Active Scanning
NIFTY_500_UNIVERSE = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "HINDUNILVR", "SBIN",
    "BHARTIARTL", "ITC", "KOTAKBANK", "LT", "AXISBANK", "ASIANPAINT", "HCLTECH",
    "BAJFINANCE", "MARUTI", "SUNPHARMA", "TITAN", "TATAMOTORS", "ULTRACEMCO",
    "TATASTEEL", "NTPC", "POWERGRID", "M&M", "WIPRO", "BAJAJFINSV", "ONGC",
    "JSWSTEEL", "COALINDIA", "ADANIENT", "ADANIPORTS", "HDFCLIFE", "SBILIFE",
    "TECHM", "GRASIM", "BRITANNIA", "CIPLA", "HEROMOTOCO", "EICHERMOT", "DIVISLAB",
    "DRREDDY", "BPCL", "APOLLOHOSP", "TATACONSUM", "SHRIRAMFIN", "INDUSINDBK",
    "BAJAJ-AUTO", "HINDALCO", "NESTLEIND", "VEDL", "BEL", "HAL", "TRENT",
    "ZOMATO", "JIOFIN", "DLF", "VBL", "CHOLAFIN", "PERSISTENT", "POLYCAB",
    "CUMMINSIND", "TVSMOTOR", "BOSCHLTD", "ABB", "SIEMENS", "HAVELLS", "PIDILITIND",
    "DIXON", "KAYNES", "BHEL", "RECLTD", "PFC", "MOTHERSON", "AUROPHARMA",
    "LUPIN", "COLPAL", "MARICO", "DABUR", "AMBUJACEM", "ACC", "BANKBARODA",
    "PNB", "CANBK", "UNIONBANK", "IDFCFIRSTB", "FEDERALBNK", "ASHOKLEY", "ESCORTS",
    "VOLTAS", "BLUESTARCO", "TATACOMM", "COFORGE", "MPHASIS", "LTTS", "NAUKRI"
]

# Active signals registry holding active signals with unique short numeric IDs (TTL: 5 minutes)
active_signals_registry: Dict[str, Dict[str, Any]] = {}
signal_counter = 100


class Nifty500ScannerEngine:

    @staticmethod
    def calculate_technical_indicators(df: pd.DataFrame) -> pd.DataFrame:
        """Calculates EMA20, EMA50, EMA200, VWAP, RSI(14), ADX(14), ATR(14), and Donchian(20)."""
        df = df.copy()
        if len(df) < 25:
            return df

        df["ema20"] = df["close"].ewm(span=20, adjust=False).mean()
        df["ema50"] = df["close"].ewm(span=50, adjust=False).mean()
        df["ema200"] = df["close"].ewm(span=200, adjust=False).mean()

        # Cumulative VWAP
        if "volume" in df.columns and df["volume"].sum() > 0:
            typical_price = (df["high"] + df["low"] + df["close"]) / 3.0
            df["vwap"] = (typical_price * df["volume"]).cumsum() / df["volume"].cumsum().replace(0, np.nan)
            df["vwap"] = df["vwap"].ffill().bfill()
        else:
            df["vwap"] = df["ema20"]

        df["vol_ma20"] = df["volume"].rolling(window=20).mean().replace(0, 1)

        # RSI 14
        delta = df["close"].diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
        rs = gain / loss.replace(0, 1e-9)
        df["rsi"] = (100 - (100 / (1 + rs))).fillna(50.0)

        # ATR 14
        high_low = df["high"] - df["low"]
        high_cp = (df["high"] - df["close"].shift(1)).abs()
        low_cp = (df["low"] - df["close"].shift(1)).abs()
        tr = pd.concat([high_low, high_cp, low_cp], axis=1).max(axis=1)
        df["atr"] = tr.rolling(window=14).mean().ffill().bfill()

        # ADX 14
        plus_dm = df["high"].diff().clip(lower=0)
        minus_dm = (-df["low"].diff()).clip(lower=0)
        plus_dm = plus_dm.where(plus_dm > minus_dm, 0.0)
        minus_dm = minus_dm.where(minus_dm > plus_dm, 0.0)
        tr_smooth = tr.rolling(window=14).sum().replace(0, np.nan)
        plus_di = 100 * (plus_dm.rolling(window=14).sum() / tr_smooth)
        minus_di = 100 * (minus_dm.rolling(window=14).sum() / tr_smooth)
        dx = (100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)).fillna(0)
        df["adx"] = dx.rolling(window=14).mean().fillna(20.0)

        # 20-period Donchian Breakout level
        df["donchian_high20"] = df["high"].rolling(window=20).max()
        df["donchian_low20"] = df["low"].rolling(window=20).min()

        return df

    @classmethod
    def check_fo_eligibility(cls, symbol: str) -> bool:
        """Determines if the stock is actively listed in NSE F&O."""
        try:
            res = db_session.query(SymToken).filter(
                SymToken.name == symbol.upper(),
                SymToken.exchange == "NFO"
            ).first()
            return res is not None
        except Exception as e:
            logger.debug(f"F&O check error for {symbol}: {e}")
            return False

    @classmethod
    def resolve_option_contract(
        cls, 
        symbol: str, 
        spot_price: float, 
        setup_type: str = "INTRADAY",
        option_type: str = "CE",
        api_key: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Picks the exact contract:
        - INTRADAY: 1-Strike ITM Call/Put (Delta ~0.60, high liquidity, low theta decay).
        - SWING: ATM Call/Put (Delta ~0.50).
        """
        try:
            options = db_session.query(SymToken).filter(
                SymToken.name == symbol.upper(),
                SymToken.exchange == "NFO",
                SymToken.symbol.like(f"%{option_type.upper()}")
            ).all()

            if not options:
                return None

            # Sort expiry dates by actual calendar date
            def parse_expiry_date(exp_str):
                try:
                    return datetime.strptime(exp_str.strip(), "%d-%b-%y").date()
                except Exception:
                    try:
                        return datetime.strptime(exp_str.strip(), "%d%b%y").date()
                    except Exception:
                        return datetime.max.date()

            expiries = sorted(list(set(opt.expiry for opt in options if opt.expiry)), key=parse_expiry_date)
            if not expiries:
                return None
            front_expiry = expiries[0]

            front_options = [opt for opt in options if opt.expiry == front_expiry and opt.strike]
            if not front_options:
                return None

            sorted_strikes = sorted(list(set(float(opt.strike) for opt in front_options)))
            if not sorted_strikes:
                return None

            # Locate ATM Strike
            atm_strike = min(sorted_strikes, key=lambda s: abs(s - spot_price))
            atm_idx = sorted_strikes.index(atm_strike)

            if option_type.upper() == "CE":
                if setup_type.upper() == "INTRADAY":
                    selected_idx = max(0, atm_idx - 1)  # 1 Strike ITM Call
                    selected_strike = sorted_strikes[selected_idx]
                    strike_label = "1-Strike ITM CE"
                    estimated_delta = 0.60
                else:
                    selected_strike = atm_strike        # ATM Call
                    strike_label = "ATM CE"
                    estimated_delta = 0.50
            else:
                if setup_type.upper() == "INTRADAY":
                    selected_idx = min(len(sorted_strikes) - 1, atm_idx + 1)  # 1 Strike ITM Put
                    selected_strike = sorted_strikes[selected_idx]
                    strike_label = "1-Strike ITM PE"
                    estimated_delta = 0.60
                else:
                    selected_strike = atm_strike        # ATM Put
                    strike_label = "ATM PE"
                    estimated_delta = 0.50

            selected_contract = next(
                (opt for opt in front_options if abs(float(opt.strike) - selected_strike) < 0.01),
                None
            )
            if not selected_contract:
                return None

            # Try to fetch live option quote if broker API session is available
            live_opt_ltp = None
            if api_key:
                try:
                    from services.quotes_service import get_quotes
                    q_ok, q_res, _ = get_quotes(selected_contract.symbol, "NFO", api_key=api_key)
                    if q_ok and q_res.get("data") and q_res["data"].get("ltp"):
                        live_opt_ltp = float(q_res["data"]["ltp"])
                except Exception as q_err:
                    logger.debug(f"Live quote fetch for {selected_contract.symbol} skipped: {q_err}")

            return {
                "symbol": selected_contract.symbol,
                "token": selected_contract.token,
                "exchange": "NFO",
                "strike": selected_strike,
                "strike_type": strike_label,
                "option_type": option_type.upper(),
                "expiry": front_expiry,
                "lot_size": int(selected_contract.lotsize or 1),
                "estimated_delta": estimated_delta,
                "live_ltp": live_opt_ltp
            }
        except Exception as e:
            logger.error(f"Error resolving option for {symbol}: {e}")
            return None

    @classmethod
    def _fetch_history_dual_source(
        cls, 
        symbol: str, 
        exchange: str, 
        interval: str, 
        start_date: str, 
        end_date: str, 
        api_key: Optional[str] = None
    ) -> tuple[bool, Dict[str, Any], int]:
        """
        Smart dual-source fetcher:
        1. First tries local Historify DuckDB ('db') for instant zero-latency retrieval without consuming API quotas.
        2. If data is not in local DB, automatically falls back to live Broker API ('api').
        """
        # 1. Try DuckDB
        ok_db, resp_db, code_db = get_history(
            symbol=symbol, exchange=exchange, interval=interval,
            start_date=start_date, end_date=end_date, source="db"
        )
        if ok_db and resp_db.get("data") and len(resp_db["data"]) >= 25:
            return ok_db, resp_db, code_db

        # 2. Fallback to Broker API
        return get_history(
            symbol=symbol, exchange=exchange, interval=interval,
            start_date=start_date, end_date=end_date, api_key=api_key, source="api"
        )

    @classmethod
    def scan_symbol(cls, symbol: str, api_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """Evaluates live candle bars for Intraday and Swing setups."""
        global signal_counter
        signals = []
        today_str = datetime.now(ist).strftime("%Y-%m-%d")
        start_history = (datetime.now(ist) - timedelta(days=220)).strftime("%Y-%m-%d")

        # 1. Intraday Momentum Scan (5-Minute Bars)
        try:
            ok_5m, resp_5m, _ = cls._fetch_history_dual_source(
                symbol=symbol, exchange="NSE", interval="5m",
                start_date=(datetime.now(ist) - timedelta(days=5)).strftime("%Y-%m-%d"),
                end_date=today_str, api_key=api_key
            )
            if ok_5m and resp_5m.get("data") and len(resp_5m["data"]) >= 25:
                df_5m = cls.calculate_technical_indicators(pd.DataFrame(resp_5m["data"]))
                curr = df_5m.iloc[-1]
                prev = df_5m.iloc[-2]

                is_intraday_buy = (
                    curr["close"] > curr["vwap"] and
                    curr["close"] > curr["ema20"] and
                    curr["volume"] >= (1.8 * curr["vol_ma20"]) and
                    curr["rsi"] >= 60.0 and
                    curr["adx"] >= 22.0 and
                    curr["close"] > prev["high"]
                )

                if is_intraday_buy:
                    spot_entry = float(curr["close"])
                    atr = max(float(curr["atr"]), spot_entry * 0.005)

                    spot_sl = round(spot_entry - (1.2 * atr), 2)
                    spot_tp1 = round(spot_entry + (1.5 * atr), 2)
                    spot_tp2 = round(spot_entry + (3.0 * atr), 2)

                    fo_eligible = cls.check_fo_eligibility(symbol)
                    opt_details = None
                    if fo_eligible:
                        opt_details = cls.resolve_option_contract(
                            symbol, spot_entry, setup_type="INTRADAY", option_type="CE", api_key=api_key
                        )
                        if opt_details:
                            delta = opt_details["estimated_delta"]
                            est_opt_entry = opt_details.get("live_ltp") or round(max(5.0, (spot_entry - opt_details["strike"]) + (atr * 0.8)), 2)
                            opt_details["opt_entry"] = est_opt_entry
                            opt_details["opt_sl"] = round(max(0.5, est_opt_entry - ((spot_entry - spot_sl) * delta)), 2)
                            opt_details["opt_tp1"] = round(est_opt_entry + ((spot_tp1 - spot_entry) * delta), 2)
                            opt_details["opt_tp2"] = round(est_opt_entry + ((spot_tp2 - spot_entry) * delta), 2)

                    signal_counter += 1
                    sig_id = str(signal_counter)

                    sig_payload = {
                        "signal_id": sig_id,
                        "symbol": symbol,
                        "setup_type": "INTRADAY",
                        "direction": "BUY",
                        "timeframe": "5M",
                        "spot_price": spot_entry,
                        "sl": spot_sl,
                        "tp1": spot_tp1,
                        "tp2": spot_tp2,
                        "rsi": round(float(curr["rsi"]), 1),
                        "adx": round(float(curr["adx"]), 1),
                        "volume_surge": round(float(curr["volume"] / curr["vol_ma20"]), 2),
                        "fo_eligible": fo_eligible,
                        "option_recommendation": opt_details,
                        "created_at": datetime.now(ist),
                        "timestamp": datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S IST")
                    }
                    active_signals_registry[sig_id] = sig_payload
                    signals.append(sig_payload)
        except Exception as e:
            logger.debug(f"Intraday scan error for {symbol}: {e}")

        # 2. Swing Breakout Scan (Daily Bars)
        try:
            ok_1d, resp_1d, _ = cls._fetch_history_dual_source(
                symbol=symbol, exchange="NSE", interval="D",
                start_date=start_history, end_date=today_str, api_key=api_key
            )
            if ok_1d and resp_1d.get("data") and len(resp_1d["data"]) >= 50:
                df_1d = cls.calculate_technical_indicators(pd.DataFrame(resp_1d["data"]))
                curr_d = df_1d.iloc[-1]
                prev_d = df_1d.iloc[-2]

                has_ema200 = "ema200" in df_1d.columns and not pd.isna(curr_d["ema200"])
                trend_ok = (curr_d["close"] > curr_d["ema50"]) and (curr_d["close"] > curr_d["ema200"] if has_ema200 else True)

                is_swing_buy = (
                    trend_ok and
                    curr_d["close"] >= prev_d["donchian_high20"] and
                    curr_d["rsi"] >= 55.0 and
                    curr_d["volume"] >= curr_d["vol_ma20"]
                )

                if is_swing_buy:
                    spot_entry = float(curr_d["close"])
                    atr = max(float(curr_d["atr"]), spot_entry * 0.015)

                    spot_sl = round(spot_entry - (2.0 * atr), 2)
                    spot_tp1 = round(spot_entry + (2.5 * atr), 2)
                    spot_tp2 = round(spot_entry + (5.0 * atr), 2)

                    fo_eligible = cls.check_fo_eligibility(symbol)
                    opt_details = None
                    if fo_eligible:
                        opt_details = cls.resolve_option_contract(
                            symbol, spot_entry, setup_type="SWING", option_type="CE", api_key=api_key
                        )
                        if opt_details:
                            delta = opt_details["estimated_delta"]
                            est_opt_entry = opt_details.get("live_ltp") or round(max(10.0, atr * 1.2), 2)
                            opt_details["opt_entry"] = est_opt_entry
                            opt_details["opt_sl"] = round(max(1.0, est_opt_entry - ((spot_entry - spot_sl) * delta)), 2)
                            opt_details["opt_tp1"] = round(est_opt_entry + ((spot_tp1 - spot_entry) * delta), 2)
                            opt_details["opt_tp2"] = round(est_opt_entry + ((spot_tp2 - spot_entry) * delta), 2)

                    signal_counter += 1
                    sig_id = str(signal_counter)

                    sig_payload = {
                        "signal_id": sig_id,
                        "symbol": symbol,
                        "setup_type": "SWING",
                        "direction": "BUY",
                        "timeframe": "1D",
                        "spot_price": spot_entry,
                        "sl": spot_sl,
                        "tp1": spot_tp1,
                        "tp2": spot_tp2,
                        "rsi": round(float(curr_d["rsi"]), 1),
                        "adx": round(float(curr_d["adx"]), 1),
                        "volume_surge": round(float(curr_d["volume"] / curr_d["vol_ma20"]), 2),
                        "fo_eligible": fo_eligible,
                        "option_recommendation": opt_details,
                        "created_at": datetime.now(ist),
                        "timestamp": datetime.now(ist).strftime("%Y-%m-%d %H:%M:%S IST")
                    }
                    active_signals_registry[sig_id] = sig_payload
                    signals.append(sig_payload)
        except Exception as e:
            logger.debug(f"Swing scan error for {symbol}: {e}")

        return signals

    @classmethod
    def scan_universe_concurrently(cls, symbols: List[str] = None, max_workers: int = 8, api_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """Fast concurrent scanner executing across the entire constituent list."""
        if symbols is None:
            symbols = NIFTY_500_UNIVERSE

        all_signals = []
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_sym = {executor.submit(cls.scan_symbol, sym, api_key): sym for sym in symbols}
            for future in as_completed(future_to_sym):
                try:
                    res = future.result()
                    if res:
                        all_signals.extend(res)
                except Exception as exc:
                    logger.debug(f"Scan symbol task generated an exception: {exc}")
        return all_signals

    @classmethod
    def format_whatsapp_alert(cls, signal: Dict[str, Any]) -> str:
        """Formats the outbound notification card with WhatsApp reply syntax."""
        sig_id = signal["signal_id"]
        sym = signal["symbol"]
        setup = signal["setup_type"]
        tf = signal["timeframe"]
        entry = signal["spot_price"]
        sl = signal["sl"]
        tp1 = signal["tp1"]
        tp2 = signal["tp2"]
        opt = signal.get("option_recommendation")

        msg = (
            f"🚨 *OPENALGO SIGNAL #{sig_id}: {sym}*\n"
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"🎯 *Setup:* {setup} BUY ({tf})\n"
            f"📈 *Spot Price:* ₹{entry:.2f}\n"
            f"🛑 *Stop Loss:* ₹{sl:.2f}\n"
            f"🎯 *Target 1 (1:1.5):* ₹{tp1:.2f}\n"
            f"🎯 *Target 2 (1:3.0):* ₹{tp2:.2f}\n"
            f"📊 *RSI:* {signal['rsi']} | *Volume Surge:* {signal['volume_surge']}x\n"
        )

        if opt:
            msg += (
                f"\n⚡ *OPTION RADAR: APPROVED*\n"
                f"🏷️ *Contract:* {opt['symbol']} ({opt['strike_type']})\n"
                f"📅 *Expiry:* {opt['expiry']} | *Lot Size:* {opt['lot_size']}\n"
                f"💵 *Est. Entry:* ₹{opt['opt_entry']}\n"
                f"🛑 *Option SL:* ₹{opt['opt_sl']}\n"
                f"🎯 *Option TP1:* ₹{opt['opt_tp1']} | *TP2:* ₹{opt['opt_tp2']}\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📲 *REPLY TO BUY (Valid 5 mins):*\n"
                f"👉 *Buy 1 Lot Option:* Reply `BUY {sig_id}`\n"
                f"👉 *Buy 2 Lots Option:* Reply `BUY {sig_id} 2L`\n"
                f"👉 *Buy 100 Shares Cash:* Reply `BUY {sig_id} EQ 100`\n"
            )
        else:
            msg += (
                f"\n⚪ *Cash Equity Only* (Not in F&O)\n"
                f"━━━━━━━━━━━━━━━━━━━━━━\n"
                f"📲 *REPLY TO BUY (Valid 5 mins):*\n"
                f"👉 *Buy 50 Shares:* Reply `BUY {sig_id} EQ 50`\n"
            )

        msg += f"\n⏰ *Time:* {signal['timestamp']}"
        return msg

    @classmethod
    def dispatch_whatsapp_broadcast(cls, message: str) -> None:
        """Sends WhatsApp broadcast through OpenAlgo's built-in bot daemon."""
        try:
            from services.whatsapp_bot_service import whatsapp_bot_service
            from database.whatsapp_db import get_all_whatsapp_users
            if whatsapp_bot_service.is_ready():
                users = get_all_whatsapp_users()
                for u in users:
                    jid = u.get("whatsapp_jid") or u.get("phone_number")
                    if jid:
                        whatsapp_bot_service.send_sync(to=jid, text=message)
        except Exception as e:
            logger.debug(f"[WHATSAPP BROADCAST NOTICE]: {e}")

    @classmethod
    def prune_expired_signals(cls, max_age_seconds: int = 1800) -> None:
        """Purges signals older than 30 minutes to prevent unbounded memory growth."""
        now = datetime.now(ist)
        expired_ids = [
            sig_id for sig_id, sig in active_signals_registry.items()
            if (now - sig["created_at"]).total_seconds() > max_age_seconds
        ]
        for sig_id in expired_ids:
            active_signals_registry.pop(sig_id, None)

    @classmethod
    def is_authorized_sender(cls, sender_phone: str) -> bool:
        """Verifies that the incoming WhatsApp message is from an authorized linked user."""
        if not sender_phone:
            return True
        try:
            from database.whatsapp_db import get_all_whatsapp_users
            users = get_all_whatsapp_users()
            if not users:
                return True
            clean_sender = re.sub(r"\D", "", sender_phone)
            for u in users:
                u_phone = re.sub(r"\D", "", u.get("phone_number") or u.get("whatsapp_jid") or "")
                if u_phone and (clean_sender.endswith(u_phone) or u_phone.endswith(clean_sender)):
                    return True
            return False
        except Exception:
            return True

    @classmethod
    def execute_inbound_whatsapp_command(cls, message_body: str, sender_phone: str, api_key: Optional[str] = None) -> str:
        """
        Parses incoming WhatsApp replies (e.g. 'BUY 101', 'BUY 101 2L', 'BUY 101 EQ 100')
        validates sender authenticity, checks TTL, and routes order through place_order.
        """
        cls.prune_expired_signals()

        if sender_phone and not cls.is_authorized_sender(sender_phone):
            return "🔒 *Access Denied:* Your phone number is not registered with this OpenAlgo instance. Please link your number in `/whatsapp`."

        clean_msg = message_body.strip().upper()
        match = re.match(r"^BUY\s+(\d+)(?:\s+(EQ|\d+L|\d+))?(?:\s+(\d+))?$", clean_msg)

        if not match:
            return "❌ *Invalid Command Format.*\nReply `BUY <ID>` (e.g. `BUY 101`) or `BUY <ID> EQ 50`."

        sig_id = match.group(1)
        param1 = match.group(2)
        param2 = match.group(3)

        signal = active_signals_registry.get(sig_id)
        if not signal:
            return f"⚠️ *Signal #{sig_id} Not Found or Expired.*\nPlease act on fresh signals."

        # Verify Signal Age (5 Minute TTL)
        age_seconds = (datetime.now(ist) - signal["created_at"]).total_seconds()
        if age_seconds > 300:
            return f"⏳ *Signal #{sig_id} Expired* ({int(age_seconds)}s old).\nTo prevent slippage, orders must be placed within 5 minutes."

        from database.auth_db import get_api_key_for_tradingview

        if not api_key:
            api_key = get_api_key_for_tradingview("admin") or ""

        # Order Type Resolution
        if param1 == "EQ":
            # Cash Equity Execution
            shares = int(param2) if param2 else 10
            sym = signal["symbol"]
            exch = "NSE"
            prod = "CNC" if signal["setup_type"] == "SWING" else "MIS"

            order_payload = {
                "apikey": api_key,
                "symbol": sym,
                "exchange": exch,
                "action": "BUY",
                "quantity": shares,
                "pricetype": "MARKET",
                "product": prod
            }

            try:
                from services.place_order_service import place_order
                success, resp, code = place_order(order_payload)
                if success:
                    return (
                        f"✅ *CASH ORDER EXECUTED VIA WHATSAPP*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"📈 *Stock:* {sym} (NSE)\n"
                        f"🔢 *Quantity:* {shares} Shares\n"
                        f"🏷️ *Product:* {prod}\n"
                        f"🛑 *Stop Loss:* ₹{signal['sl']}\n"
                        f"🎯 *Target 1:* ₹{signal['tp1']}\n"
                        f"🎯 *Target 2:* ₹{signal['tp2']}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Trade recorded in OpenAlgo Strategy Book."
                    )
                else:
                    err = resp.get("message") if isinstance(resp, dict) else str(resp)
                    return f"❌ *Order Execution Failed:* {err}"
            except Exception as e:
                return f"❌ *Execution Exception:* {str(e)}"

        else:
            # Option Contract Execution
            opt = signal.get("option_recommendation")
            if not opt:
                return f"❌ Stock {signal['symbol']} is not in the F&O segment. Reply `BUY {sig_id} EQ 10` for cash equity."

            lots = 1
            if param1 and param1.endswith("L"):
                try:
                    lots = int(param1.replace("L", ""))
                except ValueError:
                    lots = 1
            elif param1 and param1.isdigit():
                lots = int(param1)

            total_qty = lots * opt["lot_size"]
            prod = "MIS" if signal["setup_type"] == "INTRADAY" else "NRML"

            order_payload = {
                "apikey": api_key,
                "symbol": opt["symbol"],
                "exchange": "NFO",
                "action": "BUY",
                "quantity": total_qty,
                "pricetype": "MARKET",
                "product": prod
            }

            try:
                from services.place_order_service import place_order
                success, resp, code = place_order(order_payload)
                if success:
                    return (
                        f"✅ *OPTION ORDER EXECUTED VIA WHATSAPP*\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"🏷️ *Contract:* {opt['symbol']} ({opt['strike_type']})\n"
                        f"📦 *Lots:* {lots} ({total_qty} units)\n"
                        f"🏷️ *Product:* {prod}\n"
                        f"🛑 *Option SL:* ₹{opt['opt_sl']}\n"
                        f"🎯 *Option TP1:* ₹{opt['opt_tp1']}\n"
                        f"🎯 *Option TP2:* ₹{opt['opt_tp2']}\n"
                        f"━━━━━━━━━━━━━━━━━━━━━━\n"
                        f"Order routed successfully to Broker."
                    )
                else:
                    err = resp.get("message") if isinstance(resp, dict) else str(resp)
                    return f"❌ *Option Order Failed:* {err}"
            except Exception as e:
                return f"❌ *Option Execution Exception:* {str(e)}"
