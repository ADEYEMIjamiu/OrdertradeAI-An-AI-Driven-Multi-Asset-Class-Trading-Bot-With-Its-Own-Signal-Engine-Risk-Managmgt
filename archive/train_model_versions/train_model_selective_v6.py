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
    Build the V6 selective research candidate model.
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
# MAIN EXPERIMENT
# ============================================================

print()
print("=" * 76)
print("MODEL EDGE DEVELOPMENT V6")
print("SELECTIVE CROSS-MARKET INTELLIGENCE EXPERIMENT")
print("=" * 76)
print()


# ============================================================
# 1/11 RETRIEVE DATA
# ============================================================

print("[1/11] Retrieving cross-market historical data...")

market_data: Dict[str, pd.DataFrame] = {}

for prefix, symbol in MARKET_SYMBOLS.items():
    print(
        f"Retrieving {prefix} using symbol {symbol}..."
    )

    market_data[prefix] = retrieve_market_data(
        symbol
    )

    print(
        f"PASS: {prefix:<4} "
        f"rows={len(market_data[prefix])} "
        f"first={market_data[prefix].index.min()} "
        f"last={market_data[prefix].index.max()}"
    )

print()


# ============================================================
# 2/11 ENGINEER INDIVIDUAL MARKET FEATURES
# ============================================================

print(
    "[2/11] Engineering individual-market features..."
)

spy_features = engineer_symbol_features(
    market_data["SPY"],
    prefix="SPY",
    include_volume=True,
)

qqq_features = engineer_symbol_features(
    market_data["QQQ"],
    prefix="QQQ",
    include_volume=True,
)

iwm_features = engineer_symbol_features(
    market_data["IWM"],
    prefix="IWM",
    include_volume=True,
)

vix_features = engineer_symbol_features(
    market_data["VIX"],
    prefix="VIX",
    include_volume=False,
)

print(
    "SPY feature columns:",
    len(spy_features.columns),
)

print(
    "QQQ feature columns:",
    len(qqq_features.columns),
)

print(
    "IWM feature columns:",
    len(iwm_features.columns),
)

print(
    "VIX feature columns:",
    len(vix_features.columns),
)

print()


# ============================================================
# 3/11 ALIGN MARKETS
# ============================================================

print("[3/11] Aligning all markets by trading date...")

combined = pd.concat(
    [
        spy_features,
        qqq_features,
        iwm_features,
        vix_features,
    ],
    axis=1,
    join="inner",
)

combined = combined.sort_index()

print("Aligned rows:", len(combined))
print("Aligned first date:", combined.index.min())
print("Aligned last date:", combined.index.max())
print()


# ============================================================
# 4/11 ENGINEER CROSS-MARKET FEATURES
# ============================================================

print(
    "[4/11] Engineering cross-market intelligence..."
)

combined = engineer_cross_market_features(
    combined
)

print(
    "Total columns after cross-market engineering:",
    len(combined.columns),
)

print()


# ============================================================
# 5/11 CREATE TARGET
# ============================================================

print(
    "[5/11] Creating five-day SPY trading target..."
)

print(
    "Prediction horizon:",
    PREDICTION_HORIZON_DAYS,
    "trading days",
)

print(
    "Directional threshold:",
    f"{MOVEMENT_THRESHOLD:.2%}",
)

combined = create_three_state_target(
    combined
)

print()


# ============================================================
# 6/11 CREATE LEAKAGE-SAFE FEATURE CONTRACT
# ============================================================

print(
    "[6/11] Creating leakage-safe feature contract..."
)

excluded_columns = {
    "SPY_Close",
    "QQQ_Close",
    "IWM_Close",
    "VIX_Close",
    "Future_Close",
    "Future_Return",
    "Target",
}

feature_columns: List[str] = [
    column
    for column in combined.columns
    if column not in excluded_columns
]

# Shift all features by one trading session.
# This ensures the model only uses information available
# before the target measurement begins.
combined[feature_columns] = (
    combined[feature_columns]
    .shift(1)
)

required_columns = (
    feature_columns
    + [
        "Future_Return",
        "Target",
    ]
)

combined = combined.replace(
    [np.inf, -np.inf],
    np.nan,
)

combined = combined.dropna(
    subset=required_columns
).copy()

print("Candidate feature count:", len(feature_columns))
print("Usable observations:", len(combined))
print()

print("Target distribution:")

print_target_distribution(
    combined["Target"]
)

print()


# ============================================================
# 7/11 CHRONOLOGICAL HOLDOUT
# ============================================================

print(
    "[7/11] Creating chronological holdout test..."
)

X = combined[feature_columns].copy()
y = combined["Target"].astype(int).copy()

holdout_size = int(
    len(combined) * HOLDOUT_PERCENT
)

split_index = (
    len(combined) - holdout_size
)

X_train = X.iloc[:split_index].copy()
X_holdout = X.iloc[split_index:].copy()

y_train = y.iloc[:split_index].copy()
y_holdout = y.iloc[split_index:].copy()

print("Training rows:", len(X_train))
print("Holdout rows:", len(X_holdout))

print(
    "Training period:",
    X_train.index.min(),
    "->",
    X_train.index.max(),
)

print(
    "Holdout period:",
    X_holdout.index.min(),
    "->",
    X_holdout.index.max(),
)

print()


# ============================================================
# BASELINE
# ============================================================

majority_class = (
    y_train
    .value_counts()
    .idxmax()
)

baseline_predictions = np.full(
    len(y_holdout),
    majority_class,
)

baseline_metrics = calculate_metrics(
    y_holdout,
    baseline_predictions,
)

print("Majority-class baseline:")

print(
    "Predicted class:",
    CLASS_NAMES[majority_class],
)

print(
    "Baseline accuracy:",
    f"{baseline_metrics['accuracy']:.2%}",
)

print(
    "Baseline balanced accuracy:",
    f"{baseline_metrics['balanced_accuracy']:.2%}",
)

print(
    "Baseline macro F1:",
    f"{baseline_metrics['macro_f1']:.2%}",
)

print()


# ============================================================
# ============================================================
# 8/11 NESTED TIME-SERIES CROSS-VALIDATION
# ============================================================

print(
    "[8/11] Running selective time-series "
    "cross-validation..."
)

time_series_split = TimeSeriesSplit(
    n_splits=N_SPLITS
)

cv_balanced_accuracy = []
cv_macro_f1 = []
cv_sell_precision = []
cv_buy_precision = []
cv_directional_coverage = []
cv_buy_thresholds = []
cv_sell_thresholds = []

for fold_number, (
    fold_train_index,
    fold_validation_index,
) in enumerate(
    time_series_split.split(X_train),
    start=1,
):
    X_fold_full_train = X_train.iloc[
        fold_train_index
    ].copy()

    y_fold_full_train = y_train.iloc[
        fold_train_index
    ].copy()

    X_fold_validation = X_train.iloc[
        fold_validation_index
    ].copy()

    y_fold_validation = y_train.iloc[
        fold_validation_index
    ].copy()

    # --------------------------------------------------------
    # Create an internal calibration split from the end of
    # each fold's training period.
    #
    # The fold validation period remains untouched while
    # confidence thresholds are selected.
    # --------------------------------------------------------

    fold_calibration_size = max(
        int(len(X_fold_full_train) * 0.20),
        30,
    )

    fold_model_split_index = (
        len(X_fold_full_train)
        - fold_calibration_size
    )

    if fold_model_split_index <= 0:
        raise RuntimeError(
            "Insufficient training observations for nested "
            f"calibration in fold {fold_number}."
        )

    X_fold_model_train = X_fold_full_train.iloc[
        :fold_model_split_index
    ].copy()

    y_fold_model_train = y_fold_full_train.iloc[
        :fold_model_split_index
    ].copy()

    X_fold_calibration = X_fold_full_train.iloc[
        fold_model_split_index:
    ].copy()

    y_fold_calibration = y_fold_full_train.iloc[
        fold_model_split_index:
    ].copy()

    # --------------------------------------------------------
    # Train the fold model only on the earlier sub-period.
    # --------------------------------------------------------

    fold_model = build_model()

    fold_model.fit(
        X_fold_model_train,
        y_fold_model_train,
    )

    # --------------------------------------------------------
    # Select confidence thresholds using calibration data.
    # --------------------------------------------------------

    fold_threshold_results = (
        evaluate_selective_thresholds(
            model=fold_model,
            X_validation=X_fold_calibration,
            y_validation=y_fold_calibration,
        )
    )

    fold_best_thresholds = select_best_thresholds(
        fold_threshold_results
    )

    fold_buy_threshold = float(
        fold_best_thresholds[
            "buy_threshold"
        ]
    )

    fold_sell_threshold = float(
        fold_best_thresholds[
            "sell_threshold"
        ]
    )

    # --------------------------------------------------------
    # Evaluate once on the untouched fold validation period.
    # --------------------------------------------------------

    fold_predictions = create_selective_predictions(
        model=fold_model,
        X_data=X_fold_validation,
        buy_threshold=fold_buy_threshold,
        sell_threshold=fold_sell_threshold,
    )

    fold_metrics = calculate_metrics(
        y_fold_validation,
        fold_predictions,
    )

    fold_buy_count = int(
        (fold_predictions == 1).sum()
    )

    fold_sell_count = int(
        (fold_predictions == -1).sum()
    )

    fold_directional_count = (
        fold_buy_count
        + fold_sell_count
    )

    fold_directional_coverage = (
        fold_directional_count
        / len(fold_predictions)
        if len(fold_predictions) > 0
        else 0.0
    )

    cv_balanced_accuracy.append(
        fold_metrics[
            "balanced_accuracy"
        ]
    )

    cv_macro_f1.append(
        fold_metrics["macro_f1"]
    )

    cv_sell_precision.append(
        fold_metrics["sell_precision"]
    )

    cv_buy_precision.append(
        fold_metrics["buy_precision"]
    )

    cv_directional_coverage.append(
        fold_directional_coverage
    )

    cv_buy_thresholds.append(
        fold_buy_threshold
    )

    cv_sell_thresholds.append(
        fold_sell_threshold
    )

    print(
        f"Fold {fold_number}: "
        f"BUY Threshold="
        f"{fold_buy_threshold:.2f} | "
        f"SELL Threshold="
        f"{fold_sell_threshold:.2f} | "
        f"Balanced Accuracy="
        f"{fold_metrics['balanced_accuracy']:.2%} | "
        f"Macro F1="
        f"{fold_metrics['macro_f1']:.2%} | "
        f"SELL Precision="
        f"{fold_metrics['sell_precision']:.2%} | "
        f"BUY Precision="
        f"{fold_metrics['buy_precision']:.2%} | "
        f"Coverage="
        f"{fold_directional_coverage:.2%}"
    )


mean_cv_balanced_accuracy = float(
    np.mean(cv_balanced_accuracy)
)

mean_cv_macro_f1 = float(
    np.mean(cv_macro_f1)
)

mean_cv_sell_precision = float(
    np.mean(cv_sell_precision)
)

mean_cv_buy_precision = float(
    np.mean(cv_buy_precision)
)

mean_cv_directional_coverage = float(
    np.mean(cv_directional_coverage)
)

mean_cv_buy_threshold = float(
    np.mean(cv_buy_thresholds)
)

mean_cv_sell_threshold = float(
    np.mean(cv_sell_thresholds)
)

print()
print("Selective cross-validation averages:")

print(
    "Mean balanced accuracy:",
    f"{mean_cv_balanced_accuracy:.2%}",
)

print(
    "Mean macro F1:",
    f"{mean_cv_macro_f1:.2%}",
)

print(
    "Mean SELL precision:",
    f"{mean_cv_sell_precision:.2%}",
)

print(
    "Mean BUY precision:",
    f"{mean_cv_buy_precision:.2%}",
)

print(
    "Mean directional coverage:",
    f"{mean_cv_directional_coverage:.2%}",
)

print(
    "Mean selected BUY threshold:",
    f"{mean_cv_buy_threshold:.2f}",
)

print(
    "Mean selected SELL threshold:",
    f"{mean_cv_sell_threshold:.2f}",
)

print()


# ============================================================
# 9/11 THRESHOLD CALIBRATION AND HOLDOUT EVALUATION
# ============================================================

print(
    "[9/11] Calibrating V6 confidence thresholds..."
)

# ------------------------------------------------------------
# Divide the original training period into:
#
# 1. model-development data;
# 2. threshold-calibration data.
#
# The final holdout remains completely untouched.
# ------------------------------------------------------------

calibration_size = max(
    int(len(X_train) * 0.20),
    50,
)

model_development_split_index = (
    len(X_train)
    - calibration_size
)

if model_development_split_index <= 0:
    raise RuntimeError(
        "Insufficient training data for V6 threshold "
        "calibration."
    )

X_model_development = X_train.iloc[
    :model_development_split_index
].copy()

y_model_development = y_train.iloc[
    :model_development_split_index
].copy()

X_threshold_calibration = X_train.iloc[
    model_development_split_index:
].copy()

y_threshold_calibration = y_train.iloc[
    model_development_split_index:
].copy()

print(
    "Model-development rows:",
    len(X_model_development),
)

print(
    "Threshold-calibration rows:",
    len(X_threshold_calibration),
)

print(
    "Model-development period:",
    X_model_development.index.min(),
    "->",
    X_model_development.index.max(),
)

print(
    "Threshold-calibration period:",
    X_threshold_calibration.index.min(),
    "->",
    X_threshold_calibration.index.max(),
)

print()


# ------------------------------------------------------------
# Train calibration model.
# ------------------------------------------------------------

calibration_model = build_model()

calibration_model.fit(
    X_model_development,
    y_model_development,
)

# ------------------------------------------------------------
# Evaluate every threshold combination using calibration data.
# ------------------------------------------------------------

threshold_selection_results = (
    evaluate_selective_thresholds(
        model=calibration_model,
        X_validation=X_threshold_calibration,
        y_validation=y_threshold_calibration,
    )
)

best_thresholds = select_best_thresholds(
    threshold_selection_results
)

selected_buy_threshold = float(
    best_thresholds[
        "buy_threshold"
    ]
)

selected_sell_threshold = float(
    best_thresholds[
        "sell_threshold"
    ]
)

selected_calibration_buy_precision = float(
    best_thresholds[
        "buy_precision"
    ]
)

selected_calibration_sell_precision = float(
    best_thresholds[
        "sell_precision"
    ]
)

selected_calibration_balanced_accuracy = float(
    best_thresholds[
        "balanced_accuracy"
    ]
)

selected_calibration_macro_f1 = float(
    best_thresholds[
        "macro_f1"
    ]
)

selected_calibration_directional_count = int(
    best_thresholds[
        "directional_count"
    ]
)

selected_calibration_coverage = float(
    best_thresholds[
        "directional_coverage"
    ]
)

print("=" * 76)
print("SELECTED V6 CONFIDENCE THRESHOLDS")
print("=" * 76)

print(
    "Selected BUY threshold:",
    f"{selected_buy_threshold:.2f}",
)

print(
    "Selected SELL threshold:",
    f"{selected_sell_threshold:.2f}",
)

print(
    "Calibration BUY precision:",
    f"{selected_calibration_buy_precision:.2%}",
)

print(
    "Calibration SELL precision:",
    f"{selected_calibration_sell_precision:.2%}",
)

print(
    "Calibration balanced accuracy:",
    f"{selected_calibration_balanced_accuracy:.2%}",
)

print(
    "Calibration macro F1:",
    f"{selected_calibration_macro_f1:.2%}",
)

print(
    "Calibration directional trades:",
    selected_calibration_directional_count,
)

print(
    "Calibration directional coverage:",
    f"{selected_calibration_coverage:.2%}",
)

print()


# ------------------------------------------------------------
# Display the strongest threshold combinations for inspection.
# ------------------------------------------------------------

threshold_display = (
    threshold_selection_results
    .sort_values(
        by=[
            "buy_precision",
            "sell_precision",
            "macro_f1",
            "balanced_accuracy",
        ],
        ascending=False,
    )
    .head(10)
    .copy()
)

print("=" * 76)
print("TOP 10 V6 THRESHOLD COMBINATIONS")
print("=" * 76)

for _, threshold_row in threshold_display.iterrows():
    print(
        "BUY="
        f"{threshold_row['buy_threshold']:.2f} | "
        "SELL="
        f"{threshold_row['sell_threshold']:.2f} | "
        "BUY Precision="
        f"{threshold_row['buy_precision']:.2%} | "
        "SELL Precision="
        f"{threshold_row['sell_precision']:.2%} | "
        "Macro F1="
        f"{threshold_row['macro_f1']:.2%} | "
        "Balanced Accuracy="
        f"{threshold_row['balanced_accuracy']:.2%} | "
        "Trades="
        f"{int(threshold_row['directional_count'])} | "
        "Coverage="
        f"{threshold_row['directional_coverage']:.2%}"
    )

print()


# ------------------------------------------------------------
# Retrain the final candidate using all pre-holdout training
# data. The thresholds are now frozen.
# ------------------------------------------------------------

print(
    "Training final V6 candidate on all "
    "pre-holdout observations..."
)

candidate_model = build_model()

candidate_model.fit(
    X_train,
    y_train,
)

# ------------------------------------------------------------
# Apply the fixed thresholds once to the untouched holdout.
# ------------------------------------------------------------

holdout_predictions = create_selective_predictions(
    model=candidate_model,
    X_data=X_holdout,
    buy_threshold=selected_buy_threshold,
    sell_threshold=selected_sell_threshold,
)

candidate_metrics = calculate_metrics(
    y_holdout,
    holdout_predictions,
)

holdout_buy_count = int(
    (holdout_predictions == 1).sum()
)

holdout_sell_count = int(
    (holdout_predictions == -1).sum()
)

holdout_wait_count = int(
    (holdout_predictions == 0).sum()
)

holdout_directional_count = (
    holdout_buy_count
    + holdout_sell_count
)

holdout_directional_coverage = (
    holdout_directional_count
    / len(holdout_predictions)
    if len(holdout_predictions) > 0
    else 0.0
)

print()
print("=" * 76)
print("SELECTIVE V6 HOLDOUT RESULTS")
print("=" * 76)

print(
    "Fixed BUY threshold:",
    f"{selected_buy_threshold:.2f}",
)

print(
    "Fixed SELL threshold:",
    f"{selected_sell_threshold:.2f}",
)

print(
    "Accuracy:",
    f"{candidate_metrics['accuracy']:.2%}",
)

print(
    "Balanced Accuracy:",
    f"{candidate_metrics['balanced_accuracy']:.2%}",
)

print(
    "Macro F1:",
    f"{candidate_metrics['macro_f1']:.2%}",
)

print(
    "Baseline Accuracy:",
    f"{baseline_metrics['accuracy']:.2%}",
)

print(
    "Baseline Balanced Accuracy:",
    f"{baseline_metrics['balanced_accuracy']:.2%}",
)

print(
    "SELL Precision:",
    f"{candidate_metrics['sell_precision']:.2%}",
)

print(
    "SELL Recall:",
    f"{candidate_metrics['sell_recall']:.2%}",
)

print(
    "WAIT Precision:",
    f"{candidate_metrics['wait_precision']:.2%}",
)

print(
    "WAIT Recall:",
    f"{candidate_metrics['wait_recall']:.2%}",
)

print(
    "BUY Precision:",
    f"{candidate_metrics['buy_precision']:.2%}",
)

print(
    "BUY Recall:",
    f"{candidate_metrics['buy_recall']:.2%}",
)

print()
print("Selective signal counts:")

print(
    "SELL signals:",
    holdout_sell_count,
)

print(
    "WAIT signals:",
    holdout_wait_count,
)

print(
    "BUY signals:",
    holdout_buy_count,
)

print(
    "Directional signals:",
    holdout_directional_count,
)

print(
    "Directional coverage:",
    f"{holdout_directional_coverage:.2%}",
)

print()
print("Confusion Matrix:")

print(
    confusion_matrix(
        y_holdout,
        holdout_predictions,
        labels=[-1, 0, 1],
    )
)

print()
print("Classification Report:")

print(
    classification_report(
        y_holdout,
        holdout_predictions,
        labels=[-1, 0, 1],
        target_names=[
            "SELL",
            "WAIT",
            "BUY",
        ],
        zero_division=0,
    )
)

# ============================================================
# 10/11 FEATURE IMPORTANCE AND V6 RESEARCH GATES
# ============================================================

print("=" * 76)
print("TOP 20 V6 FEATURE IMPORTANCES")
print("=" * 76)

feature_importance = pd.Series(
    candidate_model.feature_importances_,
    index=feature_columns,
).sort_values(
    ascending=False
)

for rank, (
    feature_name,
    importance,
) in enumerate(
    feature_importance.head(20).items(),
    start=1,
):
    print(
        f"{rank:02d}. "
        f"{feature_name:<34} "
        f"{importance:.4f}"
    )

print()
print("[10/11] Running V6 research candidate gate...")

class_counts = y.value_counts()

adequate_class_support = all(
    class_counts.get(
        class_value,
        0,
    ) >= 100
    for class_value in [-1, 0, 1]
)

gates = {
    "Adequate class support":
        adequate_class_support,

    "Holdout balanced accuracy acceptable":
        candidate_metrics[
            "balanced_accuracy"
        ] >= 0.40,

    "Holdout macro F1 acceptable":
        candidate_metrics[
            "macro_f1"
        ] >= 0.37,

    "Holdout SELL precision acceptable":
        candidate_metrics[
            "sell_precision"
        ] >= MINIMUM_SELL_PRECISION,

    "Holdout BUY precision acceptable":
        candidate_metrics[
            "buy_precision"
        ] >= MINIMUM_BUY_PRECISION,

    "Enough holdout directional trades":
        holdout_directional_count
        >= MINIMUM_DIRECTIONAL_TRADES,

    "CV balanced accuracy acceptable":
        mean_cv_balanced_accuracy
        >= 0.40,

    "CV macro F1 acceptable":
        mean_cv_macro_f1
        >= 0.35,

    "CV directional coverage acceptable":
        mean_cv_directional_coverage
        >= 0.05,
}

for gate_name, gate_passed in gates.items():
    status = (
        "PASS"
        if gate_passed
        else "FAIL"
    )

    print(
        f"{status}: {gate_name}"
    )

all_gates_passed = all(
    gates.values()
)

print()


# ============================================================
# 11/11 SAVE V6 RESEARCH CANDIDATE
# ============================================================

print(
    "[11/11] Finalising V6 research candidate..."
)

RESEARCH_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

if all_gates_passed:
    joblib.dump(
        candidate_model,
        CANDIDATE_MODEL_PATH,
    )

    joblib.dump(
        feature_columns,
        CANDIDATE_FEATURES_PATH,
    )

    metadata = {
        "experiment":
            "selective_signal_v6",

        "primary_symbol":
            PRIMARY_SYMBOL,

        "market_symbols":
            MARKET_SYMBOLS,

        "prediction_horizon_days":
            PREDICTION_HORIZON_DAYS,

        "movement_threshold":
            MOVEMENT_THRESHOLD,

        "selected_buy_threshold":
            selected_buy_threshold,

        "selected_sell_threshold":
            selected_sell_threshold,

        "feature_count":
            len(feature_columns),

        "holdout_metrics":
            candidate_metrics,

        "holdout_buy_count":
            holdout_buy_count,

        "holdout_sell_count":
            holdout_sell_count,

        "holdout_wait_count":
            holdout_wait_count,

        "holdout_directional_coverage":
            holdout_directional_coverage,

        "mean_cv_balanced_accuracy":
            mean_cv_balanced_accuracy,

        "mean_cv_macro_f1":
            mean_cv_macro_f1,

        "mean_cv_sell_precision":
            mean_cv_sell_precision,

        "mean_cv_buy_precision":
            mean_cv_buy_precision,

        "mean_cv_directional_coverage":
            mean_cv_directional_coverage,
    }

    joblib.dump(
        metadata,
        CANDIDATE_METADATA_PATH,
    )

    print()
    print("=" * 76)

    print(
        "SELECTIVE V6 RESEARCH CANDIDATE "
        "RESULT: APPROVED"
    )

    print(
        "The candidate passed all V6 research gates."
    )

    print(
        "The candidate was saved as a research model only."
    )

    print()
    print("Candidate model:")
    print(CANDIDATE_MODEL_PATH)

    print()
    print("Candidate features:")
    print(CANDIDATE_FEATURES_PATH)

    print()
    print("Candidate metadata:")
    print(CANDIDATE_METADATA_PATH)

    print()
    print(
        "The production dashboard model was not overwritten."
    )

    print("=" * 76)

else:
    print()
    print("=" * 76)

    print(
        "SELECTIVE V6 RESEARCH CANDIDATE "
        "RESULT: REJECTED"
    )

    print(
        "The candidate did not pass all V6 research gates."
    )

    print(
        "No V6 research candidate model was saved."
    )

    print()
    print("Production protection:")

    print(
        "models/trading_model.pkl was not modified."
    )

    print(
        "models/features.pkl was not modified."
    )

    print("=" * 76)