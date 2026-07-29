def calculate_priority(row):
    """
    Calculates execution priority for each approved trade.
    """

    ai_score = float(row.get("AI Trade Score", 0))
    strategy_score = float(row.get("Strategy Score", 0))
    approved = row.get("Trade Approved", False)

    if not approved:
        return "Not Approved"

    final_score = (ai_score + strategy_score) / 2

    if final_score >= 90:
        return "★★★★★"

    elif final_score >= 80:
        return "★★★★"

    elif final_score >= 70:
        return "★★★"

    elif final_score >= 60:
        return "★★"

    else:
        return "★"