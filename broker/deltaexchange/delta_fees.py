"""
Delta Exchange India Fee & Tax Structure Reference & Calculator
Official charges for Indian residents trading on Delta Exchange India (FIU-IND Registered).
Verified directly against Delta Exchange India official fee schedule.
"""

# ------------------------------------------------------------------------------
# 1. OFFICIAL EXCHANGE COMMISSION RATES
# ------------------------------------------------------------------------------
# Futures (Perpetuals):
FUTURES_MAKER_FEE = 0.00020   # 0.020% of Notional Value (Limit orders at or inside touch)
FUTURES_TAKER_FEE = 0.00050   # 0.050% of Notional Value (Market orders / Immediate fills)

# Options:
# Trading fee is 0.010% of notional value, STRICTLY CAPPED at 3.5% of the option premium!
OPTIONS_NOTIONAL_RATE = 0.00010   # 0.010% of Notional
OPTIONS_PREMIUM_CAP = 0.035       # 3.500% of Option Premium (Fee Cap)

# Government of India GST (Goods & Services Tax):
GST_RATE = 0.18                   # 18% on all exchange brokerage and trading fees

# Average Market Execution Slippage:
SLIPPAGE_RATE = 0.00020           # 0.020% per market fill (0% for limit maker orders)

# ------------------------------------------------------------------------------
# 2. FUTURES CALCULATOR
# ------------------------------------------------------------------------------
def calculate_futures_fee(
    entry_price: float,
    exit_price: float,
    contracts: int,
    symbol: str = "BTC",
    is_entry_maker: bool = True,
    is_exit_taker: bool = True
) -> dict:
    """
    Calculates exact round-trip fees and Indian GST for a futures/perpetual trade.
    Standard execution pattern: Limit order Entry (Maker) + Market order Exit (Taker).
    """
    lot_multiplier = 0.001 if "BTC" in symbol.upper() else 0.01
    notional_entry = entry_price * contracts * lot_multiplier
    notional_exit = exit_price * contracts * lot_multiplier

    entry_fee_rate = FUTURES_MAKER_FEE if is_entry_maker else FUTURES_TAKER_FEE
    exit_fee_rate = FUTURES_TAKER_FEE if is_exit_taker else FUTURES_MAKER_FEE

    entry_fee_raw = notional_entry * entry_fee_rate
    exit_fee_raw = notional_exit * exit_fee_rate
    total_fee_raw = entry_fee_raw + exit_fee_raw

    gst = total_fee_raw * GST_RATE
    total_fee_with_gst = total_fee_raw + gst

    slippage_cost = (notional_exit * SLIPPAGE_RATE) if is_exit_taker else 0.0
    total_drag_usd = total_fee_with_gst + slippage_cost

    return {
        "notional_entry": notional_entry,
        "notional_exit": notional_exit,
        "entry_fee_raw": entry_fee_raw,
        "exit_fee_raw": exit_fee_raw,
        "exchange_fees_usd": total_fee_raw,
        "gst_18pct_usd": gst,
        "total_fee_with_gst_usd": total_fee_with_gst,
        "total_fee_with_gst_inr": total_fee_with_gst * 88.0,
        "slippage_usd": slippage_cost,
        "total_drag_usd": total_drag_usd,
        "total_drag_inr": total_drag_usd * 88.0
    }

# ------------------------------------------------------------------------------
# 3. OPTIONS CALCULATOR (WITH 3.5% PREMIUM CAP)
# ------------------------------------------------------------------------------
def calculate_single_option_leg_fee(
    strike_price: float,
    premium_usd: float,
    contracts: int,
    symbol: str = "BTC"
) -> dict:
    """
    Calculates exchange fee for a single option leg according to Delta Exchange India:
    Fee is min(0.010% of notional, 3.5% of option premium) + 18% GST.
    """
    lot_multiplier = 0.001 if "BTC" in symbol.upper() else 0.01
    notional = strike_price * contracts * lot_multiplier
    premium_total = premium_usd * contracts * lot_multiplier

    fee_by_notional = notional * OPTIONS_NOTIONAL_RATE
    fee_by_premium_cap = premium_total * OPTIONS_PREMIUM_CAP

    # Delta charges the LOWER of notional rate or 3.5% premium cap
    fee_charged = min(fee_by_notional, fee_by_premium_cap)
    gst = fee_charged * GST_RATE
    total_with_gst = fee_charged + gst

    return {
        "notional": notional,
        "premium_total": premium_total,
        "fee_by_notional": fee_by_notional,
        "fee_by_premium_cap": fee_by_premium_cap,
        "fee_charged_usd": fee_charged,
        "gst_usd": gst,
        "total_fee_usd": total_with_gst,
        "total_fee_inr": total_with_gst * 88.0,
        "cap_applied": fee_by_premium_cap < fee_by_notional
    }

def calculate_iron_condor_roundtrip_fee(
    spot_price: float,
    short_ce_strike: float,
    short_ce_premium: float,
    short_pe_strike: float,
    short_pe_premium: float,
    long_ce_strike: float,
    long_ce_premium: float,
    long_pe_strike: float,
    long_pe_premium: float,
    contracts: int,
    symbol: str = "BTC",
    decay_exit_pct: float = 0.80
) -> dict:
    """
    Calculates complete 4-leg Iron Condor round-trip fees (Entry + Decay Exit) + 18% GST.
    """
    # 1. Entry fees for 4 legs:
    f_short_ce = calculate_single_option_leg_fee(short_ce_strike, short_ce_premium, contracts, symbol)
    f_short_pe = calculate_single_option_leg_fee(short_pe_strike, short_pe_premium, contracts, symbol)
    f_long_ce = calculate_single_option_leg_fee(long_ce_strike, long_ce_premium, contracts, symbol)
    f_long_pe = calculate_single_option_leg_fee(long_pe_strike, long_pe_premium, contracts, symbol)

    entry_fee_usd = (f_short_ce["fee_charged_usd"] + f_short_pe["fee_charged_usd"] +
                     f_long_ce["fee_charged_usd"] + f_long_pe["fee_charged_usd"])

    # 2. Exit fees (at target decay, residual premium is (1 - decay_exit_pct)):
    residual = max(0.01, 1.0 - decay_exit_pct)
    exit_fee_usd = entry_fee_usd * residual

    total_exchange_fee_usd = entry_fee_usd + exit_fee_usd
    total_gst_usd = total_exchange_fee_usd * GST_RATE
    total_fee_with_gst_usd = total_exchange_fee_usd + total_gst_usd

    # Gross net credit collected:
    lot_multiplier = 0.001 if "BTC" in symbol.upper() else 0.01
    net_credit_collected_usd = ((short_ce_premium + short_pe_premium - long_ce_premium - long_pe_premium) *
                                contracts * lot_multiplier)
    gross_win_usd = net_credit_collected_usd * decay_exit_pct

    return {
        "entry_fee_usd": entry_fee_usd * (1 + GST_RATE),
        "exit_fee_usd": exit_fee_usd * (1 + GST_RATE),
        "total_fee_with_gst_usd": total_fee_with_gst_usd,
        "total_fee_with_gst_inr": total_fee_with_gst_usd * 88.0,
        "net_credit_usd": net_credit_collected_usd,
        "gross_win_usd": gross_win_usd,
        "gross_win_inr": gross_win_usd * 88.0,
        "net_profit_usd": gross_win_usd - total_fee_with_gst_usd,
        "net_profit_inr": (gross_win_usd - total_fee_with_gst_usd) * 88.0,
        "fee_pct_of_profit": (total_fee_with_gst_usd / max(gross_win_usd, 1e-4)) * 100
    }
