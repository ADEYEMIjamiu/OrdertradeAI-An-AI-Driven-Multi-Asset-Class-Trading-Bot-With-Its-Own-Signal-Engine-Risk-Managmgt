"""
check_oil_weekend_price.py -- one-off, 2026-08-24.

Confirms whether CL=F (WTI crude, the underlying for the eToro OIL CFD
position) actually rallied toward its ~88.39 take-profit level over
the weekend (which is what a ~$70 unrealized profit on this position
size implies), or whether that reading was something else. Needed to
tell apart two explanations for why the position's trailing stop
(confirmed isTslEnabled=True, see check_etoro_trailing_stop.py) still
shows stopLossRate=81.67, unmoved since it opened 2026-08-18: either
(a) price genuinely peaked near 88 and eToro's trailing stop failed to
ratchet up despite being "enabled", or (b) price never actually got
that high and the trailing stop correctly has nothing to ratchet to.
"""

from engines.market_data_engine import get_market_data

df = get_market_data("CL=F", period="10d", interval="1h")
if df is None or df.empty:
    print("No data returned for CL=F.")
else:
    print(df[["High", "Low", "Close"]].to_string())
    print(f"\n10-day High: {df['High'].max():.2f}")
    print(f"10-day Low:  {df['Low'].min():.2f}")
    print(f"Most recent Close: {df['Close'].iloc[-1]:.2f}")
