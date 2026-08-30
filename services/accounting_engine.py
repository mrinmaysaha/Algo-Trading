"""
services/accounting_engine.py
================================================================================
Indian F&O Derivatives Accounting & Asymmetric Taxation Engine (2026 Regulations)
================================================================================

Implements exact statutory contract-note calculations:
1. Option Buying (Long):
   - Entry: Stamp Duty (0.003%) on Buy Turnover. STT is 0.00%.
   - Exit: STT (0.10%) on Sell Turnover. Stamp Duty is 0.00%.
2. Option Selling (Short):
   - Entry: STT (0.10%) on Sell Turnover. Stamp Duty is 0.00%.
   - Exit: Stamp Duty (0.003%) on Buy Turnover. STT is 0.00%.
3. Statutory Charges & Turnover:
   - Exchange Turnover: 0.05% on total premium turnover (Buy + Sell).
   - SEBI Turnover: Rs. 10 per Crore (0.0001% of total turnover).
   - GST: 18% on (Brokerage + Exchange Turnover + SEBI Charges).
   - Brokerage: Rs. 20 per executed order (or custom broker rate).
================================================================================
"""

import math
from typing import Dict, Any, Optional


class IndianFOAccountingEngine:
    # Statutory Rates (Indian Equity Derivatives - 2026 Norms)
    STT_OPTION_SELL_RATE = 0.0010       # 0.10% on Option Sell Turnover
    STAMP_DUTY_BUY_RATE = 0.00003      # 0.003% on Option Buy Turnover
    EXCHANGE_TURNOVER_RATE = 0.0005    # 0.05% on Premium Turnover
    SEBI_TURNOVER_RATE = 0.000001      # Rs 10 per Crore (0.0001%)
    GST_RATE = 0.18                    # 18% on (Brokerage + Exchange + SEBI)
    BROKERAGE_PER_ORDER = 20.0         # Default Rs. 20 per order

    @classmethod
    def calculate_closed_trade_pnl(
        cls, 
        entry_price: float, 
        exit_price: float, 
        qty: int, 
        direction: str = "BUY",  # 'BUY' / 'LONG' (Option Buyer) or 'SELL' / 'SHORT' (Option Seller)
        brokerage_per_order: Optional[float] = None
    ) -> Dict[str, Any]:
        """Calculates exact gross and net realized P&L for a closed trade with asymmetric F&O taxes."""
        brokerage_rate = cls.BROKERAGE_PER_ORDER if brokerage_per_order is None else float(brokerage_per_order)
        qty = abs(int(qty))
        
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
        stt = round(sell_turnover * cls.STT_OPTION_SELL_RATE, 2)
        stamp_duty = round(buy_turnover * cls.STAMP_DUTY_BUY_RATE, 2)
        exchange_charges = round(total_turnover * cls.EXCHANGE_TURNOVER_RATE, 2)
        sebi_charges = round(total_turnover * cls.SEBI_TURNOVER_RATE, 2)
        
        # GST applies on Brokerage, Exchange Charges, and SEBI Charges
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
        brokerage_per_order: Optional[float] = None
    ) -> Dict[str, Any]:
        """Calculates live Gross MTM and Estimated Net MTM factoring accrued entry taxes and estimated exit taxes."""
        brokerage_rate = cls.BROKERAGE_PER_ORDER if brokerage_per_order is None else float(brokerage_per_order)
        is_long = str(direction).upper() in ["BUY", "LONG"]
        qty = abs(int(qty))
        entry_p = float(entry_price)
        ltp = float(current_ltp)

        if is_long:
            gross_mtm = (ltp - entry_p) * qty
            entry_turnover = entry_p * qty
            est_exit_turnover = ltp * qty
            
            # Option Buyer: Entry pays Stamp Duty; Exit pays STT
            entry_stamp_duty = entry_turnover * cls.STAMP_DUTY_BUY_RATE
            entry_stt = 0.0
            est_exit_stt = est_exit_turnover * cls.STT_OPTION_SELL_RATE
            est_exit_stamp_duty = 0.0
        else:  # Option Short
            gross_mtm = (entry_p - ltp) * qty
            entry_turnover = entry_p * qty
            est_exit_turnover = ltp * qty
            
            # Option Seller: Entry pays STT; Exit pays Stamp Duty
            entry_stt = entry_turnover * cls.STT_OPTION_SELL_RATE
            entry_stamp_duty = 0.0
            est_exit_stamp_duty = est_exit_turnover * cls.STAMP_DUTY_BUY_RATE
            est_exit_stt = 0.0

        total_est_turnover = entry_turnover + est_exit_turnover
        brokerage = 2.0 * brokerage_rate
        total_stt = round(entry_stt + est_exit_stt, 2)
        total_stamp_duty = round(entry_stamp_duty + est_exit_stamp_duty, 2)
        exchange_charges = round(total_est_turnover * cls.EXCHANGE_TURNOVER_RATE, 2)
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
