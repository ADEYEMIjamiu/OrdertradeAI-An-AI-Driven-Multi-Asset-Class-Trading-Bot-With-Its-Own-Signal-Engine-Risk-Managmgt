def calculate_trade_score(row):
    """
    Calculates a professional AI trade quality score from 0 to 100.
    """

    score = 0

    confidence = float(row.get("AI Confidence %", 0))
    trend_score = float(row.get("Trend Score", 0))
    risk_reward = row.get("Risk Reward", 0)
    signal = row.get("Signal", "HOLD")
    trade_grade = row.get("Trade Grade", "N/A")

    try:
        risk_reward = float(risk_reward)
    except:
        risk_reward = 0

    # 1. AI Confidence: max 25 points
    score += min(confidence / 100 * 25, 25)

    # 2. Trend Score: max 20 points
    # FIX 2026-08-25: this block used to score purely off trend_score's
    # magnitude with no check on `signal` (read into a local variable
    # above but never actually used here) -- so a SELL riding a
    # strongly BULLISH trend (fighting the trade) got the same +20 as
    # a SELL riding a bearish one, and a SELL aligned with a bearish
    # trend got only 0-4 points. Exact same bug class as the one fixed
    # today in strategy_engine.py's score_strategy() (see that file's
    # matching comment), just in this sibling scoring function -- found
    # by the same OIL-trade investigation, then a follow-up audit swept
    # for more of this shape and found this one still live. Now the
    # ladder only rewards trend AGREEMENT: bullish tiers for a BUY,
    # the mirrored bearish tiers for a SELL.
    if signal == "BUY":
        if trend_score >= 3:
            score += 20
        elif trend_score == 2:
            score += 16
        elif trend_score == 1:
            score += 12
        elif trend_score == 0:
            score += 7
        elif trend_score == -1:
            score += 4
        else:
            score += 0
    elif signal == "SELL":
        if trend_score <= -3:
            score += 20
        elif trend_score == -2:
            score += 16
        elif trend_score == -1:
            score += 12
        elif trend_score == 0:
            score += 7
        elif trend_score == 1:
            score += 4
        else:
            score += 0
    elif trend_score == 0:
        score += 7

    # 3. Risk Reward: max 20 points
    if risk_reward >= 3:
        score += 20
    elif risk_reward >= 2.5:
        score += 17
    elif risk_reward >= 2:
        score += 14
    elif risk_reward >= 1.5:
        score += 8
    else:
        score += 0

    # 4. Trade Grade: max 20 points
    if trade_grade == "A+":
        score += 20
    elif trade_grade == "A":
        score += 16
    elif trade_grade == "B":
        score += 10
    elif trade_grade == "C":
        score += 4

    # 5. Active signal bonus: max 15 points
    if signal in ["BUY", "SELL"]:
        score += 15

    return round(min(score, 100), 2)