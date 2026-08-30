# backtesting/execution/tax_engine.py
"""
Statutory Costs & Tax Engine for Indian F&O Trading (2026 Regulations).
Synchronized with services.accounting_engine.IndianFOAccountingEngine.
"""
from typing import Dict


class IndianTaxEngine:
    """Calculates statutory charges for Indian F&O (NSE STT vs. MCX CTT vs. Futures)."""

    STT_OPTION_SELL_RATE = 0.0010          # 0.10% on Option Sell Turnover
    STT_FUTURES_SELL_RATE = 0.0002         # 0.02% on Futures Sell Turnover
    STAMP_DUTY_BUY_RATE = 0.00003         # 0.003% on Buy Turnover
    EXCHANGE_OPTION_RATE = 0.0005         # 0.05% on Option Premium Turnover
    EXCHANGE_FUTURES_RATE = 0.000019      # 0.0019% on Futures Contract Turnover
    SEBI_TURNOVER_RATE = 0.000001         # Rs 10 per Crore (0.0001%)
    GST_RATE = 0.18                       # 18% on (Brokerage + Exchange + SEBI)
    BROKERAGE_PER_ORDER = 20.0            # Default Rs. 20 per order

    @classmethod
    def calculate_charges(
        cls,
        buy_premium: float,
        sell_premium: float,
        quantity: int,
        exchange: str = "NSE_INDEX",
        brokerage_per_order: float = 20.0,
        is_option: bool = True
    ) -> Dict[str, float]:
        qty = abs(int(quantity))
        if qty == 0:
            return {
                "total_charges": 0.0, "brokerage": 0.0, "stt_ctt": 0.0,
                "exchange_fee": 0.0, "gst": 0.0, "stamp_duty": 0.0, "sebi_fee": 0.0
            }

        buy_val = float(buy_premium) * qty
        sell_val = float(sell_premium) * qty
        turnover = buy_val + sell_val

        brokerage = float(brokerage_per_order) * 2.0  # Round trip
        stt_rate = cls.STT_OPTION_SELL_RATE if is_option else cls.STT_FUTURES_SELL_RATE
        exch_rate = cls.EXCHANGE_OPTION_RATE if is_option else cls.EXCHANGE_FUTURES_RATE

        stt_ctt = round(sell_val * stt_rate, 2)
        exchange_fee = round(turnover * exch_rate, 2)
        sebi_fee = round(turnover * cls.SEBI_TURNOVER_RATE, 2)
        stamp_duty = round(buy_val * cls.STAMP_DUTY_BUY_RATE, 2)
        gst = round((brokerage + exchange_fee + sebi_fee) * cls.GST_RATE, 2)

        total_charges = round(brokerage + stt_ctt + exchange_fee + sebi_fee + stamp_duty + gst, 2)

        return {
            "total_charges": total_charges,
            "brokerage": round(brokerage, 2),
            "stt_ctt": stt_ctt,
            "exchange_fee": exchange_fee,
            "gst": gst,
            "stamp_duty": stamp_duty,
            "sebi_fee": sebi_fee,
        }
