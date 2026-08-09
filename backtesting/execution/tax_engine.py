# backtesting/execution/tax_engine.py
"""
Statutory Costs & Tax Engine for Indian F&O Trading.
Implements NSE STT (0.10% on sell-side premium), MCX CTT (0.05% on sell-side premium),
Exchange Transaction Fees, SEBI Turnover Charges, Stamp Duty, and GST (18%).
"""
from typing import Dict


class IndianTaxEngine:
    """Calculates statutory charges for Indian F&O (NSE STT vs. MCX CTT)."""

    @staticmethod
    def calculate_charges(
        buy_premium: float,
        sell_premium: float,
        quantity: int,
        exchange: str = "NSE_INDEX",
        brokerage_per_order: float = 20.0
    ) -> Dict[str, float]:
        buy_val = buy_premium * quantity
        sell_val = sell_premium * quantity
        turnover = buy_val + sell_val

        brokerage = brokerage_per_order * 2.0  # Round trip

        if exchange.upper() in ["MCX", "MCX_COMMODITY"]:
            stt_ctt = sell_val * 0.0005  # CTT: 0.05% on Sell Side Premium for MCX Options
            exchange_fee = turnover * 0.000418
        else:
            stt_ctt = sell_val * 0.0010  # NSE Options STT: 0.10% on Sell Side Premium
            exchange_fee = turnover * 0.00035

        sebi_fee = turnover * (10.0 / 10_000_000.0)
        stamp_duty = buy_val * 0.00003
        gst = (brokerage + exchange_fee + sebi_fee) * 0.18

        total_charges = brokerage + stt_ctt + exchange_fee + sebi_fee + stamp_duty + gst

        return {
            "total_charges": round(total_charges, 2),
            "brokerage": round(brokerage, 2),
            "stt_ctt": round(stt_ctt, 2),
            "exchange_fee": round(exchange_fee, 2),
            "gst": round(gst, 2),
            "stamp_duty": round(stamp_duty, 2),
            "sebi_fee": round(sebi_fee, 2),
        }
