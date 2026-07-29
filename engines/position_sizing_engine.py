from config import *


def confidence_multiplier(confidence):
    """
    AI confidence adjustment.
    Returns multiplier between 0.5 and 1.5
    """

    if confidence >= 90:
        return 1.50
    elif confidence >= 80:
        return 1.30
    elif confidence >= 70:
        return 1.10
    elif confidence >= 60:
        return 1.00
    else:
        return 0.50


def strategy_multiplier(strategy_score):
    """
    Strategy quality adjustment.
    """

    if strategy_score >= 90:
        return 1.40
    elif strategy_score >= 80:
        return 1.20
    elif strategy_score >= 70:
        return 1.00
    elif strategy_score >= 60:
        return 0.80
    else:
        return 0.50


def regime_multiplier(regime):
    """
    Market regime adjustment.
    """

    regime = str(regime).upper()

    if regime == "BULL":
        return 1.20

    elif regime == "SIDEWAYS":
        return 0.90

    elif regime == "BEAR":
        return 0.70

    return 1.00


def exposure_multiplier(exposure_percent):
    """
    Reduce size as portfolio exposure increases.
    """

    if exposure_percent < 20:
        return 1.30

    elif exposure_percent < 40:
        return 1.10

    elif exposure_percent < 60:
        return 1.00

    elif exposure_percent < 80:
        return 0.75

    return 0.50


def calculate_position_size(
    portfolio_value,
    confidence,
    strategy_score,
    regime,
    exposure_percent,
):
    """
    Final position sizing engine.

    Trade size scales with signal quality (confidence, strategy score,
    regime, current exposure), then is capped in dollar terms by
    MIN_TRADE_AMOUNT/MAX_TRADE_AMOUNT -- the same bounds crypto trades
    already use via risk_engine.calculate_trade_amount(). This used to
    scale with portfolio_value alone (clamped to 1%-20% of total
    portfolio per trade), which on a ~$166k portfolio meant single stock
    trades of $1,600-$33,000 -- enough to burn through most of a day's
    cash and exposure budget in 2-3 trades, and with no relation at all
    to MAX_TRADE_AMOUNT in config.py. Basing the starting size on the
    midpoint of the configured trade range (instead of portfolio_value)
    keeps stocks and crypto sized consistently and predictably regardless
    of portfolio size, while still letting stronger signals size larger
    within that range.
    """

    base_size = (MIN_TRADE_AMOUNT + MAX_TRADE_AMOUNT) / 2

    size = (
        base_size
        * confidence_multiplier(confidence)
        * strategy_multiplier(strategy_score)
        * regime_multiplier(regime)
        * exposure_multiplier(exposure_percent)
    )

    size = max(MIN_TRADE_AMOUNT, min(size, MAX_TRADE_AMOUNT))

    return round(size, 2)