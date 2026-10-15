"""
Main Execution Engine Entrypoint.
Orchestrates:
  [Data Feeder: HistoricalDB / LiveStream]
               │
               ▼
  [Strategy: Core85Strategy]
               │
               ▼
  [Execution Adapter: Backtest / Paper / Live]
"""
import argparse
import sys
import os
from pathlib import Path
import pandas as pd
import numpy as np

# Ensure trading_engine directory is on sys.path
ENGINE_DIR = Path(__file__).resolve().parent
if str(ENGINE_DIR) not in sys.path:
    sys.path.insert(0, str(ENGINE_DIR))

from config import EngineConfig
from feeders.historical_db import HistoricalDBFeeder
from feeders.live_stream import LiveStreamFeeder
from adapters.backtest_adapter import BacktestAdapter
from adapters.paper_adapter import PaperAdapter
from adapters.live_adapter import LiveAdapter
from strategy.core_85_strategy import Core85Strategy

def main():
    parser = argparse.ArgumentParser(description="Modular Autonomous Trading Engine")
    parser.add_argument("--mode", choices=["backtest", "paper", "live"], default="backtest",
                        help="Execution mode (backtest, paper, live)")
    parser.add_argument("--capital", type=float, default=100000.0,
                        help="Initial Capital in INR (Default: 1,00,000)")
    args = parser.parse_args()

    cfg = EngineConfig(initial_capital=args.capital)

    print("=" * 85)
    print(f"  INITIALIZING TRADING ENGINE | MODE: {args.mode.upper()}")
    print(f"  Symbol: {cfg.symbol} | Capital: ₹{cfg.initial_capital:,.2f} | Lot Size: {cfg.lot_size}")
    print(f"  Target: {cfg.target_atr_mult:.2f}x ATR | Stop: {cfg.stop_atr_mult:.2f}x ATR | Architecture: Core 85.51% WinRate")
    print("=" * 85, flush=True)

    # 1. Backtest Mode Execution (Benchmark 2.86 Crore Architecture)
    if args.mode == "backtest":
        sys.path.insert(0, str(ENGINE_DIR.parent))
        from run_2cr_benchmark import run_benchmark
        run_benchmark(max_lots_list=[cfg.max_lots])
        return

    # 1. Initialize Data Feeder (Paper / Live)
    feeder = LiveStreamFeeder(cfg)
    feeder.initialize()

    # 2. Initialize Adapter
    if args.mode == "backtest":
        adapter = BacktestAdapter(cfg)
    elif args.mode == "paper":
        adapter = PaperAdapter(cfg)
    else:
        adapter = LiveAdapter(cfg)

    # Register sub-second live tick listener if live feeder & adapter support it
    if hasattr(feeder, "register_tick_listener") and hasattr(adapter, "update_tick"):
        feeder.register_tick_listener(adapter.update_tick)

    # 3. Initialize Strategy & Parallel Strategy Detector
    from adapters.strategy_detector_adapter import StrategyDetectorAdapter
    detector = StrategyDetectorAdapter(cfg)
    strategy = Core85Strategy(cfg)
    spot_tr, feat_tr, tr_idx = feeder.get_training_data()
    strategy.train_on_history(spot_tr, feat_tr, tr_idx)

    if args.mode == "backtest":
        print("Pre-computing vectorized probabilities for backtest...", flush=True)
        strategy.precompute_batch(feeder.feature_df, feeder.test_indices)

    # 4. Run Execution Loop
    print("\nStarting execution loop...", flush=True)
    bar_count = 0
    current_month = None

    for bar in feeder.stream_bars():
        bar_count += 1

        # Walk-forward rolling monthly retraining
        if getattr(cfg, "retrain_monthly", False) and args.mode == "backtest":
            bar_month = bar["timestamp"].strftime("%Y-%m")
            if current_month is None:
                current_month = bar_month
            elif bar_month != current_month:
                current_month = bar_month
                print(f"\n[WALK-FORWARD RETRAINING] Rolling model forward for {bar_month}...", flush=True)
                spot_tr, feat_tr, tr_idx = feeder.get_training_data_up_to(bar["idx"])
                strategy.train_on_history(spot_tr, feat_tr, tr_idx)
                rem_indices = feeder.test_indices[feeder.test_indices >= bar["idx"]]
                strategy.precompute_batch(feeder.feature_df, rem_indices)
                print(f"[WALK-FORWARD RETRAINING] {bar_month} active. Resuming execution loop...\n", flush=True)
        
        # A. Check existing position updates (Target, SL, Multi-step ladder)
        exit_rec = adapter.update_position(bar)
        if exit_rec is not None:
            strategy.notify_exit(exit_rec)

        # B. Check Parallel Strategy Detections (AMD, Structural Sweeps)
        detected_signals = detector.evaluate_candle_parallel(bar)

        # C. If no active position, evaluate entries
        if adapter.active_position is None:
            if detected_signals:
                top_sig = max(detected_signals, key=lambda s: s.confidence_score)
                adapter.open_position(
                    symbol=top_sig.symbol,
                    side=top_sig.side,
                    price=top_sig.trigger_price,
                    atr=bar.get("atr", 40.0),
                    timestamp=bar["timestamp"],
                    is_sar=False
                )
            else:
                sig = strategy.evaluate_bar(bar)
                if sig is not None:
                    side, atr, is_sar = sig
                    adapter.open_position(
                        symbol=cfg.symbol,
                        side=side,
                        price=bar["close"],
                        atr=atr,
                        timestamp=bar["timestamp"],
                        is_sar=is_sar
                    )

    print(f"\nExecution loop complete. Processed {bar_count:,} bars.")

    # 5. Generate Comprehensive & Month-Wise Report
    summary = adapter.get_portfolio_summary()
    trades_df = summary.get("trades_df")

    if trades_df is not None and not trades_df.empty:
        print("\n" + "=" * 85)
        print("  MONTH-WISE PERFORMANCE REPORT (₹1,00,000 CAPITAL)")
        print("=" * 85)

        months = trades_df["month"].unique()
        month_records = []
        running_capital = cfg.initial_capital

        for m in sorted(months):
            m_trades = trades_df[trades_df["month"] == m]
            m_tot = len(m_trades)
            m_wins = (m_trades["is_win"] == True).sum()
            m_losses = m_tot - m_wins
            m_wr = (m_wins / m_tot) * 100.0 if m_tot > 0 else 0.0

            m_gross_pnl = m_trades["net_pnl_inr"].sum()
            start_cap = running_capital
            end_cap = start_cap + m_gross_pnl
            running_capital = end_cap
            m_ret_pct = (m_gross_pnl / start_cap) * 100.0
            spot_pts_sum = m_trades["gross_pts"].sum()

            month_records.append({
                "Month": m,
                "Trades": m_tot,
                "Wins": m_wins,
                "Losses": m_losses,
                "WinRate (%)": round(m_wr, 2),
                "Spot Pts Sum": round(spot_pts_sum, 2),
                "Start Cap (₹)": round(start_cap, 2),
                "Net PnL (₹)": round(m_gross_pnl, 2),
                "End Cap (₹)": round(end_cap, 2),
                "Return (%)": round(m_ret_pct, 2)
            })

        m_report_df = pd.DataFrame(month_records)
        print(m_report_df.to_string(index=False))

        print("\n" + "=" * 85)
        print("  OVERALL 6-MONTH PORTFOLIO SUMMARY")
        print("=" * 85)
        print(f"  Initial Capital:       ₹{summary['initial_capital']:,.2f}")
        print(f"  Final Capital:         ₹{summary['final_capital']:,.2f}")
        print(f"  Net Profit:            ₹{summary['net_profit']:+,.2f}")
        print(f"  Total Return:          {summary['return_pct']:+.2f}%")
        print(f"  Total Executions:      {summary['total_trades']} (Initial + SAR Traps)")
        print(f"  Individual Win Rate:   {summary['win_rate']:.2f}% ({summary['wins']} Wins / {summary['losses']} Losses)")
        print(f"  Strategy Series Setups: {summary['total_series']}")
        print(f"  Series Win Rate:       {summary['series_win_rate']:.2f}% ({summary['series_wins']} Won / {summary['series_losses']} Lost)")
        print("=" * 85)

        m_report_df.to_csv("month_wise_backtest_report.csv", index=False)
        trades_df.to_csv("all_executed_trades.csv", index=False)
        print("\nSaved month_wise_backtest_report.csv and all_executed_trades.csv")

if __name__ == "__main__":
    main()
