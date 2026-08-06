"""
One-off script: fully closes the oversized SOL-USD position on Binance
testnet and records it through the exact same pipeline
apply_crypto_risk_management() uses in app.py (Order Book, Trade Log,
Telegram), so it shows up consistently everywhere instead of looking
like a mystery manual trade.

WHY THIS EXISTS (2026-07-31):
Before ALLOW_CRYPTO_PYRAMIDING was turned off on 2026-07-30, the bot
repeat-bought SOL-USD on nearly every cycle with nothing ever forcing a
sell, accumulating a single position of ~118.71 SOL (~80+ separate BUY
fills, all clustered around $74-75). That's now frozen: pyramiding-off
blocks any new BUY on an already-held coin, and apply_crypto_risk_management()
only sells once price moves +/-3%/5% from the last BUY's fill price --
which hadn't happened. Rather than wait indefinitely for that swing (on
a position 100x the size any single BUY signal would normally open),
this script sells the whole thing now, freeing the USDT so the next SOL
BUY signal opens a normal, single-entry position under the current rules.

Run this ONCE on the server (it needs the real Binance testnet
connection), then delete or ignore it -- it is not part of the running
app and is never imported by app.py.

    cd /root/AI-Trading-Bot
    source venv/bin/activate   # match however the service actually runs python
    python3 flatten_sol_position.py
"""

import binance_broker
from engines.order_manager import create_order, mark_order_filled, save_order
from trade_journal import log_trade
import telegram_notifier

TICKER = "SOL-USD"


def main():
    positions = binance_broker.get_positions()
    position = next(
        (p for p in positions if str(p["symbol"]).upper().strip() == TICKER),
        None,
    )

    if position is None:
        print(f"No open {TICKER} position found on Binance testnet. Nothing to do.")
        return

    qty = float(position["qty"])
    if qty <= 0:
        print(f"{TICKER} quantity is {qty} -- nothing to sell.")
        return

    print(f"Found {TICKER} position: {qty} units. Selling now...")

    # Use the same entry-price lookup logic as apply_crypto_risk_management()
    # (most recent FILLED BUY for this ticker/broker) purely for an
    # informational realized-P&L number -- doesn't affect the sell itself.
    import engines.order_manager as order_manager

    recent_orders = order_manager.load_orders(limit=200)
    entry_order = next(
        (
            o for o in recent_orders
            if str(o.get("ticker", "")).upper().strip() == TICKER
            and str(o.get("broker", "")).lower() == "binance"
            and str(o.get("side", "")).upper() == "BUY"
            and str(o.get("status", "")).upper() == "FILLED"
            and o.get("filled_price")
        ),
        None,
    )
    entry_price = float(entry_order["filled_price"]) if entry_order else None

    order = binance_broker.sell_crypto(TICKER, qty)
    print(f"Binance sell order submitted: {order}")

    current_price = binance_broker.get_current_price(TICKER)

    oms_order = create_order(
        ticker=TICKER,
        side="SELL",
        quantity=qty,
        trade_amount=qty * current_price,
        price=current_price,
        asset_class="CRYPTO",
        broker="binance",
        strategy="Manual Cleanup",
        confidence=0,
        ai_trade_score=0,
        priority="N/A",
    )
    oms_order = mark_order_filled(oms_order, filled_price=current_price, filled_quantity=qty)
    save_order(oms_order)

    realized_pnl = qty * (current_price - entry_price) if entry_price else None

    log_trade(
        ticker=TICKER,
        action="SELL",
        price=current_price,
        shares=qty,
        amount=qty * current_price,
        confidence=0,
        trend_score=0,
        reason="MANUAL CLEANUP (pre-fix pyramided position)",
        mode="BINANCE_TESTNET",
    )

    telegram_notifier.notify_trade_fill(
        ticker=TICKER,
        action="SELL",
        price=current_price,
        shares=qty,
        amount=qty * current_price,
        asset_class="CRYPTO",
        mode="BINANCE_TESTNET",
        realized_pnl=realized_pnl,
    )

    print(
        f"Done. Sold {round(qty, 6)} {TICKER} at ${round(current_price, 2)}"
        + (f" (entry ${round(entry_price, 2)}, realized P&L ${round(realized_pnl, 2)})" if entry_price else "")
    )


if __name__ == "__main__":
    main()
