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

    if trend_score >= 2:
        score += 20
    elif trend_score >= 1:
        score += 15
    elif trend_score == 0:
        score += 8
    elif trend_score <= -2:
        score += 20

    if risk_reward >= 3:
        score += 20
    elif risk_reward >= 2:
        score += 15
    elif risk_reward >= 1.5:
        score += 8

    return round(min(score, 100), 2)