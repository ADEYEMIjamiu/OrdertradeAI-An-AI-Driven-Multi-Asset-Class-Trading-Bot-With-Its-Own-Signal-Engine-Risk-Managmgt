from engines.market_data_engine import get_market_data
import pandas as pd
import ta
import joblib
import os

from sklearn.ensemble import RandomForestClassifier
from sklearn.model_selection import train_test_split
from sklearn.metrics import accuracy_score

print("Downloading market data...")

# Download SPY historical data
df = get_market_data(
    "SPY",
    start="2018-01-01",
    end="2026-01-01",
    interval="1d",
    auto_adjust=True,
)

if df.empty:
    raise RuntimeError(
        "Training stopped because SPY historical data "
        "could not be retrieved."
    )

# Fix yfinance MultiIndex columns if they appear
if isinstance(df.columns, pd.MultiIndex):
    df.columns = df.columns.get_level_values(0)

df = df.dropna()

print("Rows downloaded:", len(df))

# Make sure Close is a 1-dimensional Series
close = df["Close"].squeeze()

# Technical indicators
df["SMA20"] = ta.trend.sma_indicator(close, window=20)
df["SMA50"] = ta.trend.sma_indicator(close, window=50)
df["RSI"] = ta.momentum.rsi(close, window=14)
df["MACD"] = ta.trend.macd(close)

# Additional features
df["Returns"] = close.pct_change()
df["Volatility"] = df["Returns"].rolling(20).std()

# Target:
# 1 = Tomorrow's price is higher
# 0 = Tomorrow's price is lower
df["Target"] = (close.shift(-1) > close).astype(int)

# Remove empty rows
df = df.dropna()

# Features used by the model
features = [
    "SMA20",
    "SMA50",
    "RSI",
    "MACD",
    "Returns",
    "Volatility"
]

X = df[features]
y = df["Target"]

# Train/Test split
X_train, X_test, y_train, y_test = train_test_split(
    X,
    y,
    test_size=0.2,
    shuffle=False
)

print("Training AI model...")

# Build model
model = RandomForestClassifier(
    n_estimators=200,
    max_depth=6,
    random_state=42
)

# Train
model.fit(X_train, y_train)

# Evaluate
predictions = model.predict(X_test)
accuracy = accuracy_score(y_test, predictions)

print(f"Model Accuracy: {accuracy:.2%}")

# Create models folder if it does not exist
os.makedirs("models", exist_ok=True)

# Save model and features
joblib.dump(model, "models/trading_model.pkl")
joblib.dump(features, "models/features.pkl")

print("Model saved successfully.")
print("Features saved successfully.")