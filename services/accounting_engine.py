"""
services/accounting_engine.py
Indian F&O Derivatives Accounting & Asymmetric Taxation Engine (2026 Regulations)
"""

from typing import Dict, Any, Optional


class IndianFOAccountingEngine:
    # Statutory Rates (Indian Equity & Commodity Derivatives - 2026 Norms)
    STT_OPTION_SELL_RATE = 0.0010          # 0.10% on Option Sell Turnover
    STT_FUTURES_SELL_RATE = 0.0002         # 0.02% on Futures Sell Turnover
    STAMP_DUTY_BUY_RATE = 0.00003         # 0.003% on Buy Turnover
    EXCHANGE_OPTION_RATE = 0.0005         # 0.05% on Option Premium Turnover
    EXCHANGE_FUTURES_RATE = 0.000019      # 0.0019% on Futures Contract Turnover
    SEBI_TURNOVER_RATE = 0.000001         # Rs 10 per Crore (0.0001%)
    GST_RATE = 0.18                       # 18% on (Brokerage + Exchange + SEBI)
    BROKERAGE_PER_ORDER = 20.0            # Default Rs. 20 per order

    @classmethod
    def calculate_closed_trade_pnl(
        cls, 
        entry_price: float, 
        exit_price: float, 
        qty: int, 
        direction: str = "BUY",
        brokerage_per_order: Optional[float] = None,
        is_option: bool = True
    ) -> Dict[str, Any]:
        """Calculates exact gross and net realized P&L for a closed trade with asymmetric F&O taxes."""
        qty = abs(int(qty))
        if qty == 0:
            return {
                "gross_pnl": 0.0, "net_pnl": 0.0, "brokerage": 0.0, "stt": 0.0,
                "stamp_duty": 0.0, "exchange_charges": 0.0, "sebi_charges": 0.0,
                "gst": 0.0, "total_charges": 0.0, "buy_turnover": 0.0, "sell_turnover": 0.0
            }

        brokerage_rate = cls.BROKERAGE_PER_ORDER if brokerage_per_order is None else float(brokerage_per_order)
        stt_rate = cls.STT_OPTION_SELL_RATE if is_option else cls.STT_FUTURES_SELL_RATE
        exch_rate = cls.EXCHANGE_OPTION_RATE if is_option else cls.EXCHANGE_FUTURES_RATE
        
        if str(direction).upper() in ["BUY", "LONG"]:
            buy_price = float(entry_price)
            sell_price = float(exit_price)
            gross_pnl = (sell_price - buy_price) * qty
        else:  # Short / Option Seller
            sell_price = float(entry_price)
            buy_price = float(exit_price)
            gross_pnl = (sell_price - buy_price) * qty

        buy_turnover = buy_price * qty
        sell_turnover = sell_price * qty
        total_turnover = buy_turnover + sell_turnover

        # Asymmetric tax calculation
        brokerage = 2.0 * brokerage_rate
        stt = round(sell_turnover * stt_rate, 2)
        stamp_duty = round(buy_turnover * cls.STAMP_DUTY_BUY_RATE, 2)
        exchange_charges = round(total_turnover * exch_rate, 2)
        sebi_charges = round(total_turnover * cls.SEBI_TURNOVER_RATE, 2)
        
        gst = round((brokerage + exchange_charges + sebi_charges) * cls.GST_RATE, 2)
        total_charges = round(brokerage + stt + stamp_duty + exchange_charges + sebi_charges + gst, 2)
        net_pnl = round(gross_pnl - total_charges, 2)

        return {
            "gross_pnl": round(gross_pnl, 2),
            "net_pnl": net_pnl,
            "brokerage": brokerage,
            "stt": stt,
            "stamp_duty": stamp_duty,
            "exchange_charges": exchange_charges,
            "sebi_charges": sebi_charges,
            "gst": gst,
            "total_charges": total_charges,
            "buy_turnover": round(buy_turnover, 2),
            "sell_turnover": round(sell_turnover, 2)
        }

    @classmethod
    def calculate_open_position_mtm(
        cls, 
        entry_price: float, 
        current_ltp: float, 
        qty: int, 
        direction: str = "BUY",
        brokerage_per_order: Optional[float] = None,
        is_option: bool = True
    ) -> Dict[str, Any]:
        """Calculates live Gross MTM and Estimated Net MTM factoring accrued entry and estimated exit taxes."""
        qty = abs(int(qty))
        if qty == 0:
            return {
                "gross_mtm": 0.0, "net_mtm": 0.0, "accrued_and_exit_charges": 0.0,
                "stt": 0.0, "stamp_duty": 0.0, "exchange_charges": 0.0, "sebi_charges": 0.0,
                "gst": 0.0, "brokerage": 0.0, "entry_turnover": 0.0, "est_exit_turnover": 0.0
            }

        brokerage_rate = cls.BROKERAGE_PER_ORDER if brokerage_per_order is None else float(brokerage_per_order)
        stt_rate = cls.STT_OPTION_SELL_RATE if is_option else cls.STT_FUTURES_SELL_RATE
        exch_rate = cls.EXCHANGE_OPTION_RATE if is_option else cls.EXCHANGE_FUTURES_RATE
        is_long = str(direction).upper() in ["BUY", "LONG"]
        entry_p = float(entry_price)
        ltp = float(current_ltp)

        if is_long:
            gross_mtm = (ltp - entry_p) * qty
            entry_turnover = entry_p * qty
            est_exit_turnover = ltp * qty
            
            entry_stamp_duty = entry_turnover * cls.STAMP_DUTY_BUY_RATE
            entry_stt = 0.0
            est_exit_stt = est_exit_turnover * stt_rate
            est_exit_stamp_duty = 0.0
        else:  # Short / Option Seller
            gross_mtm = (entry_p - ltp) * qty
            entry_turnover = entry_p * qty
            est_exit_turnover = ltp * qty
            
            entry_stt = entry_turnover * stt_rate
            entry_stamp_duty = 0.0
            est_exit_stamp_duty = est_exit_turnover * cls.STAMP_DUTY_BUY_RATE
            est_exit_stt = 0.0

        total_est_turnover = entry_turnover + est_exit_turnover
        brokerage = 2.0 * brokerage_rate
        total_stt = round(entry_stt + est_exit_stt, 2)
        total_stamp_duty = round(entry_stamp_duty + est_exit_stamp_duty, 2)
        exchange_charges = round(total_est_turnover * exch_rate, 2)
        sebi_charges = round(total_est_turnover * cls.SEBI_TURNOVER_RATE, 2)
        gst = round((brokerage + exchange_charges + sebi_charges) * cls.GST_RATE, 2)

        est_total_charges = round(brokerage + total_stt + total_stamp_duty + exchange_charges + sebi_charges + gst, 2)
        net_mtm = round(gross_mtm - est_total_charges, 2)

        return {
            "gross_mtm": round(gross_mtm, 2),
            "net_mtm": net_mtm,
            "accrued_and_exit_charges": est_total_charges,
            "stt": total_stt,
            "stamp_duty": total_stamp_duty,
            "exchange_charges": exchange_charges,
            "sebi_charges": sebi_charges,
            "gst": gst,
            "brokerage": brokerage,
            "entry_turnover": round(entry_turnover, 2),
            "est_exit_turnover": round(est_exit_turnover, 2)
        }
