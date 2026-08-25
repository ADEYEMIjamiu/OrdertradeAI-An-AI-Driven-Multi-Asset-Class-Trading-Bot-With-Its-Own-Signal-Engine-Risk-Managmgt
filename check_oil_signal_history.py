"""
check_oil_signal_history.py -- one-off, 2026-08-25.

Pulls every logged trade_journal.db "trades" row for CL=F (OIL), in
chronological order, with the AI confidence/trend score and reason that
were recorded at the time -- to evaluate whether the 8/25 14:36 re-entry
@ 82.13 (closed same day at 80.53 for -$19.48) was backed by a
reasonable signal, or whether the AI kept buying into a market that was
already trending down. Same investigative pattern as
check_cluster_trade_origin.py used earlier for the LPT/ADA/DOT/FET
crypto cluster.

Note: eToro's automatic exits (stop-loss hits) don't call log_trade()
(see apply_etoro_trailing_lock()'s docstring -- the closing SELL
happens broker-side, not something this bot places itself), so only
BUY rows here have real reason/confidence data. The closes themselves
are visible on eToro's own trade history, not here.
"""
from trade_journal import load_trade_journal

rows = load_trade_journal()
columns = ["time", "ticker", "action", "price", "shares", "amount",
           "confidence", "trend_score", "reason", "mode"]
trades = [dict(zip(columns, row)) for row in rows]
trades.reverse()  # oldest first

oil_trades = [t for t in trades if str(t["ticker"]).upper().strip() == "CL=F"]

if not oil_trades:
    print("No CL=F rows found in trade_journal.db's trades table.")
else:
    print(f"{'Time':<20} {'Action':<6} {'Price':>8} {'Conf%':>7} {'Trend':>7}  Reason")
    for t in oil_trades:
        print(
            f"{t['time']:<20} {t['action']:<6} {t['price']:>8.2f} "
            f"{t['confidence']:>7.1f} {t['trend_score']:>7.1f}  {t['reason']}"
        )
