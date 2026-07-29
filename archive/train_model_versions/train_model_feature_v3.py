from pathlib import Path

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
# MODEL EDGE DEVELOPMENT V3
# FEATURE INTELLIGENCE EXPERIMENT
# ============================================================

SYMBOL = "SPY"
START_DATE = "2016-01-01"
END_DATE = "2026-07-11"
INTERVAL = "1d"

MOVEMENT_THRESHOLD = 0.005
HOLDOUT_PERCENT = 0.20
N_SPLITS = 5

RANDOM_STATE = 42

RESEARCH_DIR = Path("models/research")
CANDIDATE_MODEL_PATH = RESEARCH_DIR / "trading_model_feature_v3.pkl"
CANDIDATE_FEATURES_PATH = RESEARCH_DIR / "features_feature_v3.pkl"

CLASS_NAMES = {
    -1: "SELL",
    0: "WAIT",
    1: "BUY",
}


# ============================================================
# FEATURE CONTRACT
# ============================================================

FEATURES = [
    "SMA20",
    "SMA50",
    "RSI",
    "MACD",
    "Returns",
    "Volatility",
    "SMA20_Distance",
    "SMA50_Distance",
    "SMA20_Slope",
    "SMA50_Slope",
    "ATR",
    "ATR_Percent",
    "ATR_Expansion",
    "Momentum_5",
    "Momentum_10",
    "Momentum_20",
    "Return_5",
    "Return_10",
    "Return_20",
    "Volatility_5",
    "Volatility_10",
    "Volatility_20",
    "Range_Percent",
    "Gap_Percent",
    "Volume_Change",
    "Volume_Ratio_20",
    "Drawdown_20",
    "Trend_Strength",
]


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def flatten_market_data_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert yfinance-style MultiIndex columns into standard columns.
    """

    df = df.copy()

    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    return df


def engineer_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Build the V3 market-state feature set.
    """

    df = flatten_market_data_columns(df)
    df = df.copy()

    required_columns = [
        "Open",
        "High",
        "Low",
        "Close",
        "Volume",
    ]

    missing_columns = [
        column
        for column in required_columns
        if column not in df.columns
    ]

    if missing_columns:
        raise RuntimeError(
            "Missing required market-data columns: "
            f"{missing_columns}"
        )

    open_price = pd.to_numeric(
        df["Open"].squeeze(),
        errors="coerce",
    )

    high = pd.to_numeric(
        df["High"].squeeze(),
        errors="coerce",
    )

    low = pd.to_numeric(
        df["Low"].squeeze(),
        errors="coerce",
    )

    close = pd.to_numeric(
        df["Close"].squeeze(),
        errors="coerce",
    )

    volume = pd.to_numeric(
        df["Volume"].squeeze(),
        errors="coerce",
    )

    # --------------------------------------------------------
    # ORIGINAL MODEL FEATURES
    # --------------------------------------------------------

    df["SMA20"] = ta.trend.sma_indicator(
        close,
        window=20,
    )

    df["SMA50"] = ta.trend.sma_indicator(
        close,
        window=50,
    )

    df["RSI"] = ta.momentum.rsi(
        close,
        window=14,
    )

    df["MACD"] = ta.trend.macd(close)

    df["Returns"] = close.pct_change()

    df["Volatility"] = (
        df["Returns"]
        .rolling(20)
        .std()
    )

    # --------------------------------------------------------
    # MOVING-AVERAGE MARKET POSITION
    # --------------------------------------------------------

    df["SMA20_Distance"] = (
        close / df["SMA20"]
    ) - 1

    df["SMA50_Distance"] = (
        close / df["SMA50"]
    ) - 1

    df["SMA20_Slope"] = (
        df["SMA20"]
        .pct_change(5)
    )

    df["SMA50_Slope"] = (
        df["SMA50"]
        .pct_change(10)
    )

    # --------------------------------------------------------
    # ATR / VOLATILITY EXPANSION
    # --------------------------------------------------------

    df["ATR"] = ta.volatility.average_true_range(
        high=high,
        low=low,
        close=close,
        window=14,
    )

    df["ATR_Percent"] = (
        df["ATR"] / close
    )

    atr_baseline = (
        df["ATR_Percent"]
        .rolling(20)
        .mean()
    )

    df["ATR_Expansion"] = (
        df["ATR_Percent"] / atr_baseline
    ) - 1

    # --------------------------------------------------------
    # MOMENTUM
    # --------------------------------------------------------

    df["Momentum_5"] = (
        close / close.shift(5)
    ) - 1

    df["Momentum_10"] = (
        close / close.shift(10)
    ) - 1

    df["Momentum_20"] = (
        close / close.shift(20)
    ) - 1

    # --------------------------------------------------------
    # MULTI-HORIZON RETURNS
    # --------------------------------------------------------

    df["Return_5"] = close.pct_change(5)

    df["Return_10"] = close.pct_change(10)

    df["Return_20"] = close.pct_change(20)

    # --------------------------------------------------------
    # MULTI-HORIZON VOLATILITY
    # --------------------------------------------------------

    df["Volatility_5"] = (
        df["Returns"]
        .rolling(5)
        .std()
    )

    df["Volatility_10"] = (
        df["Returns"]
        .rolling(10)
        .std()
    )

    df["Volatility_20"] = (
        df["Returns"]
        .rolling(20)
        .std()
    )

    # --------------------------------------------------------
    # PRICE RANGE AND OVERNIGHT GAP
    # --------------------------------------------------------

    df["Range_Percent"] = (
        (high - low) / close
    )

    previous_close = close.shift(1)

    df["Gap_Percent"] = (
        open_price / previous_close
    ) - 1

    # --------------------------------------------------------
    # VOLUME INTELLIGENCE
    # --------------------------------------------------------

    df["Volume_Change"] = (
        volume.pct_change()
    )

    volume_average_20 = (
        volume
        .rolling(20)
        .mean()
    )

    df["Volume_Ratio_20"] = (
        volume / volume_average_20
    )

    # --------------------------------------------------------
    # RECENT DRAWDOWN
    # --------------------------------------------------------

    rolling_high_20 = (
        close
        .rolling(20)
        .max()
    )

    df["Drawdown_20"] = (
        close / rolling_high_20
    ) - 1

    # --------------------------------------------------------
    # TREND STRENGTH
    # --------------------------------------------------------

    trend_distance = (
        df["SMA20"] - df["SMA50"]
    ) / close

    slope_confirmation = (
        df["SMA20_Slope"]
        + df["SMA50_Slope"]
    )

    df["Trend_Strength"] = (
        trend_distance
        + slope_confirmation
    )

    return df


def create_three_state_target(
    df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Create the three-state next-day target.

    -1 = SELL
     0 = WAIT
     1 = BUY
    """

    df = df.copy()

    close = pd.to_numeric(
        df["Close"].squeeze(),
        errors="coerce",
    )

    df["Future_Return"] = (
        close.shift(-1) / close
    ) - 1

    df["Target"] = 0

    df.loc[
        df["Future_Return"] >= MOVEMENT_THRESHOLD,
        "Target",
    ] = 1

    df.loc[
        df["Future_Return"] <= -MOVEMENT_THRESHOLD,
        "Target",
    ] = -1

    return df


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
            if total
            else 0.0
        )

        print(
            f"{CLASS_NAMES[class_value]} "
            f"({class_value}): "
            f"{count} observations "
            f"({percentage:.2f}%)"
        )


def calculate_metrics(
    y_true: pd.Series,
    predictions: np.ndarray,
) -> dict:
    """
    Calculate model-quality metrics.
    """

    return {
        "accuracy": accuracy_score(
            y_true,
            predictions,
        ),
        "balanced_accuracy": balanced_accuracy_score(
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


def build_model() -> RandomForestClassifier:
    """
    Build the V3 candidate model.

    Model family is intentionally kept fixed.
    """

    return RandomForestClassifier(
        n_estimators=300,
        max_depth=6,
        min_samples_leaf=10,
        class_weight="balanced",
        random_state=RANDOM_STATE,
        n_jobs=-1,
    )


# ============================================================
# MAIN EXPERIMENT
# ============================================================

print()
print("=" * 72)
print("MODEL EDGE DEVELOPMENT V3")
print("FEATURE INTELLIGENCE EXPERIMENT")
print("=" * 72)
print()


# ============================================================
# 1/10 RETRIEVE MARKET DATA
# ============================================================

print("[1/10] Retrieving historical market data...")

df = get_market_data(
    SYMBOL,
    start=START_DATE,
    end=END_DATE,
    interval=INTERVAL,
    auto_adjust=True,
)

if df.empty:
    raise RuntimeError(
        f"Training stopped because {SYMBOL} "
        "historical data could not be retrieved."
    )

df = flatten_market_data_columns(df)

print("Rows downloaded:", len(df))
print("First observation:", df.index.min())
print("Last observation:", df.index.max())
print()


# ============================================================
# 2/10 ENGINEER V3 FEATURES
# ============================================================

print("[2/10] Engineering V3 feature intelligence...")

df = engineer_features(df)

print("Candidate feature count:", len(FEATURES))

for number, feature in enumerate(
    FEATURES,
    start=1,
):
    print(
        f"  {number:02d}. {feature}"
    )

print()


# ============================================================
# 3/10 CREATE THREE-STATE TARGET
# ============================================================

print("[3/10] Creating three-state trading target...")

df = create_three_state_target(df)

required_columns = (
    FEATURES
    + [
        "Future_Return",
        "Target",
    ]
)

df = df.dropna(
    subset=required_columns
).copy()

print("Usable observations:", len(df))
print()


# ============================================================
# 4/10 ANALYSE TARGET DISTRIBUTION
# ============================================================

print("[4/10] Analysing target distribution...")

print_target_distribution(
    df["Target"]
)

print()


# ============================================================
# 5/10 CREATE CHRONOLOGICAL HOLDOUT
# ============================================================

print("[5/10] Creating chronological holdout test...")

X = df[FEATURES].copy()
y = df["Target"].astype(int).copy()

holdout_size = int(
    len(df) * HOLDOUT_PERCENT
)

split_index = (
    len(df) - holdout_size
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
# 6/10 TIME-SERIES CROSS-VALIDATION
# ============================================================

print("[6/10] Running time-series cross-validation...")

time_series_split = TimeSeriesSplit(
    n_splits=N_SPLITS
)

cv_balanced_accuracy = []
cv_macro_f1 = []
cv_sell_precision = []
cv_buy_precision = []

for fold_number, (
    train_index,
    validation_index,
) in enumerate(
    time_series_split.split(X_train),
    start=1,
):
    X_fold_train = X_train.iloc[
        train_index
    ]

    X_fold_validation = X_train.iloc[
        validation_index
    ]

    y_fold_train = y_train.iloc[
        train_index
    ]

    y_fold_validation = y_train.iloc[
        validation_index
    ]

    fold_model = build_model()

    fold_model.fit(
        X_fold_train,
        y_fold_train,
    )

    fold_predictions = fold_model.predict(
        X_fold_validation
    )

    fold_metrics = calculate_metrics(
        y_fold_validation,
        fold_predictions,
    )

    cv_balanced_accuracy.append(
        fold_metrics["balanced_accuracy"]
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

    print(
        f"Fold {fold_number}: "
        f"Balanced Accuracy="
        f"{fold_metrics['balanced_accuracy']:.2%} | "
        f"Macro F1="
        f"{fold_metrics['macro_f1']:.2%} | "
        f"SELL Precision="
        f"{fold_metrics['sell_precision']:.2%} | "
        f"BUY Precision="
        f"{fold_metrics['buy_precision']:.2%}"
    )

print()

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

print("Cross-validation averages:")

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

print()


# ============================================================
# 7/10 TRAIN V3 HOLDOUT CANDIDATE
# ============================================================

print("[7/10] Training V3 holdout candidate model...")

candidate_model = build_model()

candidate_model.fit(
    X_train,
    y_train,
)

holdout_predictions = candidate_model.predict(
    X_holdout
)

candidate_metrics = calculate_metrics(
    y_holdout,
    holdout_predictions,
)

print()


# ============================================================
# 8/10 HOLDOUT RESULTS
# ============================================================

print("=" * 72)
print("FEATURE INTELLIGENCE V3 HOLDOUT RESULTS")
print("=" * 72)

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
    "Baseline Balanced Acc:",
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
# FEATURE IMPORTANCE
# ============================================================

print("=" * 72)
print("TOP 15 FEATURE IMPORTANCES")
print("=" * 72)

feature_importance = pd.Series(
    candidate_model.feature_importances_,
    index=FEATURES,
).sort_values(
    ascending=False
)

for rank, (
    feature_name,
    importance,
) in enumerate(
    feature_importance.head(15).items(),
    start=1,
):
    print(
        f"{rank:02d}. "
        f"{feature_name:<22} "
        f"{importance:.4f}"
    )

print()


# ============================================================
# 9/10 RESEARCH CANDIDATE GATE
# ============================================================

print("[9/10] Running V3 research candidate gate...")

class_counts = y.value_counts()

adequate_class_support = all(
    class_counts.get(
        class_value,
        0,
    ) >= 100
    for class_value in [-1, 0, 1]
)

balanced_accuracy_gate = (
    candidate_metrics["balanced_accuracy"]
    >= 0.40
)

macro_f1_gate = (
    candidate_metrics["macro_f1"]
    >= 0.38
)

sell_precision_gate = (
    candidate_metrics["sell_precision"]
    >= 0.30
)

buy_precision_gate = (
    candidate_metrics["buy_precision"]
    >= 0.45
)

cv_balanced_accuracy_gate = (
    mean_cv_balanced_accuracy
    >= 0.38
)

cv_macro_f1_gate = (
    mean_cv_macro_f1
    >= 0.35
)

gates = {
    "Adequate class support":
        adequate_class_support,

    "Balanced accuracy acceptable":
        balanced_accuracy_gate,

    "Macro F1 acceptable":
        macro_f1_gate,

    "SELL precision acceptable":
        sell_precision_gate,

    "BUY precision acceptable":
        buy_precision_gate,

    "CV balanced accuracy acceptable":
        cv_balanced_accuracy_gate,

    "CV macro F1 acceptable":
        cv_macro_f1_gate,
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
# 10/10 FINALISE RESEARCH CANDIDATE
# ============================================================

print("[10/10] Finalising V3 research candidate...")

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
        FEATURES,
        CANDIDATE_FEATURES_PATH,
    )

    print()
    print("=" * 72)
    print(
        "FEATURE V3 RESEARCH CANDIDATE RESULT: APPROVED"
    )
    print(
        "The candidate passed all V3 research gates."
    )
    print(
        "The candidate was saved as a RESEARCH model only."
    )
    print()
    print("Candidate model:")
    print(CANDIDATE_MODEL_PATH)
    print()
    print("Candidate feature contract:")
    print(CANDIDATE_FEATURES_PATH)
    print()
    print(
        "Production model was NOT overwritten."
    )
    print("=" * 72)

else:
    print()
    print("=" * 72)
    print(
        "FEATURE V3 RESEARCH CANDIDATE RESULT: REJECTED"
    )
    print(
        "The candidate did not pass all V3 research gates."
    )
    print(
        "No Feature V3 candidate model was saved."
    )
    print()
    print("Production protection:")
    print(
        "models/trading_model.pkl was not modified."
    )
    print(
        "models/features.pkl was not modified."
    )
    print("=" * 72)