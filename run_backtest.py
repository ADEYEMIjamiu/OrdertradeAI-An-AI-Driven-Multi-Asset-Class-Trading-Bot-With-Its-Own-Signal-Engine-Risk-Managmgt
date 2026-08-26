"""
CLI entry point for engines/backtest_engine.py -- run the AI decision
pipeline against historical data instead of live/paper trading.

Usage examples:

    python3 run_backtest.py --tickers AAPL,MSFT,GOOGL --start 2023-01-01 --end 2025-01-01
    python3 run_backtest.py --tickers BTC-USD,ETH-USD --start 2022-01-01 --end 2024-01-01 --balance 5000
    python3 run_backtest.py --tickers EURUSD=X,GC=F --start 2023-06-01 --end 2025-06-01 --leverage 10 --verbose

Requires models/trading_model.pkl + models/features.pkl to already exist
(same as live trading -- run train_model.py first if they're missing).
Downloads historical OHLCV via yfinance once per ticker on first run and
caches it under backtest_cache/ (gitignored) -- reruns over the same
ticker/date range are instant and make no network calls.

See engines/backtest_engine.py's module docstring for the full list of
known divergences from live trading (most importantly: Trend Score is
daily-only here, not the live 1d+1h+15m blend, since Yahoo Finance
doesn't retain years of intraday history).
"""

import argparse
import csv
import os
from datetime import datetime

from engines.backtest_engine import run_backtest


def _parse_args():
    parser = argparse.ArgumentParser(description="Backtest the AI decision pipeline against historical data.")
    parser.add_argument("--tickers", required=True, help="Comma-separated tickers, e.g. AAPL,MSFT,BTC-USD")
    parser.add_argument("--start", required=True, help="Start date, YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="End date, YYYY-MM-DD")
    parser.add_argument("--balance", type=float, default=10000.0, help="Starting account balance (default: 10000)")
    parser.add_argument("--leverage", type=float, default=1.0, help="Leverage applied to position sizing (default: 1, use 10 to model eToro FOREX/COMMODITIES CFDs)")
    parser.add_argument("--max-open", type=int, default=None, help="Max concurrent open positions (default: unlimited)")
    parser.add_argument("--verbose", action="store_true", help="Print every signal/rejection/fill as it happens")
    parser.add_argument("--out", default=None, help="Optional path to write closed trades as CSV (default: backtest_results/<timestamp>_trades.csv)")
    return parser.parse_args()


def _fmt(value, pct=False, money=False):
    if value is None:
        return "N/A"
    if pct:
        return f"{value:.2f}%"
    if money:
        return f"${value:,.2f}"
    return f"{value:.3f}"


def main():
    args = _parse_args()
    tickers = [t.strip().upper() for t in args.tickers.split(",") if t.strip()]

    print(f"Running backtest: {', '.join(tickers)} | {args.start} -> {args.end} | "
          f"balance=${args.balance:,.2f} | leverage={args.leverage}x")
    print("(First run per ticker/date-range downloads and caches historical data -- "
          "may take a moment.)\n")

    result = run_backtest(
        tickers=tickers,
        start=args.start,
        end=args.end,
        initial_balance=args.balance,
        leverage=args.leverage,
        max_open_positions=args.max_open,
        verbose=args.verbose,
    )

    m = result["metrics"]
    regime, regime_score = result["final_regime"]

    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Starting balance:   ${args.balance:,.2f}")
    print(f"Final equity:       ${result['final_equity']:,.2f}")
    total_return_pct = ((result["final_equity"] / args.balance) - 1) * 100
    print(f"Total return:       {total_return_pct:+.2f}%")
    print(f"Final SPY regime:   {regime} (score={regime_score})")
    print("-" * 60)
    print(f"Trades closed:      {m['trades_closed']} ({m['wins']} wins / {m['losses']} losses)")
    print(f"Win rate:           {_fmt(m['win_rate'], pct=True)}")
    print(f"Total P&L:          {_fmt(m['total_pnl'], money=True)}")
    print(f"Gross profit/loss:  {_fmt(m['gross_profit'], money=True)} / {_fmt(m['gross_loss'], money=True)}")
    print(f"Profit factor:      {_fmt(m['profit_factor'])}")
    print(f"Expectancy/trade:   {_fmt(m['expectancy'], money=True)}")
    print(f"Average win/loss:   {_fmt(m['average_win'], money=True)} / {_fmt(m['average_loss'], money=True)}")
    print(f"Max drawdown:       {_fmt(m['max_drawdown'], money=True)}")
    print(f"Sharpe (per-trade): {_fmt(m['sharpe_ratio'])}")
    print(f"Sortino (per-trade):{_fmt(m['sortino_ratio'])}")
    print("=" * 60)

    if result["trades"]:
        out_path = args.out
        if out_path is None:
            os.makedirs("backtest_results", exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            out_path = os.path.join("backtest_results", f"{ts}_trades.csv")

        fieldnames = list(result["trades"][0].keys())
        with open(out_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(result["trades"])
        print(f"\nClosed trades written to: {out_path}")
    else:
        print("\nNo trades were closed in this window -- nothing to write.")


if __name__ == "__main__":
    main()
