"""
check_crypto_balance_growth.py -- one-off, 2026-08-23.

Answers the user's question: "is the rising Binance USDT balance real
trading profit, and what's actually happening BUY/SELL/HOLD-wise?"

Uses the same FIFO-matched trade_journal.db data that powers the
dashboard's Performance tab (engines/performance_engine.py) -- not a
new/separate calculation -- so this can't disagree with what the
dashboard already shows. Scoped to crypto (-USD tickers) only, and
excludes pre-pyramiding-fix lots via CRYPTO_VALIDATION_START, same as
the dashboard's own crypto performance digest does.

Important nuance printed at the bottom: USDT wallet balance only moves
on a SELL fill (a BUY converts USDT into the coin; profit/loss isn't
"real" in USDT terms until that coin is sold back). So this script
also reports currently-open (HOLD) crypto lots separately, since their
unrealized gain/loss does NOT show up in the USDT balance yet.
"""

from datetime import datetime
from engines.performance_engine import (
    get_closed_trades_and_open_lots,
    _load_trades_chronological,
    _filter_pre_pyramiding_fix_crypto,
)
from config import CRYPTO_VALIDATION_START

closed_trades, open_lots = get_closed_trades_and_open_lots()

# --- Closed (realized) crypto round-trips, validation-window only ---
crypto_closed = [t for t in closed_trades if str(t["ticker"]).upper().endswith("-USD")]
crypto_closed = _filter_pre_pyramiding_fix_crypto(crypto_closed)

wins = [t for t in crypto_closed if t["pnl"] > 0]
losses = [t for t in crypto_closed if t["pnl"] <= 0]
total_realized_pnl = sum(t["pnl"] for t in crypto_closed)

# --- Raw BUY/SELL fill counts, crypto, since validation start ---
all_trades = _load_trades_chronological()

def _parse_time(value):
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return None

crypto_fills = [
    t for t in all_trades
    if str(t["ticker"]).upper().endswith("-USD")
    and (lambda ts: ts is not None and ts >= CRYPTO_VALIDATION_START)(_parse_time(t["time"]))
]
buy_fills = [t for t in crypto_fills if str(t["action"]).upper() == "BUY"]
sell_fills = [t for t in crypto_fills if str(t["action"]).upper() == "SELL"]

# --- Currently open (HOLD) crypto lots -- unrealized, not yet in USDT ---
crypto_open_count = sum(
    1 for ticker in open_lots if str(ticker).upper().endswith("-USD") and open_lots[ticker]
)

print("=" * 60)
print("CRYPTO ACTIVITY SINCE VALIDATION START "
      f"({CRYPTO_VALIDATION_START})")
print("=" * 60)
print(f"BUY fills (journaled):   {len(buy_fills)}")
print(f"SELL fills (journaled):  {len(sell_fills)}")
print(f"Currently open (HOLD):   {crypto_open_count} tickers\n")

print("=" * 60)
print("REALIZED P&L (closed round-trips only -- this is what actually")
print("moved the USDT wallet balance)")
print("=" * 60)
print(f"Closed round-trips: {len(crypto_closed)}")
print(f"Wins / Losses:      {len(wins)} / {len(losses)}")
if crypto_closed:
    win_rate = len(wins) / len(crypto_closed) * 100
    print(f"Win rate:           {win_rate:.1f}%")
print(f"Total realized P&L: ${total_realized_pnl:,.2f}")

print("\n" + "=" * 60)
print("NOTE ON THE RISING USDT BALANCE")
print("=" * 60)
print(
    "USDT balance only changes when a SELL actually fills -- a BUY just\n"
    "converts USDT into the coin, it doesn't reduce or increase USDT P&L\n"
    "until that coin is sold back. So the rising balance reflects the\n"
    "$%.2f realized above PLUS the earlier binance_broker.get_positions()\n"
    "visibility fix (19 previously-hidden tickers are now counted), NOT\n"
    "purely new trading profit. The %d still-open positions above are\n"
    "unrealized -- their gain/loss isn't in this balance yet either way."
    % (total_realized_pnl, crypto_open_count)
)
