"""
Systematic sweep of stop-loss/take-profit placement, same train/test
discipline as tune_backtest.py -- the natural next lever after that sweep
proved MIN_TRADE_CONFIDENCE/MIN_RISK_REWARD_RATIO were already about as
good as anything nearby (none of 16 combos beat the 45/1.0 baseline
out-of-sample). This sweeps HOW stops/targets are placed instead of
WHETHER a trade clears the approval gate:

  - ATR_STOP_MULTIPLIER: how many ATRs away the stop-loss sits. Wider =
    fewer stop-outs but a bigger loss on the ones that do; tighter =
    more stop-outs but each one smaller.
  - TRADE_PLAN_LOOKBACK_DAYS: how many days back the swing-high/low
    take-profit target is measured from. Shorter = closer, easier
    targets; longer = further, harder-to-reach targets but bigger
    winners when hit.

IMPORTANT -- WHY THIS MONKEY-PATCHES engines.backtest_engine, NOT
engines.trade_planner: unlike approval_engine.py (which the backtest
calls unmodified), trade_planner.create_trade_plan() is one of the three
functions with an OFFLINE COUNTERPART here -- backtest_engine.py's
offline_create_trade_plan() is a separate, line-for-line copy that does
`from config import ATR_STOP_MULTIPLIER, TRADE_PLAN_LOOKBACK_DAYS` into
ITS OWN module namespace and references those bare names directly.
Patching engines.trade_planner.ATR_STOP_MULTIPLIER would only affect
live trading's real create_trade_plan() -- which the backtest never
calls -- and have zero effect here. This script patches
engines.backtest_engine.ATR_STOP_MULTIPLIER /
engines.backtest_engine.TRADE_PLAN_LOOKBACK_DAYS instead, which is what
offline_create_trade_plan() actually reads.

MIN_TRADE_CONFIDENCE/MIN_RISK_REWARD_RATIO are left at their
already-validated defaults (45/1.0) throughout this sweep -- isolating
one set of levers at a time keeps each sweep's conclusion unambiguous.

Usage:
    python3 tune_stop_targets.py
    python3 tune_stop_targets.py --top-n 5 --out tuning_results/my_sweep.csv
"""

import argparse
import csv
import os
from datetime import datetime

import engines.backtest_engine as backtest_engine
from engines.backtest_engine import run_backtest

TUNING_TICKERS = [
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "JPM", "KO", "XOM", "V",
    "BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "LINK-USD", "AVAX-USD",
    "EURUSD=X", "GBPUSD=X",
    "GC=F", "CL=F",
]

IN_SAMPLE_START = "2020-01-01"
IN_SAMPLE_END = "2023-01-01"
OUT_OF_SAMPLE_START = "2023-01-01"
OUT_OF_SAMPLE_END = "2025-01-01"

ATR_STOP_MULTIPLIER_GRID = [1.0, 1.5, 2.0, 2.5]
TRADE_PLAN_LOOKBACK_DAYS_GRID = [10, 20, 30, 40]

MIN_TRADES_FOR_CONSIDERATION = 30


def _run_one(atr_multiplier, lookback_days, start, end):
    backtest_engine.ATR_STOP_MULTIPLIER = atr_multiplier
    backtest_engine.TRADE_PLAN_LOOKBACK_DAYS = lookback_days
    result = run_backtest(
        tickers=TUNING_TICKERS,
        start=start,
        end=end,
        initial_balance=10000.0,
        leverage=1,
        verbose=False,
    )
    m = result["metrics"]
    total_return_pct = ((result["final_equity"] / 10000.0) - 1) * 100
    return {
        "atr_stop_multiplier": atr_multiplier,
        "trade_plan_lookback_days": lookback_days,
        "total_return_pct": total_return_pct,
        "final_equity": result["final_equity"],
        "trades_closed": m["trades_closed"],
        "win_rate": m["win_rate"],
        "profit_factor": m["profit_factor"],
        "expectancy": m["expectancy"],
        "max_drawdown": m["max_drawdown"],
        "sharpe_ratio": m["sharpe_ratio"],
        "sortino_ratio": m["sortino_ratio"],
    }


def _composite_score(row):
    if row["trades_closed"] < MIN_TRADES_FOR_CONSIDERATION:
        return float("-inf")
    if row["profit_factor"] is None or row["profit_factor"] <= 1.0:
        return float("-inf")
    sharpe = row["sharpe_ratio"] or 0.0
    return sharpe * 10 + (row["profit_factor"] - 1.0)


def _fmt_row(row, label=""):
    marker = " [CURRENT DEFAULT]" if row["atr_stop_multiplier"] == 1.5 and row["trade_plan_lookback_days"] == 20 else ""
    return (
        f"{label}ATR_mult={row['atr_stop_multiplier']:<4} lookback={row['trade_plan_lookback_days']:<4} | "
        f"trades={row['trades_closed']:<5} win%={row['win_rate']:>6.2f} "
        f"PF={row['profit_factor']:>6.3f} Sharpe={row['sharpe_ratio']:>6.3f} "
        f"Sortino={row['sortino_ratio']:>6.3f} return={row['total_return_pct']:>+8.2f}%"
        f"{marker}"
    )


def main():
    parser = argparse.ArgumentParser(description="Sweep stop-loss/take-profit placement, validate winners out-of-sample.")
    parser.add_argument("--top-n", type=int, default=3)
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    grid = [(a, l) for a in ATR_STOP_MULTIPLIER_GRID for l in TRADE_PLAN_LOOKBACK_DAYS_GRID]
    print(f"IN-SAMPLE sweep: {len(grid)} combos | {IN_SAMPLE_START} -> {IN_SAMPLE_END} | "
          f"{len(TUNING_TICKERS)} tickers | MIN_TRADE_CONFIDENCE=45, MIN_RISK_REWARD_RATIO=1.0 held fixed\n")

    in_sample_results = []
    for i, (atr_multiplier, lookback_days) in enumerate(grid, 1):
        print(f"[{i}/{len(grid)}] Running ATR_mult={atr_multiplier} lookback={lookback_days} ...", flush=True)
        row = _run_one(atr_multiplier, lookback_days, IN_SAMPLE_START, IN_SAMPLE_END)
        in_sample_results.append(row)
        print("  " + _fmt_row(row))

    print("\n" + "=" * 90)
    print("IN-SAMPLE RESULTS -- ranked by composite score")
    print("=" * 90)
    ranked = sorted(in_sample_results, key=_composite_score, reverse=True)
    for i, row in enumerate(ranked, 1):
        print(f"#{i}  " + _fmt_row(row))

    if args.out:
        out_path = args.out
    else:
        os.makedirs("tuning_results", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join("tuning_results", f"{ts}_stop_target_sweep.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(in_sample_results[0].keys()))
        writer.writeheader()
        writer.writerows(in_sample_results)
    print(f"\nFull in-sample sweep written to: {out_path}")

    top_candidates = [r for r in ranked if _composite_score(r) > float("-inf")][:args.top_n]
    if not top_candidates:
        print("\nNo candidate cleared the minimum trade count / profit factor bar -- nothing to validate.")
        return

    print("\n" + "=" * 90)
    print(f"OUT-OF-SAMPLE VALIDATION -- top {len(top_candidates)} in-sample candidate(s), "
          f"re-run on data they were never tuned against ({OUT_OF_SAMPLE_START} -> {OUT_OF_SAMPLE_END})")
    print("=" * 90)

    baseline_oos = _run_one(1.5, 20, OUT_OF_SAMPLE_START, OUT_OF_SAMPLE_END)
    print("BASELINE (current config.py defaults) out-of-sample:")
    print("  " + _fmt_row(baseline_oos))

    oos_records = [dict(baseline_oos, label="BASELINE", holds_up=None)]

    for i, candidate in enumerate(top_candidates, 1):
        atr_multiplier = candidate["atr_stop_multiplier"]
        lookback_days = candidate["trade_plan_lookback_days"]
        print(f"\nCandidate #{i}: ATR_mult={atr_multiplier} lookback={lookback_days}")
        print("  in-sample:     " + _fmt_row(candidate))
        oos_row = _run_one(atr_multiplier, lookback_days, OUT_OF_SAMPLE_START, OUT_OF_SAMPLE_END)
        print("  out-of-sample: " + _fmt_row(oos_row))
        holds_up = (
            oos_row["profit_factor"] > baseline_oos["profit_factor"]
            and oos_row["sharpe_ratio"] > baseline_oos["sharpe_ratio"]
        )
        verdict = "HOLDS UP vs baseline out-of-sample -- worth adopting" if holds_up else \
                  "does NOT clearly beat baseline out-of-sample -- likely overfit to in-sample window, do not adopt"
        print(f"  verdict: {verdict}")
        oos_records.append(dict(oos_row, label=f"candidate_{i}", holds_up=holds_up))

    print("=" * 90)

    oos_out_path = out_path.replace(".csv", "_out_of_sample.csv") if out_path.endswith(".csv") \
        else out_path + "_out_of_sample.csv"
    with open(oos_out_path, "w", newline="") as f:
        fieldnames = list(oos_records[0].keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(oos_records)
    print(f"Out-of-sample validation written to: {oos_out_path}")


if __name__ == "__main__":
    main()
