"""
Systematic threshold tuning for the AI decision pipeline, validated against
data it was never tuned on -- built as the third and final part of the
backtesting improvement plan (benchmark -> full-universe -> this).

WHY THIS EXISTS
----------------
The full-universe backtest (run_backtest.py --universe all, 2020-2025)
showed a real but thin edge: profit factor 1.31, Sharpe 0.08 (per-trade),
Sortino 0.14. Positive, but weak. approval_engine.py's MIN_TRADE_CONFIDENCE
(45) and MIN_RISK_REWARD_RATIO (1.0) were set by judgment when they were
first wired up, never systematically tested against alternatives. This
sweeps a grid of both, ranks candidates on an IN-SAMPLE window, then
re-validates only the top candidates on a completely separate
OUT-OF-SAMPLE window they were never tuned against. A config only gets
recommended if it holds up on both -- this is the standard train/test
discipline for avoiding curve-fitting a config to one specific historical
window and calling it "improved" when it's really just memorized noise.

WHY A REDUCED TICKER LIST
---------------------------
The full 70-ticker universe takes ~65 minutes for one run. This sweep
needs ~16 in-sample runs plus a handful of out-of-sample validation runs
-- full-universe would take days. TUNING_TICKERS below is a 20-ticker
slice spanning all 4 asset classes (10 US_STOCKS, 6 CRYPTO, 2 FOREX,
2 COMMODITIES) chosen for breadth, not cherry-picked for performance --
it's the same kind of representative sample used elsewhere in this
project when full-universe scope wasn't practical. Once a config change
is validated here, confirm it once more with a full --universe all run
before actually editing config.py.

HOW THE MONKEY-PATCHING WORKS
-------------------------------
approval_engine.py does `from config import MIN_TRADE_CONFIDENCE, ...`,
which copies the value into approval_engine's own module namespace at
import time -- editing config.py or a config.MIN_TRADE_CONFIDENCE
attribute after that point would NOT change what approve_trade() actually
sees, since it looks up the bare name in its own module's globals. This
script instead sets engines.approval_engine.MIN_TRADE_CONFIDENCE directly
before each run, which does work: Python functions resolve globals from
their own module's __dict__, exactly what this mutates. No changes to any
production file are needed or made by this script.

Usage:
    python3 tune_backtest.py
    python3 tune_backtest.py --top-n 5 --out tuning_results/my_sweep.csv
"""

import argparse
import csv
import os
from datetime import datetime

import engines.approval_engine as approval_engine
from engines.backtest_engine import run_backtest

TUNING_TICKERS = [
    # US_STOCKS -- blue-chip, high-growth, defensive, energy mix
    "SPY", "QQQ", "AAPL", "MSFT", "NVDA", "TSLA", "JPM", "KO", "XOM", "V",
    # CRYPTO -- majors + established alts, deliberately excludes the
    # extreme-moonshot tokens (WLD/VIRTUAL/RENDER/AAVE) flagged in the
    # benchmark breakdown as post-launch survivorship artifacts, so the
    # tuning signal isn't dominated by assets nobody could have picked
    # in advance.
    "BTC-USD", "ETH-USD", "SOL-USD", "ADA-USD", "LINK-USD", "AVAX-USD",
    # FOREX
    "EURUSD=X", "GBPUSD=X",
    # COMMODITIES
    "GC=F", "CL=F",
]

IN_SAMPLE_START = "2020-01-01"
IN_SAMPLE_END = "2023-01-01"
OUT_OF_SAMPLE_START = "2023-01-01"
OUT_OF_SAMPLE_END = "2025-01-01"

MIN_TRADE_CONFIDENCE_GRID = [40, 45, 50, 55]
MIN_RISK_REWARD_RATIO_GRID = [0.8, 1.0, 1.2, 1.5]

# A combo needs at least this many closed trades in-sample to be
# considered -- otherwise a config that happens to approve almost nothing
# can post a flukey, meaningless 100% win rate on 3 trades and look like
# a "winner" by Sharpe/profit-factor alone.
MIN_TRADES_FOR_CONSIDERATION = 30


def _run_one(confidence, risk_reward, start, end):
    approval_engine.MIN_TRADE_CONFIDENCE = confidence
    approval_engine.MIN_RISK_REWARD_RATIO = risk_reward
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
        "min_trade_confidence": confidence,
        "min_risk_reward_ratio": risk_reward,
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
    """Ranking key for in-sample candidates. Requires a minimum trade
    count and a real edge (profit factor > 1) before Sharpe is even
    considered -- otherwise a near-empty, lucky sample could rank first."""
    if row["trades_closed"] < MIN_TRADES_FOR_CONSIDERATION:
        return float("-inf")
    if row["profit_factor"] is None or row["profit_factor"] <= 1.0:
        return float("-inf")
    sharpe = row["sharpe_ratio"] or 0.0
    return sharpe * 10 + (row["profit_factor"] - 1.0)


def _fmt_row(row, label=""):
    marker = " [CURRENT DEFAULT]" if row["min_trade_confidence"] == 45 and row["min_risk_reward_ratio"] == 1.0 else ""
    return (
        f"{label}conf={row['min_trade_confidence']:<4} RR={row['min_risk_reward_ratio']:<4} | "
        f"trades={row['trades_closed']:<5} win%={row['win_rate']:>6.2f} "
        f"PF={row['profit_factor']:>6.3f} Sharpe={row['sharpe_ratio']:>6.3f} "
        f"Sortino={row['sortino_ratio']:>6.3f} return={row['total_return_pct']:>+8.2f}%"
        f"{marker}"
    )


def main():
    parser = argparse.ArgumentParser(description="Sweep approval thresholds, validate winners out-of-sample.")
    parser.add_argument("--top-n", type=int, default=3, help="How many top in-sample candidates to validate out-of-sample (default: 3)")
    parser.add_argument("--out", default=None, help="Path to write the full sweep as CSV")
    args = parser.parse_args()

    grid = [(c, rr) for c in MIN_TRADE_CONFIDENCE_GRID for rr in MIN_RISK_REWARD_RATIO_GRID]
    print(f"IN-SAMPLE sweep: {len(grid)} combos | {IN_SAMPLE_START} -> {IN_SAMPLE_END} | "
          f"{len(TUNING_TICKERS)} tickers\n")

    in_sample_results = []
    for i, (confidence, risk_reward) in enumerate(grid, 1):
        print(f"[{i}/{len(grid)}] Running conf={confidence} RR={risk_reward} ...", flush=True)
        row = _run_one(confidence, risk_reward, IN_SAMPLE_START, IN_SAMPLE_END)
        in_sample_results.append(row)
        print("  " + _fmt_row(row))

    print("\n" + "=" * 90)
    print("IN-SAMPLE RESULTS -- ranked by composite score (Sharpe + profit-factor edge, "
          f"min {MIN_TRADES_FOR_CONSIDERATION} trades required)")
    print("=" * 90)
    ranked = sorted(in_sample_results, key=_composite_score, reverse=True)
    for i, row in enumerate(ranked, 1):
        print(f"#{i}  " + _fmt_row(row))

    if args.out:
        out_path = args.out
    else:
        os.makedirs("tuning_results", exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = os.path.join("tuning_results", f"{ts}_sweep.csv")
    with open(out_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(in_sample_results[0].keys()))
        writer.writeheader()
        writer.writerows(in_sample_results)
    print(f"\nFull in-sample sweep written to: {out_path}")

    top_candidates = [r for r in ranked if _composite_score(r) > float("-inf")][:args.top_n]
    if not top_candidates:
        print("\nNo candidate cleared the minimum trade count / profit factor bar -- "
              "nothing to validate out-of-sample.")
        return

    print("\n" + "=" * 90)
    print(f"OUT-OF-SAMPLE VALIDATION -- top {len(top_candidates)} in-sample candidate(s), "
          f"re-run on data they were never tuned against ({OUT_OF_SAMPLE_START} -> {OUT_OF_SAMPLE_END})")
    print("=" * 90)

    baseline_oos = _run_one(45, 1.0, OUT_OF_SAMPLE_START, OUT_OF_SAMPLE_END)
    print("BASELINE (current config.py defaults) out-of-sample:")
    print("  " + _fmt_row(baseline_oos))

    for i, candidate in enumerate(top_candidates, 1):
        confidence = candidate["min_trade_confidence"]
        risk_reward = candidate["min_risk_reward_ratio"]
        print(f"\nCandidate #{i}: conf={confidence} RR={risk_reward}")
        print("  in-sample:     " + _fmt_row(candidate))
        oos_row = _run_one(confidence, risk_reward, OUT_OF_SAMPLE_START, OUT_OF_SAMPLE_END)
        print("  out-of-sample: " + _fmt_row(oos_row))
        holds_up = (
            oos_row["profit_factor"] > baseline_oos["profit_factor"]
            and oos_row["sharpe_ratio"] > baseline_oos["sharpe_ratio"]
        )
        verdict = "HOLDS UP vs baseline out-of-sample -- worth adopting" if holds_up else \
                  "does NOT clearly beat baseline out-of-sample -- likely overfit to in-sample window, do not adopt"
        print(f"  verdict: {verdict}")

    print("=" * 90)


if __name__ == "__main__":
    main()
