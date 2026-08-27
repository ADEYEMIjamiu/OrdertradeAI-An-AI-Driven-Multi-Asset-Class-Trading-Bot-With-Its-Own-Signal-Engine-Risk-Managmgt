"""
One-off analysis script -- breaks the buy-and-hold benchmark down by asset
class and shows the single biggest movers, instead of just the blended
average run_backtest.py prints. Uses the already-cached historical data in
backtest_cache/ (populated by your last --universe all run), so this makes
no network calls and runs in a couple seconds.

Usage:
    python3 analyze_benchmark_breakdown.py --start 2020-01-01 --end 2025-01-01
"""

import argparse
import glob
import os
from collections import defaultdict

import pandas as pd

from data.asset_universe import get_enabled_symbols


def _parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    return parser.parse_args()


def main():
    args = _parse_args()
    start_ts = pd.Timestamp(args.start)
    end_ts = pd.Timestamp(args.end)

    assets = get_enabled_symbols()
    per_ticker = {}
    ticker_to_class = {}

    for asset in assets:
        ticker = asset["symbol"]
        ticker_to_class[ticker] = asset["asset_class"]
        safe = ticker.replace("/", "_").replace("=", "_")
        matches = glob.glob(os.path.join("backtest_cache", f"{safe}_*_{args.end}.pkl"))
        if not matches:
            print(f"  [no cache] {ticker} -- skipping (not found in backtest_cache/)")
            continue

        df = pd.read_pickle(matches[0])
        window = df[(df.index >= start_ts) & (df.index <= end_ts)]
        if len(window) < 2:
            continue

        entry_price = float(window["Close"].iloc[0])
        exit_price = float(window["Close"].iloc[-1])
        if entry_price > 0:
            per_ticker[ticker] = (exit_price / entry_price - 1) * 100

    by_class = defaultdict(list)
    for ticker, ret in per_ticker.items():
        by_class[ticker_to_class[ticker]].append((ticker, ret))

    print("\n" + "=" * 60)
    print("BUY-AND-HOLD BENCHMARK -- BROKEN DOWN BY ASSET CLASS")
    print("=" * 60)
    print(f"{'Asset Class':<14}{'#Tickers':>10}{'Avg Return %':>16}")

    overall = []
    for asset_class, items in sorted(by_class.items()):
        rets = [r for _, r in items]
        avg = sum(rets) / len(rets)
        overall.extend(rets)
        print(f"{asset_class:<14}{len(items):>10}{avg:>15.1f}%")

    if overall:
        print(f"{'OVERALL':<14}{len(overall):>10}{sum(overall) / len(overall):>15.1f}%")

    print("\nTop 10 individual movers (buy-and-hold over the window):")
    for ticker, ret in sorted(per_ticker.items(), key=lambda x: -x[1])[:10]:
        print(f"  {ticker:<12} {ret:>14,.1f}%   ({ticker_to_class[ticker]})")

    print("\nBottom 10 individual movers (buy-and-hold over the window):")
    for ticker, ret in sorted(per_ticker.items(), key=lambda x: x[1])[:10]:
        print(f"  {ticker:<12} {ret:>14,.1f}%   ({ticker_to_class[ticker]})")

    print("=" * 60)


if __name__ == "__main__":
    main()
