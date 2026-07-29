from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import ta

from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    classification_report,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
)
from sklearn.model_selection import TimeSeriesSplit

from engines.market_data_engine import get_market_data


# ============================================================
# MODEL TRAINING CONFIGURATION
# ============================================================

SYMBOL = "SPY"
TRAINING_START = "2018-01-01"
TRAINING_PERIOD = "10y"
INTERVAL = "1d"

MODEL_PATH = Path("models/trading_model.pkl")
FEATURES_PATH = Path("models/features.pkl")

N_TIME_SERIES_SPLITS = 5

MIN_ACCURACY_ADVANTAGE = 0.00
MIN_BUY_PRECISION = 0.50
MIN_CV_ACCURACY = 0.50


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


# ============================================================
# LOAD MARKET DATA
# ============================================================

print("=" * 70)
print("AI TRADING MACHINE - MODEL VALIDATION AND PROMOTION ENGINE")
print("=" * 70)

print("\n[1/8] Retrieving market data...")

df = get_market_data(
    SYMBOL,
    period=TRAINING_PERIOD,
    interval=INTERVAL,
    auto_adjust=True,
)

if df.empty:
    raise RuntimeError(
        f"Training stopped because {SYMBOL} historical data "
        "could not be retrieved."
    )

if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

df = df.copy()
df = df.sort_index()

print(f"Rows downloaded: {len(df)}")
print(f"Training symbol: {SYMBOL}")
print(f"Data start: {df.index.min()}")
print(f"Data end: {df.index.max()}")


# ============================================================
# FEATURE ENGINEERING
# ============================================================

print("\n[2/8] Engineering model features...")

close = df["Close"].squeeze()

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
# TARGET CREATION
# ============================================================

print("\n[3/8] Creating prediction target...")

df["Future_Close"] = close.shift(-1)

df = df.dropna(
    subset=FEATURES + ["Future_Close"]
).copy()

df["Target"] = (
    df["Future_Close"] > df["Close"]
).astype(int)

X = df[FEATURES].copy()
y = df["Target"].copy()

print(f"Usable model rows: {len(df)}")


# ============================================================
# CLASS BALANCE ANALYSIS
# ============================================================

print("\n[4/8] Analysing target class balance...")

class_counts = y.value_counts().sort_index()

down_count = int(class_counts.get(0, 0))
up_count = int(class_counts.get(1, 0))
total_count = len(y)

down_percent = (
    down_count / total_count * 100
    if total_count
    else 0.0
)

up_percent = (
    up_count / total_count * 100
    if total_count
    else 0.0
)

print(f"DOWN observations (0): {down_count}")
print(f"UP observations   (1): {up_count}")
print(f"DOWN percentage: {down_percent:.2f}%")
print(f"UP percentage:   {up_percent:.2f}%")


# ============================================================
# CHRONOLOGICAL HOLDOUT SPLIT
# ============================================================

print("\n[5/8] Creating chronological holdout test...")

split_index = int(len(X) * 0.80)

X_train = X.iloc[:split_index]
X_test = X.iloc[split_index:]

y_train = y.iloc[:split_index]
y_test = y.iloc[split_index:]

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
# NAIVE BASELINE
# ============================================================

majority_class = int(y_train.mode().iloc[0])

baseline_predictions = np.full(
    len(y_test),
    majority_class,
    dtype=int,
)

baseline_accuracy = accuracy_score(
    y_test,
    baseline_predictions,
)

print(
    f"\nNaive majority baseline accuracy: "
    f"{baseline_accuracy:.2%}"
)

print(
    f"Naive baseline predicts class: "
    f"{majority_class}"
)


# ============================================================
# TIME SERIES CROSS VALIDATION
# ============================================================

print("\n[6/8] Running time-series cross validation...")

time_series_split = TimeSeriesSplit(
    n_splits=N_TIME_SERIES_SPLITS
)

cv_accuracies = []
cv_buy_precisions = []

for fold_number, (train_index, validation_index) in enumerate(
    time_series_split.split(X_train),
    start=1,
):
    X_fold_train = X_train.iloc[train_index]
    X_fold_validation = X_train.iloc[validation_index]

    y_fold_train = y_train.iloc[train_index]
    y_fold_validation = y_train.iloc[validation_index]

    fold_model = RandomForestClassifier(
        n_estimators=300,
        max_depth=6,
        min_samples_leaf=10,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )

    fold_model.fit(
        X_fold_train,
        y_fold_train,
    )

    fold_predictions = fold_model.predict(
        X_fold_validation
    )

    fold_accuracy = accuracy_score(
        y_fold_validation,
        fold_predictions,
    )

    fold_buy_precision = precision_score(
        y_fold_validation,
        fold_predictions,
        pos_label=1,
        zero_division=0,
    )

    cv_accuracies.append(fold_accuracy)
    cv_buy_precisions.append(fold_buy_precision)

    print(
        f"Fold {fold_number}: "
        f"Accuracy={fold_accuracy:.2%} | "
        f"BUY Precision={fold_buy_precision:.2%}"
    )

mean_cv_accuracy = float(
    np.mean(cv_accuracies)
)

mean_cv_buy_precision = float(
    np.mean(cv_buy_precisions)
)

print(
    f"\nMean CV Accuracy: "
    f"{mean_cv_accuracy:.2%}"
)

print(
    f"Mean CV BUY Precision: "
    f"{mean_cv_buy_precision:.2%}"
)


# ============================================================
# TRAIN CANDIDATE MODEL
# ============================================================

print("\n[7/8] Training candidate AI model...")

candidate_model = RandomForestClassifier(
    n_estimators=300,
    max_depth=6,
    min_samples_leaf=10,
    class_weight="balanced",
    random_state=42,
    n_jobs=-1,
)

candidate_model.fit(
    X_train,
    y_train,
)

predictions = candidate_model.predict(X_test)

accuracy = accuracy_score(
    y_test,
    predictions,
)

buy_precision = precision_score(
    y_test,
    predictions,
    pos_label=1,
    zero_division=0,
)

buy_recall = recall_score(
    y_test,
    predictions,
    pos_label=1,
    zero_division=0,
)

buy_f1 = f1_score(
    y_test,
    predictions,
    pos_label=1,
    zero_division=0,
)

accuracy_advantage = (
    accuracy - baseline_accuracy
)

print("\n" + "-" * 70)
print("HOLDOUT MODEL RESULTS")
print("-" * 70)

print(f"Candidate Accuracy:      {accuracy:.2%}")
print(f"Baseline Accuracy:       {baseline_accuracy:.2%}")
print(f"Accuracy Advantage:      {accuracy_advantage:+.2%}")
print(f"BUY Precision:           {buy_precision:.2%}")
print(f"BUY Recall:              {buy_recall:.2%}")
print(f"BUY F1 Score:            {buy_f1:.2%}")

print("\nConfusion Matrix:")
print(
    confusion_matrix(
        y_test,
        predictions,
        labels=[0, 1],
    )
)

print("\nClassification Report:")

print(
    classification_report(
        y_test,
        predictions,
        labels=[0, 1],
        target_names=[
            "DOWN",
            "UP",
        ],
        zero_division=0,
    )
)


# ============================================================
# MODEL PROMOTION GATE
# ============================================================

print("\n[8/8] Running model promotion gate...")

promotion_checks = {
    "Beats naive baseline": (
        accuracy_advantage
        > MIN_ACCURACY_ADVANTAGE
    ),
    "BUY precision acceptable": (
        buy_precision
        >= MIN_BUY_PRECISION
    ),
    "CV accuracy acceptable": (
        mean_cv_accuracy
        >= MIN_CV_ACCURACY
    ),
}

for check_name, passed in promotion_checks.items():
    status = "PASS" if passed else "FAIL"

    print(
        f"{status}: {check_name}"
    )

model_promoted = all(
    promotion_checks.values()
)

MODEL_PATH.parent.mkdir(
    parents=True,
    exist_ok=True,
)

print("\n" + "=" * 70)

if model_promoted:
    joblib.dump(
        candidate_model,
        MODEL_PATH,
    )

    joblib.dump(
        FEATURES,
        FEATURES_PATH,
    )

    print("MODEL PROMOTION RESULT: APPROVED")
    print(
        "Candidate model promoted to production artifacts."
    )
    print(f"Model saved: {MODEL_PATH}")
    print(f"Features saved: {FEATURES_PATH}")

else:
    print("MODEL PROMOTION RESULT: REJECTED")
    print(
        "Candidate model did not pass all validation gates."
    )

    if MODEL_PATH.exists():
        print(
            "Existing trading model was preserved."
        )
    else:
        print(
            "WARNING: No existing production model is available."
        )

    if FEATURES_PATH.exists():
        print(
            "Existing feature contract was preserved."
        )

print("=" * 70)