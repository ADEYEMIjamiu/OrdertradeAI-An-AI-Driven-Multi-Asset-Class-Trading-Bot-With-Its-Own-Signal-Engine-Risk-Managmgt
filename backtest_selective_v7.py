from pathlib import Path
from typing import Dict, List, Tuple

import joblib
import numpy as np
import pandas as pd
import ta

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import TimeSeriesSplit

from engines.market_data_engine import get_market_data


# ============================================================
# MODEL EDGE DEVELOPMENT V6
# SELECTIVE SIGNAL AND CONFIDENCE EXPERIMENT
# ============================================================

PRIMARY_SYMBOL = "SPY"

MARKET_SYMBOLS = {
    "SPY": "SPY",
    "QQQ": "QQQ",
    "IWM": "IWM",
    "VIX": "^VIX",
}

START_DATE = "2016-01-01"
END_DATE = None
INTERVAL = "1d"

PREDICTION_HORIZON_DAYS = 5
MOVEMENT_THRESHOLD = 0.015

HOLDOUT_PERCENT = 0.20
N_SPLITS = 5
RANDOM_STATE = 42

RESEARCH_DIR = Path("models/research")

CANDIDATE_MODEL_PATH = (
    RESEARCH_DIR
    / "trading_model_selective_v6.pkl"
)

CANDIDATE_FEATURES_PATH = (
    RESEARCH_DIR
    / "features_selective_v6.pkl"
)

CANDIDATE_METADATA_PATH = (
    RESEARCH_DIR
    / "metadata_selective_v6.pkl"
)

CLASS_NAMES = {
    -1: "SELL",
    0: "WAIT",
    1: "BUY",
}

# ============================================================
# V6 SELECTIVE SIGNAL CONFIGURATION
# ============================================================

BUY_CONFIDENCE_THRESHOLDS = [
    0.40,
    0.45,
    0.50,
    0.55,
    0.60,
    0.65,
    0.70,
]

SELL_CONFIDENCE_THRESHOLDS = [
    0.40,
    0.45,
    0.50,
    0.55,
    0.60,
    0.65,
    0.70,
]

MINIMUM_DIRECTIONAL_TRADES = 20

MINIMUM_BUY_PRECISION = 0.50
MINIMUM_SELL_PRECISION = 0.35

TRANSACTION_COST_RATE = 0.001

# ============================================================
# DATA HELPERS
# ============================================================

def flatten_market_data_columns(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert yfinance-style MultiIndex columns into standard
    single-level columns.
    """

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


def retrieve_market_data(
    symbol: str,
) -> pd.DataFrame:
    """
    Retrieve validated historical market data through the
    project's central market-data engine.
    """

    kwargs = {
        "symbol": symbol,
        "start": START_DATE,
        "interval": INTERVAL,
        "auto_adjust": True,
    }

    if END_DATE is not None:
        kwargs["end"] = END_DATE

    df = get_market_data(**kwargs)

    if df is None or df.empty:
        raise RuntimeError(
            f"No historical market data was returned for "
            f"{symbol}."
        )

    df = flatten_market_data_columns(df)

    required_columns = [
        "Open",
        "High",
        "Low",
        "Close",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise RuntimeError(
            f"{symbol} is missing required columns: "
            f"{missing_columns}"
        )

    df = df.sort_index()
    df = df[~df.index.duplicated(keep="last")]

    return df


def get_numeric_series(
    df: pd.DataFrame,
    column: str,
) -> pd.Series:
    """
    Safely extract a numeric one-dimensional series.
    """

    return pd.to_numeric(
        df[column].squeeze(),
        errors="coerce",
    )


# ============================================================
# SINGLE-MARKET FEATURE ENGINEERING
# ============================================================

def engineer_symbol_features(
    df: pd.DataFrame,
    prefix: str,
    include_volume: bool = True,
) -> pd.DataFrame:
    """
    Create technical and statistical features for one market.

    All resulting feature names are prefixed, for example:

        SPY_Return_5
        QQQ_RSI
        IWM_ATR_Percent
        VIX_Momentum_10
    """

    df = flatten_market_data_columns(df)
    df = df.copy()

    open_price = get_numeric_series(df, "Open")
    high = get_numeric_series(df, "High")
    low = get_numeric_series(df, "Low")
    close = get_numeric_series(df, "Close")

    output = pd.DataFrame(index=df.index)

    # --------------------------------------------------------
    # Raw reference values
    # --------------------------------------------------------

    output[f"{prefix}_Close"] = close

    # --------------------------------------------------------
    # Returns and momentum
    # --------------------------------------------------------

    output[f"{prefix}_Return_1"] = (
        close.pct_change(1)
    )

    output[f"{prefix}_Return_5"] = (
        close.pct_change(5)
    )

    output[f"{prefix}_Return_10"] = (
        close.pct_change(10)
    )

    output[f"{prefix}_Return_20"] = (
        close.pct_change(20)
    )

    output[f"{prefix}_Momentum_5"] = (
        close / close.shift(5)
    ) - 1

    output[f"{prefix}_Momentum_10"] = (
        close / close.shift(10)
    ) - 1

    output[f"{prefix}_Momentum_20"] = (
        close / close.shift(20)
    ) - 1

    # --------------------------------------------------------
    # Trend
    # --------------------------------------------------------

    sma20 = ta.trend.sma_indicator(
        close,
        window=20,
    )

    sma50 = ta.trend.sma_indicator(
        close,
        window=50,
    )

    sma200 = ta.trend.sma_indicator(
        close,
        window=200,
    )

    output[f"{prefix}_SMA20_Distance"] = (
        close / sma20
    ) - 1

    output[f"{prefix}_SMA50_Distance"] = (
        close / sma50
    ) - 1

    output[f"{prefix}_SMA200_Distance"] = (
        close / sma200
    ) - 1

    output[f"{prefix}_SMA20_Slope"] = (
        sma20.pct_change(5)
    )

    output[f"{prefix}_SMA50_Slope"] = (
        sma50.pct_change(10)
    )

    output[f"{prefix}_Trend_Spread"] = (
        sma20 - sma50
    ) / close

    # --------------------------------------------------------
    # Momentum indicators
    # --------------------------------------------------------

    output[f"{prefix}_RSI"] = ta.momentum.rsi(
        close,
        window=14,
    )

    output[f"{prefix}_MACD"] = ta.trend.macd(
        close
    )

    output[f"{prefix}_MACD_Signal"] = (
        ta.trend.macd_signal(close)
    )

    output[f"{prefix}_MACD_Difference"] = (
        ta.trend.macd_diff(close)
    )

    # --------------------------------------------------------
    # Volatility and range
    # --------------------------------------------------------

    return_1 = close.pct_change()

    output[f"{prefix}_Volatility_5"] = (
        return_1
        .rolling(5)
        .std()
    )

    output[f"{prefix}_Volatility_10"] = (
        return_1
        .rolling(10)
        .std()
    )

    output[f"{prefix}_Volatility_20"] = (
        return_1
        .rolling(20)
        .std()
    )

    atr = ta.volatility.average_true_range(
        high=high,
        low=low,
        close=close,
        window=14,
    )

    output[f"{prefix}_ATR"] = atr

    output[f"{prefix}_ATR_Percent"] = (
        atr / close
    )

    atr_baseline = (
        output[f"{prefix}_ATR_Percent"]
        .rolling(20)
        .mean()
    )

    output[f"{prefix}_ATR_Expansion"] = (
        output[f"{prefix}_ATR_Percent"]
        / atr_baseline
    ) - 1

    output[f"{prefix}_Range_Percent"] = (
        (high - low) / close
    )

    previous_close = close.shift(1)

    output[f"{prefix}_Gap_Percent"] = (
        open_price / previous_close
    ) - 1

    # --------------------------------------------------------
    # Drawdown
    # --------------------------------------------------------

    rolling_high_20 = (
        close
        .rolling(20)
        .max()
    )

    output[f"{prefix}_Drawdown_20"] = (
        close / rolling_high_20
    ) - 1

    rolling_high_60 = (
        close
        .rolling(60)
        .max()
    )

    output[f"{prefix}_Drawdown_60"] = (
        close / rolling_high_60
    ) - 1

    # --------------------------------------------------------
    # Volume, where available
    # --------------------------------------------------------

    if include_volume and "Volume" in df.columns:
        volume = get_numeric_series(
            df,
            "Volume",
        )

        output[f"{prefix}_Volume_Change"] = (
            volume.pct_change()
        )

        volume_average_20 = (
            volume
            .rolling(20)
            .mean()
        )

        output[f"{prefix}_Volume_Ratio_20"] = (
            volume / volume_average_20
        )

    return output


# ============================================================
# CROSS-MARKET FEATURE ENGINEERING
# ============================================================

def engineer_cross_market_features(
    combined: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build features describing relationships among SPY, QQQ,
    IWM and VIX.
    """

    combined = combined.copy()

    # --------------------------------------------------------
    # Relative strength
    # --------------------------------------------------------

    combined["SPY_vs_QQQ_Return_5"] = (
        combined["SPY_Return_5"]
        - combined["QQQ_Return_5"]
    )

    combined["SPY_vs_IWM_Return_5"] = (
        combined["SPY_Return_5"]
        - combined["IWM_Return_5"]
    )

    combined["QQQ_vs_IWM_Return_5"] = (
        combined["QQQ_Return_5"]
        - combined["IWM_Return_5"]
    )

    combined["SPY_vs_QQQ_Return_20"] = (
        combined["SPY_Return_20"]
        - combined["QQQ_Return_20"]
    )

    combined["SPY_vs_IWM_Return_20"] = (
        combined["SPY_Return_20"]
        - combined["IWM_Return_20"]
    )

    # --------------------------------------------------------
    # Breadth and participation
    # --------------------------------------------------------

    combined["Equity_Breadth_5"] = (
        (
            combined["SPY_Return_5"] > 0
        ).astype(int)
        +
        (
            combined["QQQ_Return_5"] > 0
        ).astype(int)
        +
        (
            combined["IWM_Return_5"] > 0
        ).astype(int)
    )

    combined["Equity_Breadth_20"] = (
        (
            combined["SPY_Return_20"] > 0
        ).astype(int)
        +
        (
            combined["QQQ_Return_20"] > 0
        ).astype(int)
        +
        (
            combined["IWM_Return_20"] > 0
        ).astype(int)
    )

    combined["Equity_Momentum_Average_5"] = (
        combined[
            [
                "SPY_Return_5",
                "QQQ_Return_5",
                "IWM_Return_5",
            ]
        ]
        .mean(axis=1)
    )

    combined["Equity_Momentum_Average_20"] = (
        combined[
            [
                "SPY_Return_20",
                "QQQ_Return_20",
                "IWM_Return_20",
            ]
        ]
        .mean(axis=1)
    )

    combined["Equity_Momentum_Dispersion_5"] = (
        combined[
            [
                "SPY_Return_5",
                "QQQ_Return_5",
                "IWM_Return_5",
            ]
        ]
        .std(axis=1)
    )

    # --------------------------------------------------------
    # Risk-on and risk-off context
    # --------------------------------------------------------

    combined["VIX_Change_5"] = (
        combined["VIX_Return_5"]
    )

    combined["VIX_Change_20"] = (
        combined["VIX_Return_20"]
    )

    combined["Risk_On_Score"] = (
        (
            combined["SPY_Return_5"] > 0
        ).astype(int)
        +
        (
            combined["QQQ_Return_5"] > 0
        ).astype(int)
        +
        (
            combined["IWM_Return_5"] > 0
        ).astype(int)
        +
        (
            combined["VIX_Return_5"] < 0
        ).astype(int)
    )

    combined["Risk_Off_Score"] = (
        (
            combined["SPY_Return_5"] < 0
        ).astype(int)
        +
        (
            combined["QQQ_Return_5"] < 0
        ).astype(int)
        +
        (
            combined["IWM_Return_5"] < 0
        ).astype(int)
        +
        (
            combined["VIX_Return_5"] > 0
        ).astype(int)
    )

    combined["Equity_VIX_Divergence_5"] = (
        combined["SPY_Return_5"]
        - combined["VIX_Return_5"]
    )

    combined["Equity_VIX_Divergence_20"] = (
        combined["SPY_Return_20"]
        - combined["VIX_Return_20"]
    )

    # --------------------------------------------------------
    # Volatility relationship
    # --------------------------------------------------------

    combined["SPY_VIX_Volatility_Ratio"] = (
        combined["SPY_Volatility_20"]
        /
        combined["VIX_Volatility_20"].replace(
            0,
            np.nan,
        )
    )

    return combined


# ============================================================
# TARGET ENGINEERING
# ============================================================

def create_three_state_target(
    combined: pd.DataFrame,
) -> pd.DataFrame:
    """
    Create the five-day three-state target using SPY.

    -1 = SELL
     0 = WAIT
     1 = BUY
    """

    combined = combined.copy()

    close = pd.to_numeric(
        combined["SPY_Close"],
        errors="coerce",
    )

    combined["Future_Close"] = close.shift(
        -PREDICTION_HORIZON_DAYS
    )

    combined["Future_Return"] = (
        combined["Future_Close"] / close
    ) - 1

    combined["Target"] = 0

    combined.loc[
        combined["Future_Return"]
        >= MOVEMENT_THRESHOLD,
        "Target",
    ] = 1

    combined.loc[
        combined["Future_Return"]
        <= -MOVEMENT_THRESHOLD,
        "Target",
    ] = -1

    return combined

# ============================================================
# V6 SELECTIVE SIGNAL ENGINE
# ============================================================

def get_class_probability(
    model: RandomForestClassifier,
    probabilities: np.ndarray,
    class_value: int,
) -> np.ndarray:
    """
    Return the probability column for a requested class.
    """

    class_positions = {
        class_label: position
        for position, class_label
        in enumerate(model.classes_)
    }

    if class_value not in class_positions:
        return np.zeros(len(probabilities))

    return probabilities[
        :,
        class_positions[class_value]
    ]


def create_selective_predictions(
    model: RandomForestClassifier,
    X_data: pd.DataFrame,
    buy_threshold: float,
    sell_threshold: float,
) -> np.ndarray:
    """
    Convert model probabilities into selective signals.

    BUY or SELL is accepted only when its probability exceeds
    the selected confidence threshold. All uncertain cases
    become WAIT.
    """

    probabilities = model.predict_proba(X_data)

    sell_probability = get_class_probability(
        model,
        probabilities,
        -1,
    )

    buy_probability = get_class_probability(
        model,
        probabilities,
        1,
    )

    predictions = np.zeros(
        len(X_data),
        dtype=int,
    )

    buy_mask = (
        (buy_probability >= buy_threshold)
        & (buy_probability > sell_probability)
    )

    sell_mask = (
        (sell_probability >= sell_threshold)
        & (sell_probability > buy_probability)
    )

    predictions[buy_mask] = 1
    predictions[sell_mask] = -1

    return predictions


def evaluate_selective_thresholds(
    model: RandomForestClassifier,
    X_validation: pd.DataFrame,
    y_validation: pd.Series,
) -> pd.DataFrame:
    """
    Test combinations of BUY and SELL probability thresholds.
    """

    results = []

    for buy_threshold in BUY_CONFIDENCE_THRESHOLDS:
        for sell_threshold in SELL_CONFIDENCE_THRESHOLDS:

            predictions = create_selective_predictions(
                model=model,
                X_data=X_validation,
                buy_threshold=buy_threshold,
                sell_threshold=sell_threshold,
            )

            metrics = calculate_metrics(
                y_validation,
                predictions,
            )

            buy_count = int(
                (predictions == 1).sum()
            )

            sell_count = int(
                (predictions == -1).sum()
            )

            directional_count = (
                buy_count + sell_count
            )

            directional_coverage = (
                directional_count
                / len(predictions)
                if len(predictions) > 0
                else 0.0
            )

            results.append(
                {
                    "buy_threshold":
                        buy_threshold,

                    "sell_threshold":
                        sell_threshold,

                    "balanced_accuracy":
                        metrics[
                            "balanced_accuracy"
                        ],

                    "macro_f1":
                        metrics["macro_f1"],

                    "buy_precision":
                        metrics["buy_precision"],

                    "buy_recall":
                        metrics["buy_recall"],

                    "sell_precision":
                        metrics["sell_precision"],

                    "sell_recall":
                        metrics["sell_recall"],

                    "buy_count":
                        buy_count,

                    "sell_count":
                        sell_count,

                    "directional_count":
                        directional_count,

                    "directional_coverage":
                        directional_coverage,
                }
            )

    return pd.DataFrame(results)


def select_best_thresholds(
    threshold_results: pd.DataFrame,
) -> pd.Series:
    """
    Select the strongest threshold combination without simply
    maximizing the number of trades.
    """

    eligible = threshold_results.loc[
        (
            threshold_results[
                "directional_count"
            ]
            >= MINIMUM_DIRECTIONAL_TRADES
        )
        &
        (
            threshold_results[
                "buy_precision"
            ]
            >= MINIMUM_BUY_PRECISION
        )
        &
        (
            threshold_results[
                "sell_precision"
            ]
            >= MINIMUM_SELL_PRECISION
        )
    ].copy()

    if eligible.empty:
        eligible = threshold_results.loc[
            threshold_results[
                "directional_count"
            ]
            >= MINIMUM_DIRECTIONAL_TRADES
        ].copy()

    if eligible.empty:
        eligible = threshold_results.copy()

    eligible["selection_score"] = (
        eligible["buy_precision"] * 0.30
        + eligible["sell_precision"] * 0.30
        + eligible["macro_f1"] * 0.20
        + eligible["balanced_accuracy"] * 0.15
        + eligible["directional_coverage"] * 0.05
    )

    eligible = eligible.sort_values(
        by=[
            "selection_score",
            "buy_precision",
            "sell_precision",
        ],
        ascending=False,
    )

    return eligible.iloc[0]

# ============================================================
# METRICS
# ============================================================

def calculate_metrics(
    y_true: pd.Series,
    predictions: np.ndarray,
) -> Dict[str, float]:
    """
    Calculate classification metrics.
    """

    return {
        "accuracy": accuracy_score(
            y_true,
            predictions,
        ),

        "balanced_accuracy":
            balanced_accuracy_score(
                y_true,
                predictions,
            ),

        "macro_f1": f1_score(
            y_true,
            predictions,
            average="macro",
            zero_division=0,
        ),

        "sell_precision": precision_score(
            y_true,
            predictions,
            labels=[-1],
            average="macro",
            zero_division=0,
        ),

        "sell_recall": recall_score(
            y_true,
            predictions,
            labels=[-1],
            average="macro",
            zero_division=0,
        ),

        "wait_precision": precision_score(
            y_true,
            predictions,
            labels=[0],
            average="macro",
            zero_division=0,
        ),

        "wait_recall": recall_score(
            y_true,
            predictions,
            labels=[0],
            average="macro",
            zero_division=0,
        ),

        "buy_precision": precision_score(
            y_true,
            predictions,
            labels=[1],
            average="macro",
            zero_division=0,
        ),

        "buy_recall": recall_score(
            y_true,
            predictions,
            labels=[1],
            average="macro",
            zero_division=0,
        ),
    }


def print_target_distribution(
    target: pd.Series,
) -> None:
    """
    Print class counts and percentages.
    """

    total = len(target)

    for class_value in [-1, 0, 1]:
        count = int(
            (target == class_value).sum()
        )

        percentage = (
            count / total * 100
            if total > 0
            else 0.0
        )

        print(
            f"{CLASS_NAMES[class_value]} "
            f"({class_value}): "
            f"{count} observations "
            f"({percentage:.2f}%)"
        )


# ============================================================
# MODEL
# ============================================================

def build_model() -> RandomForestClassifier:
    """
    Build the V5 research candidate model.
    """

    return RandomForestClassifier(
        n_estimators=500,
        max_depth=7,
        min_samples_leaf=12,
        max_features="sqrt",
        class_weight="balanced_subsample",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )



# ============================================================
# V7 PROFIT-AWARE WALK-FORWARD BACKTEST
# ============================================================

from dataclasses import dataclass
from typing import Optional

INITIAL_CAPITAL = 10_000.0
POSITION_SIZE_PERCENT = 0.25
MAX_HOLDING_DAYS = 5
STOP_LOSS_PERCENT = 0.03
TAKE_PROFIT_PERCENT = 0.06
COMMISSION_BPS = 1.0
SLIPPAGE_BPS = 2.0
MINIMUM_TRAINING_ROWS = 1_000
RETRAIN_INTERVAL_DAYS = 21
BACKTEST_OUTPUT_DIR = Path("research/v7_profit_backtest")

SIGNALS_PATH = BACKTEST_OUTPUT_DIR / "v7_signals.csv"
TRADES_PATH = BACKTEST_OUTPUT_DIR / "v7_trades.csv"
EQUITY_PATH = BACKTEST_OUTPUT_DIR / "v7_equity_curve.csv"
SUMMARY_PATH = BACKTEST_OUTPUT_DIR / "v7_summary.csv"


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
    return value * (COMMISSION_BPS / 10_000.0)


def build_v7_dataset() -> tuple[pd.DataFrame, list[str]]:
    """Build the exact V6 cross-market feature dataset plus SPY execution prices."""
    print("[1/8] Retrieving SPY, QQQ, IWM and VIX data...")

    market_data: Dict[str, pd.DataFrame] = {}
    for prefix, symbol in MARKET_SYMBOLS.items():
        market_data[prefix] = retrieve_market_data(symbol)
        print(
            f"PASS: {prefix:<4} rows={len(market_data[prefix])} "
            f"first={market_data[prefix].index.min()} "
            f"last={market_data[prefix].index.max()}"
        )

    print("[2/8] Engineering V6 cross-market features for V7...")

    spy_features = engineer_symbol_features(
        market_data["SPY"], prefix="SPY", include_volume=True
    )
    qqq_features = engineer_symbol_features(
        market_data["QQQ"], prefix="QQQ", include_volume=True
    )
    iwm_features = engineer_symbol_features(
        market_data["IWM"], prefix="IWM", include_volume=True
    )
    vix_features = engineer_symbol_features(
        market_data["VIX"], prefix="VIX", include_volume=False
    )

    combined = pd.concat(
        [spy_features, qqq_features, iwm_features, vix_features],
        axis=1,
        join="inner",
    ).sort_index()

    combined = engineer_cross_market_features(combined)
    combined = create_three_state_target(combined)

    # Execution prices are kept unshifted and used only after a signal is formed.
    spy_raw = flatten_market_data_columns(market_data["SPY"])
    for col in ["Open", "High", "Low", "Close"]:
        combined[f"Execution_{col}"] = pd.to_numeric(
            spy_raw[col].reindex(combined.index).squeeze(), errors="coerce"
        )

    excluded_columns = {
        "SPY_Close", "QQQ_Close", "IWM_Close", "VIX_Close",
        "Future_Close", "Future_Return", "Target",
        "Execution_Open", "Execution_High", "Execution_Low", "Execution_Close",
    }
    feature_columns: List[str] = [
        column for column in combined.columns if column not in excluded_columns
    ]

    # Match V6's conservative one-session lag.
    combined[feature_columns] = combined[feature_columns].shift(1)
    combined = combined.replace([np.inf, -np.inf], np.nan)
    combined = combined.dropna(
        subset=feature_columns
        + [
            "Future_Return", "Target",
            "Execution_Open", "Execution_High", "Execution_Low", "Execution_Close",
        ]
    ).copy()
    combined["Target"] = combined["Target"].astype(int)

    if len(combined) <= MINIMUM_TRAINING_ROWS + PREDICTION_HORIZON_DAYS + 2:
        raise RuntimeError("Insufficient aligned observations for the V7 walk-forward test.")

    print(f"Feature count: {len(feature_columns)}")
    print(f"Usable aligned observations: {len(combined)}")
    print(f"Period: {combined.index.min()} -> {combined.index.max()}")
    return combined, feature_columns


def calibrate_thresholds(
    X_available: pd.DataFrame,
    y_available: pd.Series,
) -> tuple[float, float]:
    """Select thresholds using only historical data available at the retraining date."""
    calibration_size = max(int(len(X_available) * 0.20), 80)
    if calibration_size >= len(X_available):
        calibration_size = max(int(len(X_available) * 0.15), 20)

    split_index = len(X_available) - calibration_size
    if split_index <= 50:
        return 0.40, 0.40

    model = build_model()
    model.fit(X_available.iloc[:split_index], y_available.iloc[:split_index])

    threshold_results = evaluate_selective_thresholds(
        model=model,
        X_validation=X_available.iloc[split_index:],
        y_validation=y_available.iloc[split_index:],
    )
    best = select_best_thresholds(threshold_results)
    return float(best["buy_threshold"]), float(best["sell_threshold"])


def generate_walk_forward_signals(
    data: pd.DataFrame,
    feature_columns: list[str],
) -> pd.DataFrame:
    """Generate expanding-window signals without training on unavailable future labels."""
    print("[3/8] Generating purged walk-forward selective signals...")

    signal_rows: list[dict] = []
    model: Optional[RandomForestClassifier] = None
    buy_threshold = 0.40
    sell_threshold = 0.40
    last_retrain_index: Optional[int] = None

    first_signal_index = MINIMUM_TRAINING_ROWS + PREDICTION_HORIZON_DAYS

    for row_index in range(first_signal_index, len(data) - 1):
        # Purge the most recent horizon rows because their labels use future prices.
        training_end = row_index - PREDICTION_HORIZON_DAYS
        must_retrain = (
            model is None
            or last_retrain_index is None
            or row_index - last_retrain_index >= RETRAIN_INTERVAL_DAYS
        )

        if must_retrain:
            historical = data.iloc[:training_end].copy()
            X_available = historical[feature_columns]
            y_available = historical["Target"]

            buy_threshold, sell_threshold = calibrate_thresholds(
                X_available, y_available
            )

            model = build_model()
            model.fit(X_available, y_available)
            last_retrain_index = row_index

        current = data.iloc[row_index]
        next_row = data.iloc[row_index + 1]
        feature_frame = current[feature_columns].to_frame().T
        probabilities = model.predict_proba(feature_frame)[0]

        sell_probability = get_class_probability(model, probabilities.reshape(1, -1), -1)[0]
        wait_probability = get_class_probability(model, probabilities.reshape(1, -1), 0)[0]
        buy_probability = get_class_probability(model, probabilities.reshape(1, -1), 1)[0]

        signal = "WAIT"
        if buy_probability >= buy_threshold and buy_probability > sell_probability:
            signal = "BUY"
        elif sell_probability >= sell_threshold and sell_probability > buy_probability:
            signal = "SELL"

        signal_rows.append(
            {
                "Signal_Date": current.name,
                "Execution_Date": next_row.name,
                "Execution_Open": float(next_row["Execution_Open"]),
                "Execution_High": float(next_row["Execution_High"]),
                "Execution_Low": float(next_row["Execution_Low"]),
                "Execution_Close": float(next_row["Execution_Close"]),
                "Signal": signal,
                "BUY_Probability": float(buy_probability),
                "SELL_Probability": float(sell_probability),
                "WAIT_Probability": float(wait_probability),
                "BUY_Threshold": buy_threshold,
                "SELL_Threshold": sell_threshold,
                "True_Target": int(current["Target"]),
            }
        )

    signals = pd.DataFrame(signal_rows).set_index("Execution_Date")
    if signals.empty:
        raise RuntimeError("V7 produced no walk-forward signals.")

    print(f"Walk-forward observations: {len(signals)}")
    print(signals["Signal"].value_counts().to_string())
    return signals


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
        "Return_Percent": net_profit / position.total_entry_cost,
        "Holding_Days": position.holding_days,
        "Exit_Reason": reason,
    }


def run_trading_simulation(signals: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Simulate one long SPY position at a time with costs and risk exits."""
    print("[4/8] Simulating trades, costs and risk exits...")
    cash = INITIAL_CAPITAL
    position: Optional[Position] = None
    trades: list[dict] = []
    equity_rows: list[dict] = []

    for date, row in signals.iterrows():
        open_price = float(row["Execution_Open"])
        high_price = float(row["Execution_High"])
        low_price = float(row["Execution_Low"])
        close_price = float(row["Execution_Close"])
        signal = str(row["Signal"])

        # Existing positions are evaluated from the session open onward.
        if position is not None:
            position.holding_days += 1
            stop_price = position.entry_price * (1.0 - STOP_LOSS_PERCENT)
            take_price = position.entry_price * (1.0 + TAKE_PROFIT_PERCENT)

            reason: Optional[str] = None
            exit_market_price: Optional[float] = None
            if low_price <= stop_price:
                reason, exit_market_price = "STOP_LOSS", stop_price
            elif high_price >= take_price:
                reason, exit_market_price = "TAKE_PROFIT", take_price
            elif signal == "SELL":
                reason, exit_market_price = "SELL_SIGNAL", open_price
            elif position.holding_days >= MAX_HOLDING_DAYS:
                reason, exit_market_price = "MAX_HOLDING", close_price

            if reason is not None and exit_market_price is not None:
                net_value, trade = close_position(
                    position, date, exit_market_price, reason
                )
                cash += net_value
                trades.append(trade)
                position = None

        # A BUY signal can open a fresh position after any exit at the open.
        if position is None and signal == "BUY":
            allocation = cash * POSITION_SIZE_PERCENT
            entry_price = apply_buy_slippage(open_price)
            investable = allocation / (1.0 + COMMISSION_BPS / 10_000.0)
            shares = investable / entry_price if entry_price > 0 else 0.0
            gross_entry = shares * entry_price
            entry_commission = commission(gross_entry)
            total_cost = gross_entry + entry_commission

            if shares > 0 and total_cost <= cash:
                cash -= total_cost
                position = Position(date, entry_price, shares, total_cost, 0)

                # Conservative intraday handling: if both levels occur, stop is assumed first.
                stop_price = entry_price * (1.0 - STOP_LOSS_PERCENT)
                take_price = entry_price * (1.0 + TAKE_PROFIT_PERCENT)
                if low_price <= stop_price:
                    net_value, trade = close_position(
                        position, date, stop_price, "ENTRY_DAY_STOP"
                    )
                    cash += net_value
                    trades.append(trade)
                    position = None
                elif high_price >= take_price:
                    net_value, trade = close_position(
                        position, date, take_price, "ENTRY_DAY_TAKE_PROFIT"
                    )
                    cash += net_value
                    trades.append(trade)
                    position = None

        position_value = position.shares * close_price if position is not None else 0.0
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

    if position is not None:
        final_date = signals.index[-1]
        final_close = float(signals.iloc[-1]["Execution_Close"])
        net_value, trade = close_position(
            position, final_date, final_close, "END_OF_BACKTEST"
        )
        cash += net_value
        trades.append(trade)
        equity_rows[-1].update(
            {
                "Cash": cash,
                "Position_Value": 0.0,
                "Total_Equity": cash,
                "In_Position": False,
            }
        )

    trades_df = pd.DataFrame(trades)
    equity_df = pd.DataFrame(equity_rows).set_index("Date")
    print(f"Completed trades: {len(trades_df)}")
    return trades_df, equity_df


def max_drawdown(equity: pd.Series) -> float:
    return float((equity / equity.cummax() - 1.0).min())


def calculate_performance(
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    equity: pd.DataFrame,
) -> dict:
    print("[5/8] Calculating economic performance...")
    ending_capital = float(equity["Total_Equity"].iloc[-1])
    total_return = ending_capital / INITIAL_CAPITAL - 1.0
    daily_returns = equity["Total_Equity"].pct_change().dropna()

    elapsed_years = max(
        (equity.index[-1] - equity.index[0]).days / 365.25,
        1.0 / 365.25,
    )
    annualised_return = (ending_capital / INITIAL_CAPITAL) ** (1.0 / elapsed_years) - 1.0
    annualised_volatility = float(daily_returns.std() * np.sqrt(252)) if len(daily_returns) else 0.0
    sharpe = (
        float(daily_returns.mean() / daily_returns.std() * np.sqrt(252))
        if len(daily_returns) and daily_returns.std() > 0
        else 0.0
    )
    downside = daily_returns[daily_returns < 0]
    sortino = (
        float(daily_returns.mean() / downside.std() * np.sqrt(252))
        if len(downside) and downside.std() > 0
        else 0.0
    )

    if trades.empty:
        winners = trades.copy()
        losers = trades.copy()
    else:
        winners = trades[trades["Net_Profit"] > 0]
        losers = trades[trades["Net_Profit"] < 0]

    gross_profit = float(winners["Net_Profit"].sum()) if not winners.empty else 0.0
    gross_loss = abs(float(losers["Net_Profit"].sum())) if not losers.empty else 0.0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else (float("inf") if gross_profit > 0 else 0.0)

    benchmark_start = float(signals.iloc[0]["Execution_Open"])
    benchmark_end = float(signals.iloc[-1]["Execution_Close"])
    benchmark_return = benchmark_end / benchmark_start - 1.0

    return {
        "starting_capital": INITIAL_CAPITAL,
        "ending_capital": ending_capital,
        "net_profit": ending_capital - INITIAL_CAPITAL,
        "total_return": total_return,
        "annualised_return": annualised_return,
        "annualised_volatility": annualised_volatility,
        "sharpe_ratio": sharpe,
        "sortino_ratio": sortino,
        "maximum_drawdown": max_drawdown(equity["Total_Equity"]),
        "total_trades": int(len(trades)),
        "winning_trades": int(len(winners)),
        "losing_trades": int(len(losers)),
        "win_rate": float(len(winners) / len(trades)) if len(trades) else 0.0,
        "average_win": float(winners["Net_Profit"].mean()) if not winners.empty else 0.0,
        "average_loss": float(losers["Net_Profit"].mean()) if not losers.empty else 0.0,
        "profit_factor": profit_factor,
        "expectancy": float(trades["Net_Profit"].mean()) if not trades.empty else 0.0,
        "market_exposure": float(equity["In_Position"].mean()),
        "benchmark_return": benchmark_return,
        "excess_return": total_return - benchmark_return,
    }


def display_performance(performance: dict) -> None:
    print("\n" + "=" * 76)
    print("V7 PROFIT-AWARE BACKTEST RESULTS")
    print("=" * 76)
    money_fields = ["starting_capital", "ending_capital", "net_profit", "average_win", "average_loss", "expectancy"]
    percent_fields = ["total_return", "annualised_return", "annualised_volatility", "maximum_drawdown", "win_rate", "market_exposure", "benchmark_return", "excess_return"]
    labels = {
        "starting_capital": "Starting Capital", "ending_capital": "Ending Capital",
        "net_profit": "Net Profit", "total_return": "Total Return",
        "annualised_return": "Annualised Return", "annualised_volatility": "Annualised Volatility",
        "maximum_drawdown": "Maximum Drawdown", "total_trades": "Total Trades",
        "winning_trades": "Winning Trades", "losing_trades": "Losing Trades",
        "win_rate": "Win Rate", "average_win": "Average Win", "average_loss": "Average Loss",
        "profit_factor": "Profit Factor", "expectancy": "Expectancy Per Trade",
        "market_exposure": "Market Exposure", "benchmark_return": "SPY Buy-and-Hold Return",
        "excess_return": "AI Excess Return", "sharpe_ratio": "Sharpe Ratio", "sortino_ratio": "Sortino Ratio",
    }
    order = [
        "starting_capital", "ending_capital", "net_profit", "total_return",
        "annualised_return", "annualised_volatility", "sharpe_ratio", "sortino_ratio",
        "maximum_drawdown", "total_trades", "winning_trades", "losing_trades",
        "win_rate", "average_win", "average_loss", "profit_factor", "expectancy",
        "market_exposure", "benchmark_return", "excess_return",
    ]
    for key in order:
        value = performance[key]
        if key in money_fields:
            rendered = f"${value:,.2f}"
        elif key in percent_fields:
            rendered = f"{value:.2%}"
        elif key == "profit_factor" and np.isinf(value):
            rendered = "INF"
        elif isinstance(value, int):
            rendered = str(value)
        else:
            rendered = f"{value:.3f}"
        print(f"{labels[key]}: {rendered}")
    print("=" * 76)


def save_results(signals: pd.DataFrame, trades: pd.DataFrame, equity: pd.DataFrame, performance: dict) -> None:
    print("[6/8] Saving V7 outputs...")
    BACKTEST_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    signals.to_csv(SIGNALS_PATH)
    trades.to_csv(TRADES_PATH, index=False)
    equity.to_csv(EQUITY_PATH)
    pd.DataFrame([performance]).to_csv(SUMMARY_PATH, index=False)
    print(f"Signals: {SIGNALS_PATH}")
    print(f"Trades: {TRADES_PATH}")
    print(f"Equity curve: {EQUITY_PATH}")
    print(f"Summary: {SUMMARY_PATH}")


def evaluate_economic_gate(performance: dict) -> None:
    print("[7/8] Running V7 economic research gate...")
    gates = {
        "Positive net profit": performance["net_profit"] > 0,
        "Positive annualised return": performance["annualised_return"] > 0,
        "Sharpe ratio at least 0.50": performance["sharpe_ratio"] >= 0.50,
        "Profit factor at least 1.10": performance["profit_factor"] >= 1.10,
        "Maximum drawdown no worse than -20%": performance["maximum_drawdown"] >= -0.20,
        "At least 30 completed trades": performance["total_trades"] >= 30,
        "Positive expectancy": performance["expectancy"] > 0,
    }
    for name, passed in gates.items():
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    print("\n" + "=" * 76)
    if all(gates.values()):
        print("V7 ECONOMIC RESEARCH RESULT: APPROVED")
        print("Approved for further walk-forward and paper-trading research only.")
    else:
        print("V7 ECONOMIC RESEARCH RESULT: REJECTED")
        print("The production model and Alpaca execution system were not modified.")
    print("=" * 76)


def main() -> None:
    print("=" * 76)
    print("AI TRADING MACHINE — V7 PROFIT-AWARE WALK-FORWARD BACKTEST")
    print("=" * 76)
    dataset, feature_columns = build_v7_dataset()
    signals = generate_walk_forward_signals(dataset, feature_columns)
    trades, equity = run_trading_simulation(signals)
    performance = calculate_performance(signals, trades, equity)
    display_performance(performance)
    save_results(signals, trades, equity, performance)
    evaluate_economic_gate(performance)
    print("[8/8] V7 completed successfully.")


if __name__ == "__main__":
    main()
