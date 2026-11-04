"""
Quant Core: Indian Derivatives Statutory Cost & Brokerage Calculator.
─────────────────────────────────────────────────────────────────────
Calculates exact exchange fees, taxes, and statutory levies for Indian
F&O contracts (NSE / BSE / MCX). Shared identically by Backtest and Live.
"""
from typing import Optional
try:
    from .contracts import StatutoryCharges
except ImportError:
    from contracts import StatutoryCharges


class CostCalculator:
    """
    Standardized statutory charges for Indian equity derivatives (F&O).
    Rates reflect FY2024-2027 statutory mandates:
      - Brokerage: ₹20 / order leg per slice
      - STT: 0.125% on option sell turnover (premium * units)
      - Exchange Txn Fee: 0.0505% on option premium turnover (NSE)
      - SEBI Turnover Fee: 0.0001% (₹10 / crore)
      - Stamp Duty: 0.003% on buy side premium turnover
      - GST: 18% on (Brokerage + Exchange Txn Fee + SEBI Fee)
    """

    STT_OPTION_SELL_PCT = 0.00125       # 0.125%
    EXCHANGE_TXN_FEE_PCT = 0.000505    # 0.0505%
    SEBI_FEE_PCT = 0.000001            # ₹10 per crore
    STAMP_DUTY_BUY_PCT = 0.00003       # 0.003%
    GST_PCT = 0.18                     # 18%

    @classmethod
    def calculate_charges(
        cls,
        entry_price: float,
        exit_price: float,
        units: int,
        n_slices: int = 1,
        broker_per_slice: float = 20.0,
        is_option: bool = True
    ) -> StatutoryCharges:
        """
        Computes statutory fee breakdown for a complete round-trip trade.
        
        Args:
            entry_price: Buy/entry price in ₹
            exit_price: Sell/exit price in ₹
            units: Total quantity executed
            n_slices: Number of order slice legs executed
            broker_per_slice: Brokerage per executed order slice
            is_option: True for option premium turnover, False for futures
        """
        # Ensure positive values
        entry_px = max(0.05, float(entry_price))
        exit_px = max(0.05, float(exit_price))
        qty = max(1, int(units))
        slices = max(1, int(n_slices))

        buy_turnover = entry_px * qty
        sell_turnover = exit_px * qty
        total_turnover = buy_turnover + sell_turnover

        # 1. Brokerage (Entry leg + Exit leg for each slice)
        brokerage = 2.0 * broker_per_slice * slices

        if is_option:
            # 2. STT: 0.125% on option sell premium
            stt = sell_turnover * cls.STT_OPTION_SELL_PCT

            # 3. Exchange Turnover Charge: 0.0505% on total premium turnover
            exchange_fee = total_turnover * cls.EXCHANGE_TXN_FEE_PCT

            # 4. Stamp Duty: 0.003% on buy premium turnover
            stamp_duty = buy_turnover * cls.STAMP_DUTY_BUY_PCT
        else:
            # Futures STT (0.0125% on sell)
            stt = sell_turnover * 0.000125
            exchange_fee = total_turnover * 0.000019
            stamp_duty = buy_turnover * 0.00002

        # 5. SEBI Turnover Fee (₹10 / crore on total turnover)
        sebi_fee = total_turnover * cls.SEBI_FEE_PCT

        # 6. GST: 18% on (Brokerage + Exchange Txn Fee + SEBI Fee)
        gst = (brokerage + exchange_fee + sebi_fee) * cls.GST_PCT

        total = brokerage + stt + exchange_fee + sebi_fee + stamp_duty + gst

        return StatutoryCharges(
            brokerage=round(brokerage, 2),
            stt=round(stt, 2),
            exchange_txn_fee=round(exchange_fee, 2),
            sebi_fee=round(sebi_fee, 2),
            stamp_duty=round(stamp_duty, 2),
            gst=round(gst, 2),
            total=round(total, 2)
        )
