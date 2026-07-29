from __future__ import annotations

import json
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
# MODEL EDGE DEVELOPMENT V2
# THREE-STATE TARGET RESEARCH
# ============================================================

SYMBOL = "SPY"
TRAINING_PERIOD = "10y"
INTERVAL = "1d"

BUY_THRESHOLD = 0.005
SELL_THRESHOLD = -0.005

N_TIME_SERIES_SPLITS = 5

MIN_CLASS_OBSERVATIONS = 50
MIN_BALANCED_ACCURACY = 0.40
MIN_MACRO_F1 = 0.35
MIN_BUY_PRECISION = 0.50
MIN_SELL_PRECISION = 0.50

MODEL_DIRECTORY = Path("models")

CANDIDATE_MODEL_PATH = (
    MODEL_DIRECTORY / "three_state_candidate_model.pkl"
)

CANDIDATE_FEATURES_PATH = (
    MODEL_DIRECTORY / "three_state_candidate_features.pkl"
)

CANDIDATE_METADATA_PATH = (
    MODEL_DIRECTORY / "three_state_candidate_metadata.json"
)


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
]


CLASS_NAMES = {
    -1: "SELL",
    0: "WAIT",
    1: "BUY",
}


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def flatten_market_columns(
    market_df: pd.DataFrame,
) -> pd.DataFrame:
    """
    Convert yfinance-style MultiIndex columns into ordinary
    one-level columns.
    """
    result = market_df.copy()

    if isinstance(result.columns, pd.MultiIndex):
        result.columns = result.columns.get_level_values(0)

    return result


def create_three_state_target(
    future_return: pd.Series,
) -> pd.Series:
    """
    Convert next-day returns into SELL, WAIT and BUY classes.

    -1 = meaningful downside
     0 = market noise / no trade
     1 = meaningful upside
    """
    conditions = [
        future_return <= SELL_THRESHOLD,
        future_return >= BUY_THRESHOLD,
    ]

    choices = [
        -1,
        1,
    ]

    target = np.select(
        conditions,
        choices,
        default=0,
    )

    return pd.Series(
        target,
        index=future_return.index,
        dtype=int,
    )


def build_model() -> RandomForestClassifier:
    """
    Return a consistent research candidate model.
    """
    return RandomForestClassifier(
        n_estimators=400,
        max_depth=7,
        min_samples_leaf=12,
        min_samples_split=20,
        class_weight="balanced_subsample",
        random_state=42,
        n_jobs=-1,
    )


def get_class_metric(
    values: np.ndarray,
    labels: list[int],
    target_label: int,
) -> float:
    """
    Safely extract a metric for one target class.
    """
    label_to_position = {
        label: position
        for position, label in enumerate(labels)
    }

    position = label_to_position[target_label]

    return float(values[position])


# ============================================================
# START
# ============================================================

print("=" * 76)
print("AI TRADING MACHINE")
print("MODEL EDGE DEVELOPMENT V2 - THREE-STATE TARGET")
print("=" * 76)

print("\nTarget definition:")
print(
    f"SELL: future return <= {SELL_THRESHOLD:.2%}"
)
print(
    f"WAIT: {SELL_THRESHOLD:.2%} < future return "
    f"< {BUY_THRESHOLD:.2%}"
)
print(
    f"BUY:  future return >= {BUY_THRESHOLD:.2%}"
)


# ============================================================
# 1. RETRIEVE MARKET DATA
# ============================================================

print("\n[1/9] Retrieving historical market data...")

df = get_market_data(
    SYMBOL,
    period=TRAINING_PERIOD,
    interval=INTERVAL,
    auto_adjust=True,
)

if df is None or df.empty:
    raise RuntimeError(
        f"No historical market data was returned for {SYMBOL}."
    )

df = flatten_market_columns(df)
df = df.sort_index().copy()

required_price_columns = {
    "Open",
    "High",
    "Low",
    "Close",
    "Volume",
}

missing_price_columns = (
    required_price_columns - set(df.columns)
)

if missing_price_columns:
    raise RuntimeError(
        "Market data is missing required columns: "
        + ", ".join(sorted(missing_price_columns))
    )

print(f"Rows downloaded: {len(df)}")
print(f"First observation: {df.index.min()}")
print(f"Last observation:  {df.index.max()}")


# ============================================================
# 2. FEATURE ENGINEERING
# ============================================================

print("\n[2/9] Engineering model features...")

close = pd.to_numeric(
    df["Close"],
    errors="coerce",
)

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


# ============================================================
# 3. THREE-STATE TARGET
# ============================================================

print("\n[3/9] Creating three-state trading target...")

df["Future_Close"] = close.shift(-1)

df["Future_Return"] = (
    df["Future_Close"] / close
) - 1.0

df = df.dropna(
    subset=FEATURES + ["Future_Return"]
).copy()

df["Target"] = create_three_state_target(
    df["Future_Return"]
)

X = df[FEATURES].copy()
y = df["Target"].copy()

print(f"Usable observations: {len(df)}")


# ============================================================
# 4. CLASS DISTRIBUTION
# ============================================================

print("\n[4/9] Analysing target distribution...")

class_counts = (
    y.value_counts()
    .reindex([-1, 0, 1], fill_value=0)
)

class_percentages = (
    class_counts / len(y) * 100
)

for label in [-1, 0, 1]:
    print(
        f"{CLASS_NAMES[label]:>4} ({label:>2}): "
        f"{int(class_counts[label]):>5} observations "
        f"({class_percentages[label]:.2f}%)"
    )

class_support_check = bool(
    (class_counts >= MIN_CLASS_OBSERVATIONS).all()
)


# ============================================================
# 5. CHRONOLOGICAL HOLDOUT
# ============================================================

print("\n[5/9] Creating chronological holdout test...")

split_index = int(len(X) * 0.80)

X_train = X.iloc[:split_index].copy()
X_test = X.iloc[split_index:].copy()

y_train = y.iloc[:split_index].copy()
y_test = y.iloc[split_index:].copy()

if X_train.empty or X_test.empty:
    raise RuntimeError(
        "Insufficient observations for chronological validation."
    )

print(f"Training rows: {len(X_train)}")
print(f"Holdout rows:  {len(X_test)}")

print(
    f"Training period: "
    f"{X_train.index.min()} -> {X_train.index.max()}"
)

print(
    f"Holdout period:  "
    f"{X_test.index.min()} -> {X_test.index.max()}"
)


# ============================================================
# 6. MAJORITY-CLASS BASELINE
# ============================================================

majority_class = int(
    y_train.mode().iloc[0]
)

baseline_predictions = np.full(
    shape=len(y_test),
    fill_value=majority_class,
    dtype=int,
)

baseline_accuracy = accuracy_score(
    y_test,
    baseline_predictions,
)

baseline_balanced_accuracy = balanced_accuracy_score(
    y_test,
    baseline_predictions,
)

baseline_macro_f1 = f1_score(
    y_test,
    baseline_predictions,
    labels=[-1, 0, 1],
    average="macro",
    zero_division=0,
)

print("\nMajority-class baseline:")
print(
    f"Predicted class: "
    f"{CLASS_NAMES[majority_class]}"
)

print(
    f"Baseline accuracy: "
    f"{baseline_accuracy:.2%}"
)

print(
    f"Baseline balanced accuracy: "
    f"{baseline_balanced_accuracy:.2%}"
)

print(
    f"Baseline macro F1: "
    f"{baseline_macro_f1:.2%}"
)


# ============================================================
# 7. TIME-SERIES CROSS-VALIDATION
# ============================================================

print("\n[6/9] Running time-series cross-validation...")

time_series_split = TimeSeriesSplit(
    n_splits=N_TIME_SERIES_SPLITS
)

cv_balanced_accuracies = []
cv_macro_f1_scores = []
cv_buy_precisions = []
cv_sell_precisions = []

for fold_number, (
    fold_train_index,
    fold_validation_index,
) in enumerate(
    time_series_split.split(X_train),
    start=1,
):
    X_fold_train = X_train.iloc[
        fold_train_index
    ]

    X_fold_validation = X_train.iloc[
        fold_validation_index
    ]

    y_fold_train = y_train.iloc[
        fold_train_index
    ]

    y_fold_validation = y_train.iloc[
        fold_validation_index
    ]

    fold_model = build_model()

    fold_model.fit(
        X_fold_train,
        y_fold_train,
    )

    fold_predictions = fold_model.predict(
        X_fold_validation
    )

    fold_balanced_accuracy = (
        balanced_accuracy_score(
            y_fold_validation,
            fold_predictions,
        )
    )

    fold_macro_f1 = f1_score(
        y_fold_validation,
        fold_predictions,
        labels=[-1, 0, 1],
        average="macro",
        zero_division=0,
    )

    fold_precision_values = precision_score(
        y_fold_validation,
        fold_predictions,
        labels=[-1, 0, 1],
        average=None,
        zero_division=0,
    )

    fold_sell_precision = get_class_metric(
        fold_precision_values,
        [-1, 0, 1],
        -1,
    )

    fold_buy_precision = get_class_metric(
        fold_precision_values,
        [-1, 0, 1],
        1,
    )

    cv_balanced_accuracies.append(
        fold_balanced_accuracy
    )

    cv_macro_f1_scores.append(
        fold_macro_f1
    )

    cv_buy_precisions.append(
        fold_buy_precision
    )

    cv_sell_precisions.append(
        fold_sell_precision
    )

    print(
        f"Fold {fold_number}: "
        f"Balanced Accuracy="
        f"{fold_balanced_accuracy:.2%} | "
        f"Macro F1={fold_macro_f1:.2%} | "
        f"SELL Precision="
        f"{fold_sell_precision:.2%} | "
        f"BUY Precision="
        f"{fold_buy_precision:.2%}"
    )

mean_cv_balanced_accuracy = float(
    np.mean(cv_balanced_accuracies)
)

mean_cv_macro_f1 = float(
    np.mean(cv_macro_f1_scores)
)

mean_cv_sell_precision = float(
    np.mean(cv_sell_precisions)
)

mean_cv_buy_precision = float(
    np.mean(cv_buy_precisions)
)

print("\nCross-validation averages:")

print(
    f"Mean balanced accuracy: "
    f"{mean_cv_balanced_accuracy:.2%}"
)

print(
    f"Mean macro F1: "
    f"{mean_cv_macro_f1:.2%}"
)

print(
    f"Mean SELL precision: "
    f"{mean_cv_sell_precision:.2%}"
)

print(
    f"Mean BUY precision: "
    f"{mean_cv_buy_precision:.2%}"
)


# ============================================================
# 8. HOLDOUT CANDIDATE MODEL
# ============================================================

print("\n[7/9] Training holdout candidate model...")

candidate_model = build_model()

candidate_model.fit(
    X_train,
    y_train,
)

predictions = candidate_model.predict(
    X_test
)

accuracy = accuracy_score(
    y_test,
    predictions,
)

balanced_accuracy = balanced_accuracy_score(
    y_test,
    predictions,
)

macro_f1 = f1_score(
    y_test,
    predictions,
    labels=[-1, 0, 1],
    average="macro",
    zero_division=0,
)

precision_values = precision_score(
    y_test,
    predictions,
    labels=[-1, 0, 1],
    average=None,
    zero_division=0,
)

recall_values = recall_score(
    y_test,
    predictions,
    labels=[-1, 0, 1],
    average=None,
    zero_division=0,
)

sell_precision = get_class_metric(
    precision_values,
    [-1, 0, 1],
    -1,
)

wait_precision = get_class_metric(
    precision_values,
    [-1, 0, 1],
    0,
)

buy_precision = get_class_metric(
    precision_values,
    [-1, 0, 1],
    1,
)

sell_recall = get_class_metric(
    recall_values,
    [-1, 0, 1],
    -1,
)

wait_recall = get_class_metric(
    recall_values,
    [-1, 0, 1],
    0,
)

buy_recall = get_class_metric(
    recall_values,
    [-1, 0, 1],
    1,
)

print("\n" + "-" * 76)
print("THREE-STATE HOLDOUT RESULTS")
print("-" * 76)

print(f"Accuracy:              {accuracy:.2%}")
print(
    f"Balanced Accuracy:     "
    f"{balanced_accuracy:.2%}"
)
print(f"Macro F1:              {macro_f1:.2%}")

print(
    f"Baseline Accuracy:     "
    f"{baseline_accuracy:.2%}"
)

print(
    f"Baseline Balanced Acc: "
    f"{baseline_balanced_accuracy:.2%}"
)

print(
    f"SELL Precision:        "
    f"{sell_precision:.2%}"
)

print(
    f"SELL Recall:           "
    f"{sell_recall:.2%}"
)

print(
    f"WAIT Precision:        "
    f"{wait_precision:.2%}"
)

print(
    f"WAIT Recall:           "
    f"{wait_recall:.2%}"
)

print(
    f"BUY Precision:         "
    f"{buy_precision:.2%}"
)

print(
    f"BUY Recall:            "
    f"{buy_recall:.2%}"
)

print("\nConfusion Matrix:")
print(
    confusion_matrix(
        y_test,
        predictions,
        labels=[-1, 0, 1],
    )
)

print("\nClassification Report:")
print(
    classification_report(
        y_test,
        predictions,
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
# 9. RESEARCH CANDIDATE GATE
# ============================================================

print("\n[8/9] Running research candidate gate...")

promotion_checks = {
    "Adequate class support": (
        class_support_check
    ),
    "Balanced accuracy acceptable": (
        balanced_accuracy
        >= MIN_BALANCED_ACCURACY
    ),
    "Macro F1 acceptable": (
        macro_f1
        >= MIN_MACRO_F1
    ),
    "SELL precision acceptable": (
        sell_precision
        >= MIN_SELL_PRECISION
    ),
    "BUY precision acceptable": (
        buy_precision
        >= MIN_BUY_PRECISION
    ),
    "CV balanced accuracy acceptable": (
        mean_cv_balanced_accuracy
        >= MIN_BALANCED_ACCURACY
    ),
}

for check_name, passed in promotion_checks.items():
    result_text = (
        "PASS"
        if passed
        else "FAIL"
    )

    print(
        f"{result_text}: {check_name}"
    )

candidate_approved = all(
    promotion_checks.values()
)


# ============================================================
# SAVE RESEARCH ARTIFACTS ONLY
# ============================================================

print("\n[9/9] Finalising research candidate...")

MODEL_DIRECTORY.mkdir(
    parents=True,
    exist_ok=True,
)

metadata = {
    "symbol": SYMBOL,
    "training_period": TRAINING_PERIOD,
    "interval": INTERVAL,
    "buy_threshold": BUY_THRESHOLD,
    "sell_threshold": SELL_THRESHOLD,
    "features": FEATURES,
    "classes": CLASS_NAMES,
    "accuracy": accuracy,
    "balanced_accuracy": balanced_accuracy,
    "macro_f1": macro_f1,
    "sell_precision": sell_precision,
    "sell_recall": sell_recall,
    "wait_precision": wait_precision,
    "wait_recall": wait_recall,
    "buy_precision": buy_precision,
    "buy_recall": buy_recall,
    "mean_cv_balanced_accuracy": (
        mean_cv_balanced_accuracy
    ),
    "mean_cv_macro_f1": mean_cv_macro_f1,
    "mean_cv_sell_precision": (
        mean_cv_sell_precision
    ),
    "mean_cv_buy_precision": (
        mean_cv_buy_precision
    ),
    "approved": candidate_approved,
    "promotion_checks": promotion_checks,
}

print("\n" + "=" * 76)

if candidate_approved:
    joblib.dump(
        candidate_model,
        CANDIDATE_MODEL_PATH,
    )

    joblib.dump(
        FEATURES,
        CANDIDATE_FEATURES_PATH,
    )

    with CANDIDATE_METADATA_PATH.open(
        "w",
        encoding="utf-8",
    ) as metadata_file:
        json.dump(
            metadata,
            metadata_file,
            indent=4,
        )

    print("RESEARCH CANDIDATE RESULT: APPROVED")
    print(
        "Three-state candidate artifacts were saved."
    )
    print(
        f"Candidate model: "
        f"{CANDIDATE_MODEL_PATH}"
    )
    print(
        f"Candidate features: "
        f"{CANDIDATE_FEATURES_PATH}"
    )
    print(
        f"Candidate metadata: "
        f"{CANDIDATE_METADATA_PATH}"
    )

else:
    print("RESEARCH CANDIDATE RESULT: REJECTED")
    print(
        "The candidate did not pass all research gates."
    )
    print(
        "No three-state candidate model was promoted."
    )

print("\nProduction protection:")
print(
    "models/trading_model.pkl was not modified."
)
print(
    "models/features.pkl was not modified."
)

print("=" * 76)