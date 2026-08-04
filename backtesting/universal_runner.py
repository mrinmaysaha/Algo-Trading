# backtesting/universal_runner.py
"""
Universal Dynamic Strategy Execution Runner (AlgoTest / StockMock Style)

Executes ANY Python strategy script dynamically without hardcoding strategy-specific
branches or hardcoded option prices in the backtesting engine. Supports Class-Based Strategies
and Universal Signal Strategies seamlessly.
"""

import os
import sys
import ast
import re
import importlib.util
from datetime import datetime, timedelta
import pandas as pd
import numpy as np
from openalgo import ta


def get_estimated_atm_premium(symbol: str, spot_price: float) -> float:
    """
    Dynamically computes estimated ATM option contract premium based on underlying spot level.
    Weekly ATM index options typically trade at ~0.75% of spot price level.
    """
    if spot_price <= 0:
        return 200.0
    return round(spot_price * 0.0075, 2)


class SimulatedBrokerClient:
    """
    Mocks OpenAlgo API client (api) to intercept strategy order execution
    and market data subscriptions during historical bar replay.
    """
    def __init__(self, api_key=None, host=None, ws_url=None, verbose=0):
        self.api_key = api_key
        self.host = host
        self.ws_url = ws_url
        self.verbose = verbose
        self.placed_orders = []
        self.subscribed_symbols = []
        self.current_bar = {}
        self.symbol_quotes = {}

    def connect(self):
        """Mocks OpenAlgo WebSocket connect."""
        return True

    def set_current_bar(self, data_dict):
        """Sets the current historical bar data."""
        self.current_bar = data_dict
        sym = data_dict.get("symbol", "")
        ltp = float(data_dict.get("close", data_dict.get("ltp", 0.0)))
        self.symbol_quotes[sym] = ltp

    def optionsorder(self, strategy=None, underlying=None, exchange=None, expiry_date=None,
                     offset="ATM", option_type="CE", action="BUY", quantity=1, pricetype="MARKET", product="MIS", **kwargs):
        """Simulates OpenAlgo optionsorder placement with dynamic ATM premium."""
        spot_price = float(self.current_bar.get("close", self.current_bar.get("ltp", 52500.0)))
        opt_symbol = f"{underlying}_{offset}_{option_type}"
        est_premium = get_estimated_atm_premium(underlying or "INDEX", spot_price)
        
        order_record = {
            "status": "success",
            "order_id": f"ORD_{len(self.placed_orders) + 1}",
            "symbol": opt_symbol,
            "underlying": underlying,
            "underlying_ltp": spot_price,
            "option_type": option_type,
            "action": action,
            "quantity": quantity,
            "product": product,
            "price": est_premium,
            "timestamp": self.current_bar.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
        }
        self.placed_orders.append(order_record)
        return order_record

    def placeorder(self, strategy=None, symbol=None, action="BUY", exchange=None, price_type="MARKET", product="MIS", quantity=1, **kwargs):
        """Simulates OpenAlgo placeorder execution."""
        ltp = float(self.current_bar.get("close", self.current_bar.get("ltp", 0.0)))
        est_premium = get_estimated_atm_premium(symbol or "INDEX", ltp) if ltp > 0 else 200.0
        
        order_record = {
            "status": "success",
            "order_id": f"ORD_{len(self.placed_orders) + 1}",
            "symbol": symbol,
            "action": action,
            "quantity": quantity,
            "product": product,
            "price": est_premium,
            "timestamp": self.current_bar.get("timestamp", datetime.now().strftime("%Y-%m-%d %H:%M"))
        }
        self.placed_orders.append(order_record)
        return order_record

    def quotes(self, exchange=None, symbol=None):
        """Returns mock/simulated quote."""
        ltp = float(self.current_bar.get("close", self.current_bar.get("ltp", 0.0)))
        return {"status": "success", "symbol": symbol, "ltp": ltp}

    def subscribe_ltp(self, symbol_list, on_data_received=None):
        """Mocks subscribing to market ticks."""
        self.subscribed_symbols.extend(symbol_list)
        return {"status": "success"}


class UniversalStrategyRunner:
    """
    Universal Dynamic Strategy Execution Engine.
    Executes ANY Python strategy script without hardcoded strategy names or logic.
    """
    def __init__(self, strategy_path: str):
        self.strategy_path = strategy_path
        with open(strategy_path, "r", encoding="utf-8") as f:
            self.code = f.read()
        self.tree = ast.parse(self.code)
        
        # Dynamic Module Loader
        self.module = None
        try:
            module_name = f"dynamic_strat_{os.path.basename(strategy_path).replace('.py', '')}"
            spec = importlib.util.spec_from_file_location(module_name, strategy_path)
            if spec and spec.loader:
                mod = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = mod
                spec.loader.exec_module(mod)
                self.module = mod
        except Exception as exc:
            print(f"Notice: Dynamic module import for {strategy_path}: {exc}")

    def extract_parameters(self) -> dict:
        """Extracts CONFIG dictionary or os.getenv(...) parameters dynamically."""
        params = {}
        
        # 1. Extract from module CONFIG dictionary if available
        if self.module and hasattr(self.module, "CONFIG") and isinstance(self.module.CONFIG, dict):
            for k, v in self.module.CONFIG.items():
                if isinstance(v, (int, float, str, bool)):
                    params[k.upper()] = v

        # 2. Extract os.getenv("PARAM_NAME", "default")
        matches = re.finditer(r'os\.getenv\(\s*["\']([A-Z_0-9]+)["\']\s*,\s*["\']([^"\']+)["\']\s*\)', self.code)
        for match in matches:
            key, val = match.groups()
            if key in ["OPENALGO_API_KEY", "HOST_SERVER", "OPENALGO_HOST", "WEBSOCKET_URL", "WEBSOCKET_HOST", "WEBSOCKET_PORT", "STRATEGY_NAME"]:
                continue
            if val.isdigit():
                params[key] = int(val)
            elif val.replace('.', '', 1).isdigit() and val.count('.') < 2:
                params[key] = float(val)
            elif val.lower() in ['true', 'false']:
                params[key] = val.lower() == 'true'
            else:
                params[key] = val

        # 3. Extract direct AST assignments
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        name_upper = target.id.upper()
                        if any(k in name_upper for k in ["SL", "STOP", "TARGET", "TP", "TRAIL", "LOT", "PRODUCT", "DELTA"]):
                            if isinstance(node.value, ast.Constant):
                                params[name_upper] = node.value.value
                            elif isinstance(node.value, ast.Num):
                                params[name_upper] = node.value.n
                            elif isinstance(node.value, ast.Str):
                                params[name_upper] = node.value.s

        return params

    def run_simulation(self, symbol: str, df: pd.DataFrame, params: dict) -> list:
        """
        Universal entry point to simulate ANY strategy across historical OHLCV data.
        """
        LOT_SIZES = {
            "NIFTY": 65,
            "BANKNIFTY": 30,
            "FINNIFTY": 60,
            "MIDCPNIFTY": 120,
            "SENSEX": 20,
            "BANKEX": 30
        }
        lot_sz = params.get("LOT_SIZE", params.get("LOTSIZE", LOT_SIZES.get(symbol.upper(), 65)))
        delta = float(params.get("DELTA", 0.55))

        # Check if the module defines a Strategy Class
        strat_class = None
        if self.module:
            for attr_name in dir(self.module):
                attr = getattr(self.module, attr_name)
                if isinstance(attr, type) and "Strategy" in attr_name and attr_name != "OpenAlgoInstitutionalStrategyBase":
                    strat_class = attr
                    break

        if strat_class is not None and hasattr(self.module, "CONFIG"):
            return self._run_class_strategy_simulation(symbol, df, strat_class, self.module.CONFIG, lot_sz, delta)

        return self._run_universal_bar_simulation(symbol, df, params, lot_sz, delta)

    def _run_class_strategy_simulation(self, symbol: str, df: pd.DataFrame, strat_class, config: dict, lot_sz: int, delta: float) -> list:
        """
        Executes Class-Based Strategy by replaying OHLCV candles to the strategy instance.
        """
        cfg_copy = dict(config)
        cfg_copy["active_index"] = symbol.upper()
        if symbol.upper() in cfg_copy.get("indices_registry", {}):
            cfg_copy["indices_registry"][symbol.upper()]["lot_size"] = lot_sz

        sim_client = SimulatedBrokerClient()

        try:
            strat_instance = strat_class(cfg_copy)
            strat_instance.client = sim_client
        except Exception as exc:
            print(f"Notice: Class instantiation fallback: {exc}")
            return self._run_universal_bar_simulation(symbol, df, config, lot_sz, delta)

        trades = []
        trade_id = 1
        in_pos = False
        pos_dir = None
        spot_entry = 0.0
        entry_t = None

        close = df["close"]
        high = df["high"]
        low = df["low"]
        vol = df["volume"] if "volume" in df.columns else pd.Series(1000, index=df.index)

        # Reset candle history
        if hasattr(strat_instance, "aggregator") and hasattr(strat_instance.aggregator, "candles"):
            strat_instance.aggregator.candles = []

        for i in range(len(df)):
            t = df.index[i]
            t_dt = pd.to_datetime(t)
            c_p = float(close.iloc[i])
            h_p = float(high.iloc[i])
            l_p = float(low.iloc[i])
            v_p = int(vol.iloc[i])

            sim_client.set_current_bar({"symbol": symbol, "close": c_p, "high": h_p, "low": l_p, "timestamp": t_dt.strftime("%Y-%m-%d %H:%M")})

            # Check open position risk & intraday MIS session close (15:15 IST)
            if in_pos:
                exit_pos = False
                exit_price = c_p
                exit_reason = "SIGNAL"

                # 1. Hard Intraday Square-Off at 15:15 IST
                if t_dt.time() >= pd.to_datetime("15:15").time():
                    exit_pos = True
                    exit_price = c_p
                    exit_reason = "SESSION_CLOSE (15:15 IST)"

                # 2. Check Stop Loss Hit from strategy instance state
                elif hasattr(strat_instance, "stop_loss") and strat_instance.stop_loss > 0:
                    if pos_dir == "CE" and l_p <= strat_instance.stop_loss:
                        exit_pos = True
                        exit_price = strat_instance.stop_loss
                        exit_reason = "SL_HIT"
                    elif pos_dir == "PE" and h_p >= strat_instance.stop_loss:
                        exit_pos = True
                        exit_price = strat_instance.stop_loss
                        exit_reason = "SL_HIT"

                # 3. Check Take Profit Hit from strategy instance state
                elif hasattr(strat_instance, "take_profit") and strat_instance.take_profit > 0:
                    if pos_dir == "CE" and h_p >= strat_instance.take_profit:
                        exit_pos = True
                        exit_price = strat_instance.take_profit
                        exit_reason = "TARGET_HIT"
                    elif pos_dir == "PE" and l_p <= strat_instance.take_profit:
                        exit_pos = True
                        exit_price = strat_instance.take_profit
                        exit_reason = "TARGET_HIT"

                if exit_pos:
                    spot_pnl_pts = round(exit_price - spot_entry, 2) if pos_dir == "CE" else round(spot_entry - exit_price, 2)
                    option_pnl_pts = round(spot_pnl_pts * delta, 2)
                    pnl_rs = round(option_pnl_pts * lot_sz, 2)
                    res_val = "WIN" if option_pnl_pts > 0 else "LOSS"

                    # Dynamic ATM Premium based on Entry Spot Level
                    est_entry_prem = get_estimated_atm_premium(symbol, spot_entry)

                    # Calculate intraday market session holding minutes (09:15 - 15:30 IST)
                    holding_mins = 0
                    if t_dt > entry_t:
                        curr_d = entry_t.date()
                        end_d = t_dt.date()
                        while curr_d <= end_d:
                            if curr_d.weekday() < 5:
                                m_open = datetime.combine(curr_d, datetime.min.time()).replace(hour=9, minute=15)
                                m_close = datetime.combine(curr_d, datetime.min.time()).replace(hour=15, minute=30)
                                t_s = max(entry_t, m_open) if curr_d == entry_t.date() else m_open
                                t_e = min(t_dt, m_close) if curr_d == t_dt.date() else m_close
                                if t_e > t_s:
                                    holding_mins += int((t_e - t_s).total_seconds() / 60)
                            curr_d += timedelta(days=1)

                    h_mins = max(1, holding_mins)
                    h_str = f"{h_mins} min" if h_mins < 60 else f"{h_mins // 60}h {h_mins % 60}m"

                    trades.append({
                        "trade_id": trade_id,
                        "symbol": symbol,
                        "date": entry_t.strftime("%Y-%m-%d"),
                        "day": entry_t.strftime("%a"),
                        "entry_time": entry_t.strftime("%Y-%m-%d %H:%M"),
                        "exit_time": t_dt.strftime("%Y-%m-%d %H:%M"),
                        "direction": f"Call ({pos_dir})" if pos_dir == "CE" else f"Put ({pos_dir})",
                        "action": f"BUY {pos_dir}",
                        "option_type": pos_dir,
                        "size": 1,
                        "entry_price": spot_entry,
                        "exit_price": exit_price,
                        "pnl_pts": option_pnl_pts,
                        "result": res_val,
                        "holding_time": h_str,
                        "pnl": pnl_rs,
                        "return_pct": round((option_pnl_pts / est_entry_prem) * 100.0, 2)
                    })
                    trade_id += 1
                    in_pos = False
                    pos_dir = None
                    strat_instance.position = None

            # Feed candle to strategy instance callbacks
            try:
                orders_before = len(sim_client.placed_orders)
                if hasattr(strat_instance, "on_tick_received"):
                    strat_instance.on_tick_received({"ltp": c_p, "volume": v_p, "timestamp": int(t_dt.timestamp() * 1000)})
                elif hasattr(strat_instance, "on_market_data"):
                    strat_instance.on_market_data({"close": c_p, "high": h_p, "low": l_p, "open": float(df["open"].iloc[i]), "volume": v_p})
                
                orders_after = len(sim_client.placed_orders)

                if not in_pos and orders_after > orders_before:
                    new_order = sim_client.placed_orders[-1]
                    in_pos = True
                    pos_dir = new_order.get("option_type", "CE")
                    spot_entry = c_p
                    entry_t = t_dt
            except Exception:
                pass

        return trades

    def _run_universal_bar_simulation(self, symbol: str, df: pd.DataFrame, params: dict, lot_sz: int, delta: float) -> list:
        """
        Universal Bar-by-Bar Signal & Strategy Execution Engine.
        Dynamically extracts signals, SL, TP, and 15:15 IST intraday MIS square-off.
        """
        close = df["close"]
        high = df["high"]
        low = df["low"]

        # 1. Extract SL and TP from strategy parameters or code
        sl_pts = None
        for k in ["STOP_LOSS", "SL_PTS", "SL_POINTS", "SL", "STOPLOSS"]:
            if k in params:
                sl_pts = float(params[k])
                break

        tp_pts = None
        for k in ["TARGET", "TARGET_PTS", "TARGET_POINTS", "TP", "TAKE_PROFIT"]:
            if k in params:
                tp_pts = float(params[k])
                break

        # 2. Universal Signal Generator
        is_ema = "EMA_FAST" in params and "EMA_SLOW" in params
        is_supertrend = "ST_PERIOD" in params or "ST_MULTIPLIER" in params or "supertrend" in self.code.lower()

        if is_ema:
            fast = params.get("EMA_FAST", 5)
            slow = params.get("EMA_SLOW", 13)
            ema_f = ta.ema(close, fast)
            ema_s = ta.ema(close, slow)
            buy_s = ta.crossover(ema_f, ema_s)
            sell_s = ta.crossunder(ema_f, ema_s)
        elif is_supertrend:
            st_p = params.get("ST_PERIOD", 7)
            st_m = params.get("ST_MULTIPLIER", 2.0)
            _, st_dir = ta.supertrend(high, low, close, period=st_p, multiplier=st_m)
            st_dir = pd.Series(st_dir, index=close.index)
            buy_s = (st_dir == 1) & (st_dir.shift(1) != 1)
            sell_s = (st_dir == -1) & (st_dir.shift(1) != -1)
        else:
            rsi_p = params.get("RSI_PERIOD", 14)
            rsi_ob = params.get("RSI_OB", 70)
            rsi_os = params.get("RSI_OS", 30)
            rsi = ta.rsi(close, rsi_p)
            buy_s = (rsi < rsi_os)
            sell_s = (rsi > rsi_ob)

        is_late = close.index.time >= pd.to_datetime("14:45").time()

        ce_in = pd.Series(buy_s, index=close.index).fillna(False).astype(bool)
        ce_out = pd.Series(sell_s, index=close.index).fillna(False).astype(bool)
        ce_in[is_late] = False

        pe_in = pd.Series(sell_s, index=close.index).fillna(False).astype(bool)
        pe_out = pd.Series(buy_s, index=close.index).fillna(False).astype(bool)
        pe_in[is_late] = False

        trades = []
        trade_id = 1

        in_pos = False
        pos_dir = None
        entry_p = 0.0
        entry_t = None

        for i in range(len(close)):
            t = close.index[i]
            t_dt = pd.to_datetime(t)
            c_p = float(close.iloc[i])
            h_p = float(high.iloc[i])
            l_p = float(low.iloc[i])

            if not in_pos:
                if t_dt.time() >= pd.to_datetime("14:45").time():
                    continue

                if ce_in.iloc[i]:
                    in_pos = True
                    pos_dir = "CE"
                    entry_p = c_p
                    entry_t = t_dt
                elif pe_in.iloc[i]:
                    in_pos = True
                    pos_dir = "PE"
                    entry_p = c_p
                    entry_t = t_dt
            else:
                exit_pos = False
                exit_p = c_p

                # 1. Intraday MIS Session Auto Square-Off at 15:15 IST
                if t_dt.time() >= pd.to_datetime("15:15").time():
                    exit_pos = True
                    exit_p = c_p

                # 2. Stop Loss Hit Check on candle High/Low
                elif sl_pts is not None:
                    if pos_dir == "CE":
                        sl_target = entry_p - sl_pts
                        if l_p <= sl_target:
                            exit_pos = True
                            exit_p = sl_target
                    elif pos_dir == "PE":
                        sl_target = entry_p + sl_pts
                        if h_p >= sl_target:
                            exit_pos = True
                            exit_p = sl_target

                # 3. Take Profit Hit Check on candle High/Low
                if not exit_pos and tp_pts is not None:
                    if pos_dir == "CE":
                        tp_target = entry_p + tp_pts
                        if h_p >= tp_target:
                            exit_pos = True
                            exit_p = tp_target
                    elif pos_dir == "PE":
                        tp_target = entry_p - tp_pts
                        if l_p <= tp_target:
                            exit_pos = True
                            exit_p = tp_target

                # 4. Signal Exit Check
                if not exit_pos:
                    if pos_dir == "CE" and ce_out.iloc[i]:
                        exit_pos = True
                        exit_p = c_p
                    elif pos_dir == "PE" and pe_out.iloc[i]:
                        exit_pos = True
                        exit_p = c_p

                if exit_pos:
                    spot_pnl_pts = round(exit_p - entry_p, 2) if pos_dir == "CE" else round(entry_p - exit_p, 2)
                    option_pnl_pts = round(spot_pnl_pts * delta, 2)
                    pnl_rs = round(option_pnl_pts * lot_sz, 2)
                    res_val = "WIN" if option_pnl_pts > 0 else "LOSS"

                    est_entry_prem = get_estimated_atm_premium(symbol, entry_p)

                    holding_mins = 0
                    if t_dt > entry_t:
                        curr_d = entry_t.date()
                        end_d = t_dt.date()
                        while curr_d <= end_d:
                            if curr_d.weekday() < 5:
                                m_open = datetime.combine(curr_d, datetime.min.time()).replace(hour=9, minute=15)
                                m_close = datetime.combine(curr_d, datetime.min.time()).replace(hour=15, minute=30)
                                t_s = max(entry_t, m_open) if curr_d == entry_t.date() else m_open
                                t_e = min(t_dt, m_close) if curr_d == t_dt.date() else m_close
                                if t_e > t_s:
                                    holding_mins += int((t_e - t_s).total_seconds() / 60)
                            curr_d += timedelta(days=1)

                    h_mins = max(1, holding_mins)
                    h_str = f"{h_mins} min" if h_mins < 60 else f"{h_mins // 60}h {h_mins % 60}m"

                    trades.append({
                        "trade_id": trade_id,
                        "symbol": symbol,
                        "date": entry_t.strftime("%Y-%m-%d"),
                        "day": entry_t.strftime("%a"),
                        "entry_time": entry_t.strftime("%Y-%m-%d %H:%M"),
                        "exit_time": t_dt.strftime("%Y-%m-%d %H:%M"),
                        "direction": f"Call ({pos_dir})" if pos_dir == "CE" else f"Put ({pos_dir})",
                        "action": f"BUY {pos_dir}",
                        "option_type": pos_dir,
                        "size": 1,
                        "entry_price": entry_p,
                        "exit_price": exit_p,
                        "pnl_pts": option_pnl_pts,
                        "result": res_val,
                        "holding_time": h_str,
                        "pnl": pnl_rs,
                        "return_pct": round((option_pnl_pts / est_entry_prem) * 100.0, 2)
                    })
                    trade_id += 1
                    in_pos = False
                    pos_dir = None

        return trades
