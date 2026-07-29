
from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from engines.market_data_engine import get_market_data


# ============================================================
# V8 CONFIGURATION
# ============================================================

SYMBOL = "SPY"

V7_DIRECTORY = Path("research/v7_profit_backtest")
TRADES_PATH = V7_DIRECTORY / "v7_trades.csv"
SIGNALS_PATH = V7_DIRECTORY / "v7_signals.csv"
EQUITY_PATH = V7_DIRECTORY / "v7_equity_curve.csv"

OUTPUT_DIRECTORY = Path("research/v8_trade_forensics")

ENRICHED_TRADES_PATH = OUTPUT_DIRECTORY / "v8_enriched_trades.csv"
EXIT_REASON_PATH = OUTPUT_DIRECTORY / "v8_exit_reason_summary.csv"
YEARLY_PATH = OUTPUT_DIRECTORY / "v8_yearly_summary.csv"
MONTHLY_PATH = OUTPUT_DIRECTORY / "v8_monthly_summary.csv"
HOLDING_PATH = OUTPUT_DIRECTORY / "v8_holding_period_summary.csv"
CONFIDENCE_PATH = OUTPUT_DIRECTORY / "v8_confidence_summary.csv"
SUMMARY_PATH = OUTPUT_DIRECTORY / "v8_forensic_summary.csv"


# ============================================================
# LOADING AND VALIDATION
# ============================================================

def require_file(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(
            f"Required file not found: {path}\n"
            "Run backtest_selective_v7.py first."
        )


def load_v7_outputs() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    print("[1/8] Loading V7 research outputs...")

    for path in (TRADES_PATH, SIGNALS_PATH, EQUITY_PATH):
        require_file(path)

    trades = pd.read_csv(
        TRADES_PATH,
        parse_dates=["Entry_Date", "Exit_Date"],
    )

    signals = pd.read_csv(SIGNALS_PATH)

    if "Execution_Date" in signals.columns:
        signals["Execution_Date"] = pd.to_datetime(
            signals["Execution_Date"],
            errors="coerce",
        )
        signals = signals.set_index("Execution_Date")
    elif "Date" in signals.columns:
        signals["Date"] = pd.to_datetime(
            signals["Date"],
            errors="coerce",
        )
        signals = signals.set_index("Date")
    else:
        first_column = signals.columns[0]
        parsed_index = pd.to_datetime(
            signals[first_column],
            errors="coerce",
        )
        if parsed_index.notna().sum() > 0:
            signals[first_column] = parsed_index
            signals = signals.set_index(first_column)

    equity = pd.read_csv(EQUITY_PATH)

    if "Date" in equity.columns:
        equity["Date"] = pd.to_datetime(
            equity["Date"],
            errors="coerce",
        )
        equity = equity.set_index("Date")
    else:
        first_column = equity.columns[0]
        parsed_index = pd.to_datetime(
            equity[first_column],
            errors="coerce",
        )
        if parsed_index.notna().sum() > 0:
            equity[first_column] = parsed_index
            equity = equity.set_index(first_column)

    required_trade_columns = {
        "Entry_Date",
        "Exit_Date",
        "Entry_Price",
        "Exit_Price",
        "Net_Profit",
        "Return_Percent",
        "Holding_Days",
        "Exit_Reason",
    }

    missing = required_trade_columns - set(trades.columns)

    if missing:
        raise RuntimeError(
            "v7_trades.csv is missing required columns: "
            f"{sorted(missing)}"
        )

    print("Trades loaded:", len(trades))
    print("Signals loaded:", len(signals))
    print("Equity rows loaded:", len(equity))

    return trades, signals, equity


def load_market_data(
    trades: pd.DataFrame,
) -> pd.DataFrame:
    print("[2/8] Retrieving market data for trade-path analysis...")

    start_date = (
        trades["Entry_Date"].min()
        - pd.Timedelta(days=10)
    ).strftime("%Y-%m-%d")

    end_date = (
        trades["Exit_Date"].max()
        + pd.Timedelta(days=10)
    ).strftime("%Y-%m-%d")

    market = get_market_data(
        SYMBOL,
        start=start_date,
        end=end_date,
        interval="1d",
        auto_adjust=True,
    )

    if market.empty:
        raise RuntimeError(
            "No SPY market data was returned for V8."
        )

    if isinstance(market.columns, pd.MultiIndex):
        market.columns = market.columns.get_level_values(0)

    market = market.sort_index()
    market.index = pd.to_datetime(market.index)

    required_columns = {"Open", "High", "Low", "Close"}
    missing = required_columns - set(market.columns)

    if missing:
        raise RuntimeError(
            "Market data is missing required columns: "
            f"{sorted(missing)}"
        )

    return market


# ============================================================
# TRADE ENRICHMENT
# ============================================================

def safe_divide(
    numerator: float,
    denominator: float,
) -> float:
    if denominator == 0:
        return 0.0
    return float(numerator / denominator)


def get_signal_confidence(
    signals: pd.DataFrame,
    entry_date: pd.Timestamp,
) -> tuple[float, float, float]:
    if signals.empty:
        return np.nan, np.nan, np.nan

    matching = signals.loc[
        signals.index == entry_date
    ]

    if matching.empty:
        return np.nan, np.nan, np.nan

    row = matching.iloc[0]

    buy_probability = row.get(
        "Buy_Probability",
        row.get("BUY_Probability", np.nan),
    )

    sell_probability = row.get(
        "Sell_Probability",
        row.get("SELL_Probability", np.nan),
    )

    wait_probability = row.get(
        "Wait_Probability",
        row.get("WAIT_Probability", np.nan),
    )

    return (
        float(buy_probability)
        if pd.notna(buy_probability)
        else np.nan,
        float(sell_probability)
        if pd.notna(sell_probability)
        else np.nan,
        float(wait_probability)
        if pd.notna(wait_probability)
        else np.nan,
    )


def enrich_trades(
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    market: pd.DataFrame,
) -> pd.DataFrame:
    print("[3/8] Calculating MFE, MAE and post-exit behaviour...")

    enriched_rows: list[dict[str, Any]] = []

    for _, trade in trades.iterrows():
        entry_date = pd.Timestamp(trade["Entry_Date"])
        exit_date = pd.Timestamp(trade["Exit_Date"])
        entry_price = float(trade["Entry_Price"])

        trade_path = market.loc[
            (market.index >= entry_date)
            & (market.index <= exit_date)
        ]

        if trade_path.empty:
            mfe = np.nan
            mae = np.nan
            close_return = np.nan
        else:
            highest_price = float(trade_path["High"].max())
            lowest_price = float(trade_path["Low"].min())
            final_close = float(trade_path["Close"].iloc[-1])

            mfe = safe_divide(
                highest_price - entry_price,
                entry_price,
            )

            mae = safe_divide(
                lowest_price - entry_price,
                entry_price,
            )

            close_return = safe_divide(
                final_close - entry_price,
                entry_price,
            )

        future_window_end = exit_date + pd.Timedelta(days=10)

        post_exit_path = market.loc[
            (market.index > exit_date)
            & (market.index <= future_window_end)
        ].head(5)

        if post_exit_path.empty:
            post_exit_max_return = np.nan
            post_exit_min_return = np.nan
        else:
            exit_price = float(trade["Exit_Price"])

            post_exit_max_return = safe_divide(
                float(post_exit_path["High"].max())
                - exit_price,
                exit_price,
            )

            post_exit_min_return = safe_divide(
                float(post_exit_path["Low"].min())
                - exit_price,
                exit_price,
            )

        buy_probability, sell_probability, wait_probability = (
            get_signal_confidence(
                signals,
                entry_date,
            )
        )

        net_profit = float(trade["Net_Profit"])
        return_percent = float(trade["Return_Percent"])

        enriched = trade.to_dict()

        enriched.update(
            {
                "Outcome": (
                    "WIN"
                    if net_profit > 0
                    else "LOSS"
                    if net_profit < 0
                    else "FLAT"
                ),
                "MFE_Percent": mfe,
                "MAE_Percent": mae,
                "Path_Close_Return": close_return,
                "Profit_Capture_Ratio": (
                    safe_divide(return_percent, mfe)
                    if pd.notna(mfe) and mfe > 0
                    else np.nan
                ),
                "Post_Exit_Max_Return_5D": post_exit_max_return,
                "Post_Exit_Min_Return_5D": post_exit_min_return,
                "Buy_Probability_At_Entry": buy_probability,
                "Sell_Probability_At_Entry": sell_probability,
                "Wait_Probability_At_Entry": wait_probability,
                "Entry_Year": entry_date.year,
                "Entry_Month": str(entry_date.to_period("M")),
                "Holding_Bucket": pd.cut(
                    [float(trade["Holding_Days"])],
                    bins=[-np.inf, 1, 3, 5, np.inf],
                    labels=[
                        "1 day",
                        "2-3 days",
                        "4-5 days",
                        "6+ days",
                    ],
                )[0],
            }
        )

        enriched_rows.append(enriched)

    return pd.DataFrame(enriched_rows)


# ============================================================
# SUMMARY HELPERS
# ============================================================

def summarise_group(
    data: pd.DataFrame,
    group_column: str,
) -> pd.DataFrame:
    grouped = data.groupby(
        group_column,
        dropna=False,
        observed=False,
    )

    summary = grouped.agg(
        Trades=("Net_Profit", "size"),
        Net_Profit=("Net_Profit", "sum"),
        Average_Profit=("Net_Profit", "mean"),
        Win_Rate=(
            "Net_Profit",
            lambda values: float((values > 0).mean()),
        ),
        Average_Return=("Return_Percent", "mean"),
        Median_Return=("Return_Percent", "median"),
        Average_MFE=("MFE_Percent", "mean"),
        Average_MAE=("MAE_Percent", "mean"),
        Average_Holding_Days=("Holding_Days", "mean"),
    ).reset_index()

    gross_profit = grouped["Net_Profit"].apply(
        lambda values: values[values > 0].sum()
    )

    gross_loss = grouped["Net_Profit"].apply(
        lambda values: abs(values[values < 0].sum())
    )

    profit_factor = pd.Series(
        np.where(
            gross_loss.values > 0,
            gross_profit.values / gross_loss.values,
            np.where(
                gross_profit.values > 0,
                np.inf,
                0.0,
            ),
        ),
        index=gross_profit.index,
    )

    summary["Profit_Factor"] = summary[group_column].map(
        profit_factor
    )

    return summary


def confidence_bucket(
    probability: pd.Series,
) -> pd.Series:
    return pd.cut(
        probability,
        bins=[
            -np.inf,
            0.40,
            0.45,
            0.50,
            0.55,
            0.60,
            np.inf,
        ],
        labels=[
            "<40%",
            "40-45%",
            "45-50%",
            "50-55%",
            "55-60%",
            "60%+",
        ],
    )


# ============================================================
# FORENSIC ANALYSIS
# ============================================================

def run_forensics(
    enriched: pd.DataFrame,
) -> dict[str, pd.DataFrame]:
    print("[4/8] Analysing where the strategy lost money...")

    exit_reason_summary = summarise_group(
        enriched,
        "Exit_Reason",
    )

    yearly_summary = summarise_group(
        enriched,
        "Entry_Year",
    )

    monthly_summary = summarise_group(
        enriched,
        "Entry_Month",
    )

    holding_summary = summarise_group(
        enriched,
        "Holding_Bucket",
    )

    enriched["Buy_Confidence_Bucket"] = confidence_bucket(
        enriched["Buy_Probability_At_Entry"]
    )

    confidence_summary = summarise_group(
        enriched,
        "Buy_Confidence_Bucket",
    )

    return {
        "exit_reason": exit_reason_summary,
        "yearly": yearly_summary,
        "monthly": monthly_summary,
        "holding": holding_summary,
        "confidence": confidence_summary,
    }


def calculate_forensic_summary(
    enriched: pd.DataFrame,
    equity: pd.DataFrame,
) -> pd.DataFrame:
    print("[5/8] Calculating V8 forensic conclusions...")

    losses = enriched[enriched["Net_Profit"] < 0]
    wins = enriched[enriched["Net_Profit"] > 0]

    total_profit = float(enriched["Net_Profit"].sum())
    total_trades = int(len(enriched))

    avoidable_exit_losses = enriched[
        (enriched["Net_Profit"] < 0)
        & (enriched["MFE_Percent"] > 0.02)
    ]

    model_sell_trades = enriched[
        enriched["Exit_Reason"].astype(str).str.contains(
            "SELL",
            case=False,
            na=False,
        )
    ]

    stop_loss_trades = enriched[
        enriched["Exit_Reason"].astype(str).str.contains(
            "STOP",
            case=False,
            na=False,
        )
    ]

    max_holding_trades = enriched[
        enriched["Exit_Reason"].astype(str).str.contains(
            "HOLD",
            case=False,
            na=False,
        )
    ]

    take_profit_trades = enriched[
        enriched["Exit_Reason"].astype(str).str.contains(
            "TAKE",
            case=False,
            na=False,
        )
    ]

    worst_losing_streak = 0
    current_losing_streak = 0

    for profit in enriched.sort_values(
        "Exit_Date"
    )["Net_Profit"]:
        if profit < 0:
            current_losing_streak += 1
            worst_losing_streak = max(
                worst_losing_streak,
                current_losing_streak,
            )
        else:
            current_losing_streak = 0

    summary = {
        "Total_Trades": total_trades,
        "Total_Net_Profit": total_profit,
        "Win_Rate": float((enriched["Net_Profit"] > 0).mean()),
        "Average_Win": (
            float(wins["Net_Profit"].mean())
            if not wins.empty
            else 0.0
        ),
        "Average_Loss": (
            float(losses["Net_Profit"].mean())
            if not losses.empty
            else 0.0
        ),
        "Average_MFE": float(enriched["MFE_Percent"].mean()),
        "Average_MAE": float(enriched["MAE_Percent"].mean()),
        "Median_MFE": float(enriched["MFE_Percent"].median()),
        "Median_MAE": float(enriched["MAE_Percent"].median()),
        "Worst_Losing_Streak": worst_losing_streak,
        "Avoidable_Loss_Count": int(len(avoidable_exit_losses)),
        "Avoidable_Loss_Net_Profit": float(
            avoidable_exit_losses["Net_Profit"].sum()
        ),
        "Model_Sell_Trade_Count": int(len(model_sell_trades)),
        "Model_Sell_Net_Profit": float(
            model_sell_trades["Net_Profit"].sum()
        ),
        "Stop_Loss_Trade_Count": int(len(stop_loss_trades)),
        "Stop_Loss_Net_Profit": float(
            stop_loss_trades["Net_Profit"].sum()
        ),
        "Max_Holding_Trade_Count": int(len(max_holding_trades)),
        "Max_Holding_Net_Profit": float(
            max_holding_trades["Net_Profit"].sum()
        ),
        "Take_Profit_Trade_Count": int(len(take_profit_trades)),
        "Take_Profit_Net_Profit": float(
            take_profit_trades["Net_Profit"].sum()
        ),
    }

    return pd.DataFrame([summary])


# ============================================================
# REPORTING
# ============================================================

def print_table(
    title: str,
    data: pd.DataFrame,
    rows: int = 20,
) -> None:
    print()
    print("=" * 86)
    print(title)
    print("=" * 86)

    if data.empty:
        print("No data available.")
        return

    display = data.head(rows).copy()

    for column in display.columns:
        if (
            "Rate" in column
            or "Return" in column
            or "MFE" in column
            or "MAE" in column
        ):
            if pd.api.types.is_numeric_dtype(display[column]):
                display[column] = display[column].map(
                    lambda value: (
                        f"{value:.2%}"
                        if pd.notna(value)
                        else "N/A"
                    )
                )

    print(display.to_string(index=False))


def print_main_findings(
    enriched: pd.DataFrame,
    analyses: dict[str, pd.DataFrame],
    forensic_summary: pd.DataFrame,
) -> None:
    summary = forensic_summary.iloc[0]

    print()
    print("=" * 86)
    print("V8 TRADE FORENSICS — MAIN FINDINGS")
    print("=" * 86)

    print("Total trades:", int(summary["Total_Trades"]))
    print(
        "Total net profit:",
        f"${summary['Total_Net_Profit']:,.2f}",
    )
    print(
        "Win rate:",
        f"{summary['Win_Rate']:.2%}",
    )
    print(
        "Average MFE:",
        f"{summary['Average_MFE']:.2%}",
    )
    print(
        "Average MAE:",
        f"{summary['Average_MAE']:.2%}",
    )
    print(
        "Worst losing streak:",
        int(summary["Worst_Losing_Streak"]),
    )
    print(
        "Potentially avoidable losses:",
        int(summary["Avoidable_Loss_Count"]),
    )

    exit_summary = analyses["exit_reason"].sort_values(
        "Net_Profit"
    )

    if not exit_summary.empty:
        worst_exit = exit_summary.iloc[0]
        best_exit = exit_summary.iloc[-1]

        print()
        print(
            "Most damaging exit reason:",
            worst_exit["Exit_Reason"],
            f"(${worst_exit['Net_Profit']:,.2f})",
        )

        print(
            "Most profitable exit reason:",
            best_exit["Exit_Reason"],
            f"(${best_exit['Net_Profit']:,.2f})",
        )

    confidence_summary = analyses["confidence"].copy()

    profitable_confidence = confidence_summary[
        confidence_summary["Net_Profit"] > 0
    ]

    if not profitable_confidence.empty:
        best_confidence = profitable_confidence.sort_values(
            "Net_Profit",
            ascending=False,
        ).iloc[0]

        print(
            "Best profitable BUY-confidence bucket:",
            best_confidence["Buy_Confidence_Bucket"],
            f"(${best_confidence['Net_Profit']:,.2f})",
        )
    else:
        print(
            "No BUY-confidence bucket produced positive "
            "aggregate profit."
        )

    print()
    print("Interpretation guide:")

    print(
        "- Large positive MFE with a losing final result "
        "suggests an exit-management problem."
    )

    print(
        "- Persistently negative MAE immediately after entry "
        "suggests an entry-quality problem."
    )

    print(
        "- A strongly negative MODEL_SELL result suggests the "
        "SELL classifier may be damaging valid long positions."
    )

    print(
        "- Profit concentrated in only one confidence bucket "
        "suggests the entry threshold should be tightened."
    )

    print("=" * 86)


# ============================================================
# SAVING
# ============================================================

def save_outputs(
    enriched: pd.DataFrame,
    analyses: dict[str, pd.DataFrame],
    forensic_summary: pd.DataFrame,
) -> None:
    print("[6/8] Saving V8 forensic outputs...")

    OUTPUT_DIRECTORY.mkdir(
        parents=True,
        exist_ok=True,
    )

    enriched.to_csv(
        ENRICHED_TRADES_PATH,
        index=False,
    )

    analyses["exit_reason"].to_csv(
        EXIT_REASON_PATH,
        index=False,
    )

    analyses["yearly"].to_csv(
        YEARLY_PATH,
        index=False,
    )

    analyses["monthly"].to_csv(
        MONTHLY_PATH,
        index=False,
    )

    analyses["holding"].to_csv(
        HOLDING_PATH,
        index=False,
    )

    analyses["confidence"].to_csv(
        CONFIDENCE_PATH,
        index=False,
    )

    forensic_summary.to_csv(
        SUMMARY_PATH,
        index=False,
    )

    print("Saved:", ENRICHED_TRADES_PATH)
    print("Saved:", EXIT_REASON_PATH)
    print("Saved:", YEARLY_PATH)
    print("Saved:", MONTHLY_PATH)
    print("Saved:", HOLDING_PATH)
    print("Saved:", CONFIDENCE_PATH)
    print("Saved:", SUMMARY_PATH)


# ============================================================
# MAIN
# ============================================================

def main() -> None:
    print("=" * 86)
    print("AI TRADING MACHINE — V8 TRADE FORENSICS")
    print("=" * 86)

    trades, signals, equity = load_v7_outputs()
    market = load_market_data(trades)

    enriched = enrich_trades(
        trades,
        signals,
        market,
    )

    analyses = run_forensics(enriched)

    forensic_summary = calculate_forensic_summary(
        enriched,
        equity,
    )

    print_table(
        "EXIT-REASON PERFORMANCE",
        analyses["exit_reason"].sort_values(
            "Net_Profit"
        ),
    )

    print_table(
        "BUY-CONFIDENCE PERFORMANCE",
        analyses["confidence"],
    )

    print_table(
        "HOLDING-PERIOD PERFORMANCE",
        analyses["holding"],
    )

    print_table(
        "YEARLY PERFORMANCE",
        analyses["yearly"],
    )

    print_main_findings(
        enriched,
        analyses,
        forensic_summary,
    )

    save_outputs(
        enriched,
        analyses,
        forensic_summary,
    )

    print()
    print("[7/8] V8 diagnostic analysis completed.")
    print(
        "[8/8] No production model, broker setting, "
        "or Alpaca order was modified."
    )


if __name__ == "__main__":
    main()
