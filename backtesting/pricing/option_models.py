# backtesting/pricing/option_models.py
"""
Indian Market Option Pricing Engine.
BSM Model for European Spot Index Options (NSE/BSE) and Black-76 for MCX Commodity Options.
Calculated using RBI benchmark risk-free rate (~6.5%).
"""
import math
import datetime
from typing import Dict, Any, Optional
import pandas as pd
from scipy.stats import norm


class IndianOptionPricingEngine:
    """Prices European Spot Options (BSM) & Commodity Futures Options (Black-76) for the Indian Market."""

    @staticmethod
    def price_nse_index_option(
        spot: float, strike: float, dte_days: float, iv: float = 0.18, rate: float = 0.065, option_type: str = "CE"
    ) -> float:
        """Black-Scholes-Merton model for European Spot Options on Indian Exchanges (NSE/BSE)."""
        if dte_days <= 0.0001:
            return max(0.05, round(spot - strike if option_type.upper() == "CE" else strike - spot, 2))

        T = max(0.0001, float(dte_days) / 365.0)
        S, K, r, sigma = float(spot), float(strike), float(rate), max(0.01, float(iv))

        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)

        if option_type.upper() == "CE":
            price = S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
        else:
            price = K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)

        return max(0.05, round(price, 2))

    @staticmethod
    def price_mcx_commodity_option(
        futures_price: float, strike: float, dte_days: float, iv: float = 0.25, rate: float = 0.065, option_type: str = "CE"
    ) -> float:
        """Black-76 model for Options on Commodity Futures (MCX India)."""
        if dte_days <= 0.0001:
            return max(0.05, round(futures_price - strike if option_type.upper() == "CE" else strike - futures_price, 2))

        T = max(0.0001, float(dte_days) / 365.0)
        F, K, r, sigma = float(futures_price), float(strike), float(rate), max(0.01, float(iv))

        d1 = (math.log(F / K) + 0.5 * (sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        discount = math.exp(-r * T)

        if option_type.upper() == "CE":
            price = discount * (F * norm.cdf(d1) - K * norm.cdf(d2))
        else:
            price = discount * (K * norm.cdf(-d2) - F * norm.cdf(-d1))

        return max(0.05, round(price, 2))

    @staticmethod
    def calculate_intraday_dte(trade_date: Any, trade_time: Any, expiry_date: Any, exchange: str = "NSE") -> float:
        """
        Calculates exact fractional Days-to-Expiry (DTE) accounting for continuous intraday time decay.
        """
        if isinstance(trade_date, str):
            trade_date = pd.to_datetime(trade_date).date()
        elif hasattr(trade_date, "date"):
            trade_date = trade_date.date()

        if isinstance(expiry_date, str):
            expiry_date = pd.to_datetime(expiry_date).date()
        elif hasattr(expiry_date, "date"):
            expiry_date = expiry_date.date()

        days = (expiry_date - trade_date).days
        if days < 0:
            return 0.0001

        # Calculate session elapsed fraction
        if exchange.upper() in ("MCX", "COMMODITY"):
            # MCX: 16:00 to 23:30 (450 minutes)
            h = trade_time.hour if hasattr(trade_time, "hour") else 16
            m = trade_time.minute if hasattr(trade_time, "minute") else 0
            mins_from_start = max(0, (h - 16) * 60 + m)
            intraday_frac = min(1.0, mins_from_start / 450.0)
        else:
            # NSE/BSE: 09:15 to 15:30 (375 minutes)
            h = trade_time.hour if hasattr(trade_time, "hour") else 9
            m = trade_time.minute if hasattr(trade_time, "minute") else 15
            mins_from_start = max(0, (h - 9) * 60 + (m - 15))
            intraday_frac = min(1.0, mins_from_start / 375.0)

        exact_dte = max(0.0001, float(days) + (1.0 - intraday_frac))
        return exact_dte

    @staticmethod
    def resolve_strike(spot: float, symbol: str, option_type: str = "CE", offset: str = "ATM") -> float:
        """Resolves exact ATM, ITM1, or OTM1 strike price based on symbol strike step."""
        sym = symbol.upper()
        if "NIFTY" in sym and "BANK" not in sym and "MID" not in sym and "FIN" not in sym:
            step = 50.0
        elif "BANKNIFTY" in sym or "SENSEX" in sym or "BANKEX" in sym or "GOLDM" in sym or "CRUDE" in sym:
            step = 100.0
        elif "FINNIFTY" in sym or "MIDCPNIFTY" in sym:
            step = 25.0
        else:
            step = 100.0

        atm_strike = round(spot / step) * step
        opt_type = option_type.upper()

        off_up = offset.upper()
        if off_up.startswith("ITM"):
            n_steps = int(off_up[3:]) if len(off_up) > 3 and off_up[3:].isdigit() else 1
            return (atm_strike - n_steps * step) if opt_type == "CE" else (atm_strike + n_steps * step)
        elif off_up.startswith("OTM"):
            n_steps = int(off_up[3:]) if len(off_up) > 3 and off_up[3:].isdigit() else 1
            return (atm_strike + n_steps * step) if opt_type == "CE" else (atm_strike - n_steps * step)
        return atm_strike

    @staticmethod
    def apply_execution_friction(price: float, action: str, symbol: str) -> float:
        """
        Applies bid-ask spread and market order slippage.
        Buy orders fill higher at the Ask; Sell orders fill lower at the Bid.
        """
        sym = symbol.upper()
        if "SENSEX" in sym or "BFO" in sym:
            spread_pct = 0.015
            min_friction = 4.0
        elif "BANKNIFTY" in sym or "GOLDM" in sym:
            spread_pct = 0.010
            min_friction = 2.5
        else:  # NIFTY / general
            spread_pct = 0.008
            min_friction = 1.0

        friction = max(min_friction, float(price) * spread_pct)
        if action.upper() == "BUY":
            return round(float(price) + friction, 2)
        else:
            return max(0.05, round(float(price) - friction, 2))

    @staticmethod
    def calculate_greeks(
        spot: float, strike: float, dte_days: float, iv: float = 0.18, rate: float = 0.065, option_type: str = "CE"
    ) -> Dict[str, float]:
        """Calculates Delta, Gamma, Theta (per day and per minute), and Vega."""
        T = max(0.0001, float(dte_days) / 365.0)
        S, K, r, sigma = float(spot), float(strike), float(rate), max(0.01, float(iv))
        sqrt_T = math.sqrt(T)

        d1 = (math.log(S / K) + (r + 0.5 * sigma**2) * T) / (sigma * sqrt_T)
        d2 = d1 - sigma * sqrt_T

        pdf_d1 = norm.pdf(d1)
        cdf_d1 = norm.cdf(d1)
        cdf_neg_d1 = norm.cdf(-d1)

        gamma = pdf_d1 / (S * sigma * sqrt_T)
        vega = (S * sqrt_T * pdf_d1) / 100.0

        if option_type.upper() == "CE":
            delta = cdf_d1
            theta_annual = -(S * pdf_d1 * sigma) / (2 * sqrt_T) - r * K * math.exp(-r * T) * norm.cdf(d2)
        else:
            delta = -cdf_neg_d1
            theta_annual = -(S * pdf_d1 * sigma) / (2 * sqrt_T) + r * K * math.exp(-r * T) * norm.cdf(-d2)

        theta_day = theta_annual / 365.0
        theta_minute = theta_day / 375.0

        return {
            "delta": round(delta, 4),
            "gamma": round(gamma, 6),
            "theta_day": round(theta_day, 2),
            "theta_minute": round(theta_minute, 4),
            "vega": round(vega, 2),
        }

    @classmethod
    def simulate_realistic_option_pnl(
        cls,
        symbol: str,
        pos_type: str,
        entry_spot: float,
        exit_spot: float,
        entry_date: Any,
        entry_time: Any,
        exit_date: Any,
        exit_time: Any,
        expiry_date: Any,
        quantity: int,
        offset: str = "ITM1",
        iv: float = 0.18,
        exchange: str = "NSE"
    ) -> Dict[str, Any]:
        """
        Full-fidelity option trade simulation combining:
        1. Exact BSM / Black-76 option pricing
        2. Intraday continuous Theta decay
        3. Real ITM1 / ATM strike resolution
        4. Bid-Ask spread & slippage friction on entry & exit
        5. SEBI / MCX statutory taxation
        """
        strike = cls.resolve_strike(entry_spot, symbol, pos_type, offset=offset)

        entry_dte = cls.calculate_intraday_dte(entry_date, entry_time, expiry_date, exchange=exchange)
        exit_dte = cls.calculate_intraday_dte(exit_date, exit_time, expiry_date, exchange=exchange)

        is_mcx = exchange.upper() in ("MCX", "COMMODITY")
        if is_mcx:
            raw_entry = cls.price_mcx_commodity_option(entry_spot, strike, entry_dte, iv=max(iv, 0.22), option_type=pos_type)
            raw_exit = cls.price_mcx_commodity_option(exit_spot, strike, exit_dte, iv=max(iv, 0.22), option_type=pos_type)
        else:
            raw_entry = cls.price_nse_index_option(entry_spot, strike, entry_dte, iv=iv, option_type=pos_type)
            raw_exit = cls.price_nse_index_option(exit_spot, strike, exit_dte, iv=iv, option_type=pos_type)

        # Apply realistic execution friction (spread & slippage)
        fill_entry = cls.apply_execution_friction(raw_entry, "BUY", symbol)
        fill_exit = cls.apply_execution_friction(raw_exit, "SELL", symbol)

        qty = abs(int(quantity))
        gross_pnl = round((fill_exit - fill_entry) * qty, 2)

        # Tax calculation (SEBI Oct 2024 / MCX)
        turnover = (fill_entry + fill_exit) * qty
        brokerage = 40.0
        stt = round(fill_exit * qty * (0.0010 if not is_mcx else 0.0005), 2)
        exch_fee = round(turnover * 0.0005, 2)
        gst = round((brokerage + exch_fee) * 0.18, 2)
        stamp = round(fill_entry * qty * 0.00003, 2)
        sebi = round(turnover * 0.000001, 2)
        total_taxes = round(brokerage + stt + exch_fee + gst + stamp + sebi, 2)
        net_pnl = round(gross_pnl - total_taxes, 2)

        return {
            "symbol": symbol,
            "strike": strike,
            "option_type": pos_type,
            "entry_prem_raw": raw_entry,
            "entry_prem_fill": fill_entry,
            "exit_prem_raw": raw_exit,
            "exit_prem_fill": fill_exit,
            "gross_pnl": gross_pnl,
            "taxes": total_taxes,
            "net_pnl": net_pnl,
        }
