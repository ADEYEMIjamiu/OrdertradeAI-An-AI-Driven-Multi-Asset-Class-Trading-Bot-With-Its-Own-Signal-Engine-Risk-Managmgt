from __future__ import annotations

from dataclasses import dataclass
from itertools import product
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd

import backtest_selective_v7 as v7


# ============================================================
# V9 — RISK AND REGIME OPTIMISATION
# ============================================================

OUTPUT_DIRECTORY = Path("research/v9_risk_regime_optimisation")
GRID_RESULTS_PATH = OUTPUT_DIRECTORY / "v9_development_grid.csv"
BEST_CONFIGURATION_PATH = OUTPUT_DIRECTORY / "v9_best_configuration.csv"
VALIDATION_TRADES_PATH = OUTPUT_DIRECTORY / "v9_validation_trades.csv"
VALIDATION_EQUITY_PATH = OUTPUT_DIRECTORY / "v9_validation_equity.csv"
VALIDATION_SUMMARY_PATH = OUTPUT_DIRECTORY / "v9_validation_summary.csv"

DEVELOPMENT_FRACTION = 0.70

STOP_LOSSES = [0.03, 0.04, 0.05]
TAKE_PROFITS = [0.06, 0.08, 0.10]
MAX_HOLDING_PERIODS = [5, 7, 10]
MIN_BUY_CONFIDENCES = [0.40, 0.50, 0.55]
REGIME_FILTERS = ["NONE", "ABOVE_SMA200"]
USE_MODEL_SELL_OPTIONS = [True, False]

MIN_DEVELOPMENT_TRADES = 20
MIN_VALIDATION_TRADES = 8

INITIAL_CAPITAL = float(v7.INITIAL_CAPITAL)
POSITION_SIZE_PERCENT = float(v7.POSITION_SIZE_PERCENT)
COMMISSION_BPS = float(v7.COMMISSION_BPS)
SLIPPAGE_BPS = float(v7.SLIPPAGE_BPS)


@dataclass
class Position:
    entry_date: pd.Timestamp
    entry_price: float
    shares: float
    total_entry_cost: float
    holding_days: int = 0


def apply_buy_slippage(price: float) -> float:
    return price * (1.0 + SLIPPAGE_BPS / 10_000.0)


def apply_sell_slippage(price: float) -> float:
    return price * (1.0 - SLIPPAGE_BPS / 10_000.0)


def commission(value: float) -> float:
    return value * COMMISSION_BPS / 10_000.0


def close_position(
    position: Position,
    exit_date: pd.Timestamp,
    market_price: float,
    reason: str,
) -> tuple[float, dict]:
    exit_price = apply_sell_slippage(market_price)
    gross_value = position.shares * exit_price
    exit_commission = commission(gross_value)
    net_value = gross_value - exit_commission
    net_profit = net_value - position.total_entry_cost

    return net_value, {
        "Entry_Date": position.entry_date,
        "Exit_Date": exit_date,
        "Entry_Price": position.entry_price,
        "Exit_Price": exit_price,
        "Shares": position.shares,
        "Entry_Cost": position.total_entry_cost,
        "Net_Exit_Value": net_value,
        "Net_Profit": net_profit,
        "Return_Percent": (
            net_profit / position.total_entry_cost
            if position.total_entry_cost > 0
            else 0.0
        ),
        "Holding_Days": position.holding_days,
        "Exit_Reason": reason,
    }


def add_regime_columns(signals: pd.DataFrame) -> pd.DataFrame:
    enriched = signals.copy()
    close = pd.to_numeric(enriched["Execution_Close"], errors="coerce")

    # Shift by one session so today's execution does not use today's close
    # to decide whether today's BUY is permitted.
    enriched["Regime_Close"] = close.shift(1)
    enriched["Regime_SMA200"] = close.rolling(200).mean().shift(1)
    enriched["Above_SMA200"] = (
        enriched["Regime_Close"] > enriched["Regime_SMA200"]
    )

    return enriched


def configuration_signal(
    row: pd.Series,
    min_buy_confidence: float,
    regime_filter: str,
) -> str:
    original_signal = str(row["Signal"])

    if original_signal != "BUY":
        return original_signal

    buy_probability = float(row["BUY_Probability"])
    if buy_probability < min_buy_confidence:
        return "WAIT"

    if regime_filter == "ABOVE_SMA200":
        if not bool(row.get("Above_SMA200", False)):
            return "WAIT"

    return "BUY"


def run_simulation(
    signals: pd.DataFrame,
    stop_loss: float,
    take_profit: float,
    max_holding_days: int,
    min_buy_confidence: float,
    regime_filter: str,
    use_model_sell: bool,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    cash = INITIAL_CAPITAL
    position: Optional[Position] = None
    trades: list[dict] = []
    equity_rows: list[dict] = []

    for date, row in signals.iterrows():
        open_price = float(row["Execution_Open"])
        high_price = float(row["Execution_High"])
        low_price = float(row["Execution_Low"])
        close_price = float(row["Execution_Close"])

        signal = configuration_signal(
            row=row,
            min_buy_confidence=min_buy_confidence,
            regime_filter=regime_filter,
        )

        if position is not None:
            position.holding_days += 1
            stop_price = position.entry_price * (1.0 - stop_loss)
            take_price = position.entry_price * (1.0 + take_profit)

            reason: Optional[str] = None
            exit_market_price: Optional[float] = None

            # Conservative assumption if stop and target both occur:
            # stop is treated as occurring first.
            if low_price <= stop_price:
                reason = "STOP_LOSS"
                exit_market_price = stop_price
            elif high_price >= take_price:
                reason = "TAKE_PROFIT"
                exit_market_price = take_price
            elif use_model_sell and signal == "SELL":
                reason = "SELL_SIGNAL"
                exit_market_price = open_price
            elif position.holding_days >= max_holding_days:
                reason = "MAX_HOLDING"
                exit_market_price = close_price

            if reason is not None and exit_market_price is not None:
                net_value, trade = close_position(
                    position=position,
                    exit_date=pd.Timestamp(date),
                    market_price=exit_market_price,
                    reason=reason,
                )
                cash += net_value
                trades.append(trade)
                position = None

        if position is None and signal == "BUY":
            allocation = cash * POSITION_SIZE_PERCENT
            entry_price = apply_buy_slippage(open_price)
            investable = allocation / (
                1.0 + COMMISSION_BPS / 10_000.0
            )
            shares = investable / entry_price if entry_price > 0 else 0.0
            gross_entry = shares * entry_price
            entry_commission = commission(gross_entry)
            total_cost = gross_entry + entry_commission

            if shares > 0 and total_cost <= cash:
                cash -= total_cost
                position = Position(
                    entry_date=pd.Timestamp(date),
                    entry_price=entry_price,
                    shares=shares,
                    total_entry_cost=total_cost,
                    holding_days=0,
                )

                stop_price = entry_price * (1.0 - stop_loss)
                take_price = entry_price * (1.0 + take_profit)

                if low_price <= stop_price:
                    net_value, trade = close_position(
                        position=position,
                        exit_date=pd.Timestamp(date),
                        market_price=stop_price,
                        reason="ENTRY_DAY_STOP",
                    )
                    cash += net_value
                    trades.append(trade)
                    position = None
                elif high_price >= take_price:
                    net_value, trade = close_position(
                        position=position,
                        exit_date=pd.Timestamp(date),
                        market_price=take_price,
                        reason="ENTRY_DAY_TAKE_PROFIT",
                    )
                    cash += net_value
                    trades.append(trade)
                    position = None

        position_value = (
            position.shares * close_price
            if position is not None
            else 0.0
        )

        equity_rows.append(
            {
                "Date": date,
                "Cash": cash,
                "Position_Value": position_value,
                "Total_Equity": cash + position_value,
                "Signal": signal,
                "In_Position": position is not None,
            }
        )

    if position is not None and len(signals) > 0:
        final_date = pd.Timestamp(signals.index[-1])
        final_close = float(signals.iloc[-1]["Execution_Close"])
        net_value, trade = close_position(
            position=position,
            exit_date=final_date,
            market_price=final_close,
            reason="END_OF_PERIOD",
        )
        cash += net_value
        trades.append(trade)

        if equity_rows:
            equity_rows[-1].update(
                {
                    "Cash": cash,
                    "Position_Value": 0.0,
                    "Total_Equity": cash,
                    "In_Position": False,
                }
            )

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_rows)

    if not equity_df.empty:
        equity_df = equity_df.set_index("Date")

    return trades_df, equity_df


def maximum_drawdown(equity: pd.Series) -> float:
    if equity.empty:
        return 0.0
    running_max = equity.cummax()
    drawdown = equity / running_max - 1.0
    return float(drawdown.min())


def calculate_performance(
    trades: pd.DataFrame,
    equity: pd.DataFrame,
) -> dict:
    if equity.empty:
        return {
            "Ending_Capital": INITIAL_CAPITAL,
            "Net_Profit": 0.0,
            "Total_Return": 0.0,
            "Annualised_Return": 0.0,
            "Sharpe_Ratio": 0.0,
            "Maximum_Drawdown": 0.0,
            "Total_Trades": 0,
            "Win_Rate": 0.0,
            "Profit_Factor": 0.0,
            "Expectancy": 0.0,
        }

    equity_series = equity["Total_Equity"].astype(float)
    ending_capital = float(equity_series.iloc[-1])
    net_profit = ending_capital - INITIAL_CAPITAL
    total_return = ending_capital / INITIAL_CAPITAL - 1.0

    daily_returns = equity_series.pct_change().dropna()
    periods = max(len(daily_returns), 1)

    annualised_return = (
        (1.0 + total_return) ** (252.0 / periods) - 1.0
        if ending_capital > 0
        else -1.0
    )

    volatility = float(daily_returns.std(ddof=0))
    sharpe = (
        float(daily_returns.mean() / volatility * np.sqrt(252.0))
        if volatility > 0
        else 0.0
    )

    if trades.empty:
        total_trades = 0
        win_rate = 0.0
        profit_factor = 0.0
        expectancy = 0.0
    else:
        profits = trades["Net_Profit"].astype(float)
        winners = profits[profits > 0]
        losers = profits[profits < 0]

        total_trades = int(len(profits))
        win_rate = float((profits > 0).mean())
        gross_profit = float(winners.sum())
        gross_loss = abs(float(losers.sum()))

        if gross_loss > 0:
            profit_factor = gross_profit / gross_loss
        elif gross_profit > 0:
            profit_factor = float("inf")
        else:
            profit_factor = 0.0

        expectancy = float(profits.mean())

    return {
        "Ending_Capital": ending_capital,
        "Net_Profit": net_profit,
        "Total_Return": total_return,
        "Annualised_Return": annualised_return,
        "Sharpe_Ratio": sharpe,
        "Maximum_Drawdown": maximum_drawdown(equity_series),
        "Total_Trades": total_trades,
        "Win_Rate": win_rate,
        "Profit_Factor": profit_factor,
        "Expectancy": expectancy,
    }


def selection_score(metrics: dict) -> float:
    trades = int(metrics["Total_Trades"])

    if trades < MIN_DEVELOPMENT_TRADES:
        return -1_000_000.0 + trades

    profit_factor = float(metrics["Profit_Factor"])
    if not np.isfinite(profit_factor):
        profit_factor = 5.0

    return (
        float(metrics["Total_Return"]) * 100.0
        + float(metrics["Sharpe_Ratio"]) * 2.0
        + min(profit_factor, 5.0)
        + float(metrics["Win_Rate"])
        + float(metrics["Maximum_Drawdown"])
    )


def evaluate_grid(
    development_signals: pd.DataFrame,
) -> pd.DataFrame:
    print("[3/8] Evaluating development-period risk configurations...")

    configurations = list(
        product(
            STOP_LOSSES,
            TAKE_PROFITS,
            MAX_HOLDING_PERIODS,
            MIN_BUY_CONFIDENCES,
            REGIME_FILTERS,
            USE_MODEL_SELL_OPTIONS,
        )
    )

    rows: list[dict] = []

    for index, (
        stop_loss,
        take_profit,
        max_holding_days,
        min_buy_confidence,
        regime_filter,
        use_model_sell,
    ) in enumerate(configurations, start=1):
        trades, equity = run_simulation(
            signals=development_signals,
            stop_loss=stop_loss,
            take_profit=take_profit,
            max_holding_days=max_holding_days,
            min_buy_confidence=min_buy_confidence,
            regime_filter=regime_filter,
            use_model_sell=use_model_sell,
        )

        metrics = calculate_performance(trades, equity)

        rows.append(
            {
                "Stop_Loss": stop_loss,
                "Take_Profit": take_profit,
                "Max_Holding_Days": max_holding_days,
                "Min_BUY_Confidence": min_buy_confidence,
                "Regime_Filter": regime_filter,
                "Use_Model_SELL": use_model_sell,
                **metrics,
                "Selection_Score": selection_score(metrics),
            }
        )

        if index % 50 == 0 or index == len(configurations):
            print(
                f"Processed {index}/{len(configurations)} configurations"
            )

    results = pd.DataFrame(rows)
    results = results.sort_values(
        by=[
            "Selection_Score",
            "Total_Return",
            "Profit_Factor",
        ],
        ascending=False,
    ).reset_index(drop=True)

    return results


def print_metrics(title: str, metrics: dict) -> None:
    print()
    print("=" * 82)
    print(title)
    print("=" * 82)
    print(f"Ending Capital:   ${metrics['Ending_Capital']:,.2f}")
    print(f"Net Profit:       ${metrics['Net_Profit']:,.2f}")
    print(f"Total Return:      {metrics['Total_Return']:.2%}")
    print(f"Annualised Return: {metrics['Annualised_Return']:.2%}")
    print(f"Sharpe Ratio:      {metrics['Sharpe_Ratio']:.3f}")
    print(f"Maximum Drawdown:  {metrics['Maximum_Drawdown']:.2%}")
    print(f"Total Trades:      {int(metrics['Total_Trades'])}")
    print(f"Win Rate:          {metrics['Win_Rate']:.2%}")
    print(f"Profit Factor:     {metrics['Profit_Factor']:.3f}")
    print(f"Expectancy:       ${metrics['Expectancy']:,.2f}")


def validation_gate(metrics: dict) -> tuple[bool, list[str]]:
    checks = {
        "Positive net profit": metrics["Net_Profit"] > 0,
        "Positive total return": metrics["Total_Return"] > 0,
        "Sharpe ratio at least 0.50": metrics["Sharpe_Ratio"] >= 0.50,
        "Profit factor at least 1.10": metrics["Profit_Factor"] >= 1.10,
        "Maximum drawdown no worse than -20%":
            metrics["Maximum_Drawdown"] >= -0.20,
        f"At least {MIN_VALIDATION_TRADES} validation trades":
            metrics["Total_Trades"] >= MIN_VALIDATION_TRADES,
        "Positive expectancy": metrics["Expectancy"] > 0,
    }

    messages: list[str] = []

    for label, passed in checks.items():
        messages.append(
            f"{'PASS' if passed else 'FAIL'}: {label}"
        )

    return all(checks.values()), messages


def main() -> None:
    print("=" * 82)
    print("AI TRADING MACHINE — V9 RISK AND REGIME OPTIMISATION")
    print("=" * 82)

    print("[1/8] Building the leakage-safe V7 dataset and signals...")
    data, feature_columns = v7.build_v7_dataset()
    signals = v7.generate_walk_forward_signals(
        data=data,
        feature_columns=feature_columns,
    )
    signals = add_regime_columns(signals).dropna(
        subset=["Regime_SMA200"]
    )

    split_index = int(len(signals) * DEVELOPMENT_FRACTION)

    development_signals = signals.iloc[:split_index].copy()
    validation_signals = signals.iloc[split_index:].copy()

    print("[2/8] Creating development and untouched validation periods...")
    print(
        "Development:",
        development_signals.index.min(),
        "->",
        development_signals.index.max(),
        f"({len(development_signals)} rows)",
    )
    print(
        "Validation:",
        validation_signals.index.min(),
        "->",
        validation_signals.index.max(),
        f"({len(validation_signals)} rows)",
    )

    grid_results = evaluate_grid(development_signals)

    eligible = grid_results[
        grid_results["Total_Trades"] >= MIN_DEVELOPMENT_TRADES
    ].copy()

    if eligible.empty:
        raise RuntimeError(
            "No V9 development configuration produced enough trades."
        )

    best = eligible.iloc[0].copy()

    print("[4/8] Selecting one configuration using development data only...")
    print()
    print("Selected configuration:")
    print(f"Stop loss:          {best['Stop_Loss']:.2%}")
    print(f"Take profit:        {best['Take_Profit']:.2%}")
    print(f"Maximum holding:    {int(best['Max_Holding_Days'])} days")
    print(f"Minimum BUY conf.:  {best['Min_BUY_Confidence']:.2%}")
    print(f"Regime filter:      {best['Regime_Filter']}")
    print(f"Use model SELL:     {bool(best['Use_Model_SELL'])}")

    development_metrics = {
        key: best[key]
        for key in [
            "Ending_Capital",
            "Net_Profit",
            "Total_Return",
            "Annualised_Return",
            "Sharpe_Ratio",
            "Maximum_Drawdown",
            "Total_Trades",
            "Win_Rate",
            "Profit_Factor",
            "Expectancy",
        ]
    }

    print_metrics(
        "V9 SELECTED CONFIGURATION — DEVELOPMENT RESULTS",
        development_metrics,
    )

    print("[5/8] Testing the selected configuration once on validation data...")

    validation_trades, validation_equity = run_simulation(
        signals=validation_signals,
        stop_loss=float(best["Stop_Loss"]),
        take_profit=float(best["Take_Profit"]),
        max_holding_days=int(best["Max_Holding_Days"]),
        min_buy_confidence=float(best["Min_BUY_Confidence"]),
        regime_filter=str(best["Regime_Filter"]),
        use_model_sell=bool(best["Use_Model_SELL"]),
    )

    validation_metrics = calculate_performance(
        validation_trades,
        validation_equity,
    )

    print_metrics(
        "V9 UNTOUCHED VALIDATION RESULTS",
        validation_metrics,
    )

    print("[6/8] Saving V9 research outputs...")

    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=True)

    grid_results.to_csv(GRID_RESULTS_PATH, index=False)
    pd.DataFrame([best]).to_csv(
        BEST_CONFIGURATION_PATH,
        index=False,
    )
    validation_trades.to_csv(
        VALIDATION_TRADES_PATH,
        index=False,
    )
    validation_equity.to_csv(
        VALIDATION_EQUITY_PATH,
        index=True,
    )
    pd.DataFrame([validation_metrics]).to_csv(
        VALIDATION_SUMMARY_PATH,
        index=False,
    )

    print("Saved:", GRID_RESULTS_PATH)
    print("Saved:", BEST_CONFIGURATION_PATH)
    print("Saved:", VALIDATION_TRADES_PATH)
    print("Saved:", VALIDATION_EQUITY_PATH)
    print("Saved:", VALIDATION_SUMMARY_PATH)

    print("[7/8] Running V9 untouched-validation economic gate...")

    passed, messages = validation_gate(validation_metrics)

    for message in messages:
        print(message)

    print()
    print("=" * 82)

    if passed:
        print("V9 RISK/REGIME RESEARCH RESULT: PASSED")
        print(
            "The selected execution policy survived the untouched "
            "validation period."
        )
    else:
        print("V9 RISK/REGIME RESEARCH RESULT: REJECTED")
        print(
            "The selected execution policy did not pass all untouched "
            "validation gates."
        )

    print(
        "The production model, dashboard, broker settings and Alpaca "
        "execution system were not modified."
    )
    print("=" * 82)
    print("[8/8] V9 completed successfully.")


if __name__ == "__main__":
    main()
