import pandas as pd

def sort_trade_queue(df):
    """
    Robust sorting of approved trades (handles missing columns safely)
    """

    # ✅ Safe filter (avoid KeyError)
    if "Trade Approved" not in df.columns:
        return df.copy()

    approved = df[df["Trade Approved"] == True].copy()

    if approved.empty:
        return approved

    # ✅ Ensure ALL required columns exist
    required_cols = [
        "Priority",
        "AI Trade Score",
        "Strategy Score",
        "AI Confidence %"
    ]

    for col in required_cols:
        if col not in approved.columns:
            approved[col] = 0

    # ✅ Replace NaN / None with safe values
    approved = approved.fillna(0)

    # ✅ Convert to numeric (prevents crash)
    for col in required_cols:
        approved[col] = pd.to_numeric(approved[col], errors="coerce").fillna(0)

    # ✅ Sort safely
    approved = approved.sort_values(
        by=required_cols,
        ascending=False
    )

    return approved.reset_index(drop=True)

def filter_executable_trades(trade_queue, allowed_asset_classes=None):
    """
    Filters trades that are allowed for execution.
    """

    if allowed_asset_classes is None:
        allowed_asset_classes = ["US_STOCKS", "CRYPTO"]

    executable = trade_queue[
        trade_queue["Asset Class"].isin(allowed_asset_classes)
    ].copy()

    blocked = trade_queue[
        ~trade_queue["Asset Class"].isin(allowed_asset_classes)
    ].copy()

    return executable, blocked