"""
Backtest Engine: Performance Scorecard & Metric Compiler.
─────────────────────────────────────────────────────────
Compiles standard quantitative metrics, risk ratios, drawdowns,
equity curve time-series, and ledger tables.
Compatible with Web App Backtest Page, VS Code Extension, and Dashboard Execution Log.
"""
from datetime import datetime
from typing import Dict, Any, List, Optional
import math
import numpy as np

from trading_engine.quant_core.contracts import TradeRecord


class ScorecardGenerator:
    """
    Computes rigorous trading performance statistics from executed TradeRecords.
    """

    @classmethod
    def generate(
        cls,
        trades: List[TradeRecord],
        equity_curve: List[Dict[str, Any]],
        initial_capital: float,
        parameters: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Builds complete scorecard dictionary.
        """
        init_cap = max(1000.0, float(initial_capital))
        total_trades = len(trades)

        if total_trades == 0:
            empty_bif = {
                "brokerage": 0.0, "stt": 0.0, "exchange_txn": 0.0,
                "gst": 0.0, "stamp_duty": 0.0, "sebi": 0.0
            }
            empty_sc = {
                "win_rate_pct": 0.0, "total_trades": 0, "winning_trades": 0,
                "losing_trades": 0, "total_pnl_inr": 0.0, "gross_pnl_inr": 0.0,
                "total_charges_inr": 0.0, "total_pnl_points": 0.0,
                "profit_factor": 0.0, "max_drawdown_pct": 0.0, "max_drawdown_inr": 0.0,
                "charges_bifurcation": empty_bif, "trades": []
            }
            return {
                "summary": {
                    "netPnl": 0.0,
                    "netPnlFormatted": "₹0.00",
                    "grossPnl": 0.0,
                    "points": 0.0,
                    "totalPoints": 0.0,
                    "roiPct": 0.0,
                    "roi": 0.0,
                    "winRate": 0.0,
                    "totalTrades": 0,
                    "wins": 0,
                    "losses": 0,
                    "winningTrades": 0,
                    "losingTrades": 0,
                    "profitFactor": 0.0,
                    "maxDrawdown": 0.0,
                    "maxDrawdownPct": 0.0,
                    "charges": 0.0,
                    "totalCharges": 0.0,
                    "avgTrade": 0.0,
                    "avgWin": 0.0,
                    "avgLoss": 0.0,
                    "riskReward": "1 : 0.00",
                    "capital": init_cap
                },
                "scorecard": empty_sc,
                "win_rate_pct": 0.0,
                "total_trades": 0,
                "winning_trades": 0,
                "losing_trades": 0,
                "total_pnl_inr": 0.0,
                "gross_pnl_inr": 0.0,
                "total_charges_inr": 0.0,
                "total_pnl_points": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "max_drawdown_inr": 0.0,
                "charges_bifurcation": empty_bif,
                "executionLogEntry": {
                    "id": f"BT_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                    "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "dateRange": f"{parameters.get('dateFrom', '')} to {parameters.get('dateTo', '')}",
                    "symbol": parameters.get("symbol", "NIFTY50"),
                    "strategy": parameters.get("strategy", "BSS Pro v5"),
                    "capital": f"₹{int(init_cap):,}",
                    "trades": 0,
                    "winRate": "0.0%",
                    "netPnl": "+₹0.00",
                    "roi": "0.0%",
                    "status": "COMPLETED"
                },
                "trades": [],
                "equityCurve": equity_curve,
                "parameters": parameters
            }

        # Calculations
        net_pnl = sum(t.net_pnl for t in trades)
        gross_pnl = sum(t.gross_pnl for t in trades)
        total_charges = sum(t.charges for t in trades)
        total_points = sum(t.points for t in trades)

        wins_list = [t for t in trades if t.net_pnl > 0]
        losses_list = [t for t in trades if t.net_pnl <= 0]
        n_wins = len(wins_list)
        n_losses = len(losses_list)
        win_rate = (n_wins / total_trades * 100.0) if total_trades > 0 else 0.0

        gross_wins = sum(t.gross_pnl for t in wins_list)
        gross_losses = abs(sum(t.gross_pnl for t in losses_list))
        profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else (99.0 if gross_wins > 0 else 0.0)

        avg_trade = net_pnl / total_trades
        avg_win = (sum(t.net_pnl for t in wins_list) / n_wins) if n_wins > 0 else 0.0
        avg_loss = (abs(sum(t.net_pnl for t in losses_list)) / n_losses) if n_losses > 0 else 0.0
        rr_ratio = (avg_win / avg_loss) if avg_loss > 0 else 0.0

        # Drawdown calculation across trade equity curve
        peak = init_cap
        max_dd = 0.0
        max_dd_pct = 0.0
        running_cap = init_cap
        for t in trades:
            running_cap += t.net_pnl
            if running_cap > peak:
                peak = running_cap
            dd = peak - running_cap
            if dd > max_dd:
                max_dd = dd
            if peak > 0:
                dd_pct = (dd / peak) * 100.0
                if dd_pct > max_dd_pct:
                    max_dd_pct = dd_pct
        roi_pct = (net_pnl / init_cap * 100.0)

        # Net PnL string formatting
        sign_str = "+" if net_pnl >= 0 else ""
        net_str = f"{sign_str}₹{net_pnl:,.2f}"

        trade_dicts = [t.to_dict() for t in trades]

        summary = {
            "netPnl": round(net_pnl, 2),
            "netPnlFormatted": net_str,
            "grossPnl": round(gross_pnl, 2),
            "points": round(total_points, 2),
            "totalPoints": round(total_points, 2),
            "roiPct": round(roi_pct, 2),
            "roi": round(roi_pct, 2),
            "winRate": round(win_rate, 1),
            "totalTrades": total_trades,
            "wins": n_wins,
            "losses": n_losses,
            "winningTrades": n_wins,
            "losingTrades": n_losses,
            "profitFactor": round(profit_factor, 2),
            "maxDrawdown": round(max_dd, 2),
            "maxDrawdownPct": round(max_dd_pct, 1),
            "charges": round(total_charges, 2),
            "totalCharges": round(total_charges, 2),
            "avgTrade": round(avg_trade, 2),
            "avgWin": round(avg_win, 2),
            "avgLoss": round(avg_loss, 2),
            "riskReward": f"1 : {rr_ratio:.2f}",
            "capital": init_cap,
            "finalBalance": round(init_cap + net_pnl, 2),
            "final_balance": round(init_cap + net_pnl, 2)
        }

        execution_log_entry = {
            "id": f"BT_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "dateRange": f"{parameters.get('dateFrom', '')} to {parameters.get('dateTo', '')}",
            "symbol": parameters.get("symbol", "NIFTY50"),
            "strategy": parameters.get("strategy", "BSS Pro v5"),
            "capital": f"₹{int(init_cap):,}",
            "trades": total_trades,
            "winRate": f"{win_rate:.1f}%",
            "netPnl": net_str,
            "roi": f"{sign_str}{roi_pct:.2f}%",
            "status": "COMPLETED"
        }

        # Statutory charges bifurcation
        total_brokerage = sum(t.charges_breakdown.brokerage for t in trades if t.charges_breakdown)
        total_stt = sum(t.charges_breakdown.stt for t in trades if t.charges_breakdown)
        total_exchange = sum(t.charges_breakdown.exchange_txn_fee for t in trades if t.charges_breakdown)
        total_gst = sum(t.charges_breakdown.gst for t in trades if t.charges_breakdown)
        total_stamp = sum(t.charges_breakdown.stamp_duty for t in trades if t.charges_breakdown)
        total_sebi = sum(t.charges_breakdown.sebi_fee for t in trades if t.charges_breakdown)

        bifurcation = {
            "brokerage": round(total_brokerage, 2),
            "stt": round(total_stt, 2),
            "exchange_txn": round(total_exchange, 2),
            "gst": round(total_gst, 2),
            "stamp_duty": round(total_stamp, 2),
            "sebi": round(total_sebi, 2)
        }

        scorecard_dict = {
            "win_rate_pct": round(win_rate, 1),
            "total_trades": total_trades,
            "winning_trades": n_wins,
            "losing_trades": n_losses,
            "total_pnl_inr": round(net_pnl, 2),
            "gross_pnl_inr": round(gross_pnl, 2),
            "total_charges_inr": round(total_charges, 2),
            "total_pnl_points": round(total_points, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd_pct, 1),
            "max_drawdown_inr": round(max_dd, 2),
            "capital": init_cap,
            "final_balance": round(init_cap + net_pnl, 2),
            "finalBalance": round(init_cap + net_pnl, 2),
            "charges_bifurcation": bifurcation,
            "trades": trade_dicts
        }

        return {
            "summary": summary,
            "scorecard": scorecard_dict,
            "capital": init_cap,
            "final_balance": round(init_cap + net_pnl, 2),
            "finalBalance": round(init_cap + net_pnl, 2),
            "win_rate_pct": round(win_rate, 1),
            "total_trades": total_trades,
            "winning_trades": n_wins,
            "losing_trades": n_losses,
            "total_pnl_inr": round(net_pnl, 2),
            "gross_pnl_inr": round(gross_pnl, 2),
            "total_charges_inr": round(total_charges, 2),
            "total_pnl_points": round(total_points, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_dd_pct, 1),
            "max_drawdown_inr": round(max_dd, 2),
            "charges_bifurcation": bifurcation,
            "executionLogEntry": execution_log_entry,
            "trades": trade_dicts,
            "equityCurve": equity_curve,
            "parameters": parameters
        }
