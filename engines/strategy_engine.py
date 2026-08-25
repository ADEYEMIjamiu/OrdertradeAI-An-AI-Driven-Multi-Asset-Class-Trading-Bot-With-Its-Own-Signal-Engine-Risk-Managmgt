def identify_strategy(row):
    """
    Identifies which strategy is most likely responsible for the trade setup.
    """

    from streamlit import session_state as ss

    ticker = row.get("Symbol", "").upper()

    # 🔥 FORCE SAFE SIGNAL READ
    signal = str(row.get("Signal", "HOLD")).upper()

    # 🔍 DEBUG BEFORE LOGIC
    print(f"📊 ORIGINAL SIGNAL → {ticker}: {signal}")

    # 🚫 BLOCK duplicate BUY (KEY FIX)
    if signal == "BUY":
        if "paper_engine" in ss:
            if ticker in ss.paper_engine.positions:
                print(f"⛔ STRATEGY BLOCKED BUY → already holding {ticker}")
                signal = "HOLD"

    # 🔍 DEBUG AFTER LOGIC
    print(f"📊 FINAL SIGNAL AFTER BLOCK → {ticker}: {signal}")

    # 🔢 SAFE numeric extraction
    def safe_float(x):
        try:
            return float(x)
        except:
            return 0.0

    confidence = safe_float(row.get("AI Confidence %", 0))
    trend_score = safe_float(row.get("Trend Score", 0))
    daily_change = safe_float(row.get("Daily Change %", 0))

    # 🧠 STRATEGY LOGIC
    if signal == "BUY":
        if trend_score >= 2 and confidence >= 75:
            return "Momentum Trend"
        elif daily_change >= 2 and confidence >= 70:
            return "Breakout"
        elif daily_change < 0 and confidence >= 70:
            return "Dip Buy"
        else:
            return "AI Buy Setup"

    elif signal == "SELL":
        if trend_score <= -2:
            return "Bearish Momentum"
        elif daily_change <= -2:
            return "Breakdown"
        else:
            return "AI Sell Setup"

    return "No Strategy"

def score_strategy(row):
    """
    Scores the strategy quality before execution.
    """

    strategy = row.get("Strategy", "No Strategy")
    signal = str(row.get("Signal", "HOLD")).upper()
    confidence = float(row.get("AI Confidence %", 0))
    trend_score = float(row.get("Trend Score", 0))
    risk_reward = row.get("Risk Reward", 0)

    try:
        risk_reward = float(risk_reward)
    except Exception:
        risk_reward = 0

    score = 0

    if strategy == "Momentum Trend":
        score += 35
    elif strategy == "Breakout":
        score += 30
    elif strategy == "Dip Buy":
        score += 25
    elif strategy == "Bearish Momentum":
        score += 30
    elif strategy == "Breakdown":
        score += 25
    elif strategy in ["AI Buy Setup", "AI Sell Setup"]:
        score += 20

    score += min(confidence / 100 * 25, 25)

    # FIX 2026-08-25: this block used to award the +20 "strong trend
    # conviction" bonus purely off trend_score's magnitude, with no
    # check on which way the trade itself was going -- so a BUY signal
    # riding the single worst (most bearish) trend reading the system
    # produces got the exact same +20 bonus as a BUY riding the
    # strongest bullish trend. That's backwards: a trend fighting the
    # trade's direction should never be rewarded. Found via a real OIL
    # trade (2026-08-25) entered at trend_score=-2 -- the worst reading
    # ever logged for that ticker -- that lost within 7 hours; combined
    # with approval_engine.py's trend filter being set to reject BUYs
    # only below -3 (a threshold trend_score never actually reaches,
    # see that file's matching fix), nothing was catching this.
    # Now only rewards trend AGREEMENT: bullish trend for a BUY,
    # bearish trend for a SELL, never the mismatched direction. A
    # trend_score of exactly 0 (neutral) still gets the same modest
    # +8 either way, since neutral doesn't favor either direction.
    if signal == "BUY":
        if trend_score >= 2:
            score += 20
        elif trend_score >= 1:
            score += 15
        elif trend_score == 0:
            score += 8
    elif signal == "SELL":
        if trend_score <= -2:
            score += 20
        elif trend_score == 0:
            score += 8
    elif trend_score == 0:
        score += 8

    if risk_reward >= 3:
        score += 20
    elif risk_reward >= 2:
        score += 15
    elif risk_reward >= 1.5:
        score += 8

    return round(min(score, 100), 2)