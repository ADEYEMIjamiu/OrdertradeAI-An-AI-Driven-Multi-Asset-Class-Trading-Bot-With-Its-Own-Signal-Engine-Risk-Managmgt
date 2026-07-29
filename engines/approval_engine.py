from config import (
    MIN_TRADE_SCORE,
    MIN_RISK_REWARD_RATIO,
    MIN_TRADE_CONFIDENCE,
)

TEST_MODE = False

def approve_trade(row, open_positions_count=0):
    """
    Final approval gate before a trade is allowed.
    """

    ticker = row.get("Ticker") or row.get("symbol") or "UNKNOWN"
    signal = row.get("Signal", "WAIT")

    confidence = float(row.get("AI Confidence %", 0))
    trend_score = float(row.get("Trend Score", 0))
    trade_score = float(row.get("AI Trade Score", 0))
    risk_reward = row.get("Risk Reward", 0)
    trade_grade = row.get("Trade Grade", "N/A")
    trade_decision = row.get("Trade Decision", "WAIT")

    try:
        risk_reward = float(risk_reward)
    except:
        risk_reward = 0

    # =====================================================
    # 🚀 TEST MODE (FOR DEBUGGING ONLY)
    # =====================================================
    if TEST_MODE:
        if signal in ["BUY", "SELL"]:
            print(f"🧪 TEST MODE → {ticker} APPROVED")
            return True, "TEST MODE APPROVED"

    # =====================================================
    # 🛑 BASIC VALIDATION
    # =====================================================
    if signal not in ["BUY", "SELL"]:
        print(f"❌ {ticker} → No valid signal")
        return False, "No active BUY or SELL signal."

    # =====================================================
    # 🔥 SAFETY FILTERS (HIGH PRIORITY)
    # =====================================================

    # NOTE: this used to hardcode "if open_positions_count >= 2: reject",
    # a stricter and completely separate cap from MAX_POSITIONS/
    # MAX_OPEN_POSITIONS (5) in config.py -- and one that counted stock
    # positions only, so it (along with the equivalent bug in
    # risk_engine.py) silently blocked crypto trades too once only 2
    # stocks were held. The real position-count gates already live in
    # engines/risk_engine.py (can_open_position / risk_check_before_trade),
    # which are asset-class aware (stocks vs crypto counted separately).
    # Removed here to avoid a second, conflicting, stricter cap.

    # 2. Minimum confidence -- the strict, final-stage bar. See
    # MIN_TRADE_CONFIDENCE in config.py for why this is deliberately
    # higher than trade_planner.py's GRADE_C_CONFIDENCE (30): a signal
    # can clear that looser bar and still earn a full plan + a C grade,
    # but still get rejected here if it hasn't reached this stricter one.
    if confidence < MIN_TRADE_CONFIDENCE:
        print(f"❌ {ticker} → Low confidence: {confidence}")
        return False, f"Low confidence: {confidence}"

    # =====================================================
    # 📊 QUALITY FILTERS
    # =====================================================

    if trade_score < MIN_TRADE_SCORE:
        print(f"❌ {ticker} → Low trade score: {trade_score}")
        return False, f"Trade score too low: {trade_score}"

    if risk_reward < MIN_RISK_REWARD_RATIO:
        print(f"❌ {ticker} → Weak RR: {risk_reward}")
        return False, f"Risk/reward too weak: {risk_reward}"

    # Grade filter (balanced, not too strict)
    if trade_grade in ["D", "N/A", "ERROR"]:
        print(f"❌ {ticker} → Weak grade: {trade_grade}")
        return False, f"Weak trade grade: {trade_grade}"

    # =====================================================
    # 🧠 DECISION VALIDATION
    # =====================================================

    if trade_decision not in ["ENTER NOW", "WAIT / SMALL SIZE", "BUY", "SELL"]:
        print(f"❌ {ticker} → Bad decision: {trade_decision}")
        return False, f"Trade decision is {trade_decision}"

    # =====================================================
    # 📉 TREND FILTER (SMART, NOT TOO STRICT)
    # =====================================================

    if signal == "BUY" and trend_score < -3:
        print(f"❌ {ticker} → Strong bearish trend")
        return False, "Strong bearish trend"

    if signal == "SELL" and trend_score > 3:
        print(f"❌ {ticker} → Strong bullish trend")
        return False, "Strong bullish trend"

    # =====================================================
    # ✅ FINAL APPROVAL
    # =====================================================

    print(
        f"✅ APPROVED → {ticker} | "
        f"Score={trade_score} | RR={risk_reward} | Conf={confidence}"
    )

    return True, "Trade approved."