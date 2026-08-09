# backtesting/pricing/option_models.py
"""
Indian Market Option Pricing Engine.
BSM Model for European Spot Index Options (NSE/BSE) and Black-76 for MCX Commodity Options.
Calculated using RBI benchmark risk-free rate (~6.5%).
"""
import math
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

        T = dte_days / 365.0
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

        T = dte_days / 365.0
        F, K, r, sigma = float(futures_price), float(strike), float(rate), max(0.01, float(iv))

        d1 = (math.log(F / K) + 0.5 * (sigma**2) * T) / (sigma * math.sqrt(T))
        d2 = d1 - sigma * math.sqrt(T)
        discount = math.exp(-r * T)

        if option_type.upper() == "CE":
            price = discount * (F * norm.cdf(d1) - K * norm.cdf(d2))
        else:
            price = discount * (K * norm.cdf(-d2) - F * norm.cdf(-d1))

        return max(0.05, round(price, 2))
