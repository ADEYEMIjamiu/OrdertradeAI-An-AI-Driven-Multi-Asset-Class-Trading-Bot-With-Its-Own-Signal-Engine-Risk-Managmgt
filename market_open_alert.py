"""
Market-Open Telegram Alert -- pings you on Telegram the moment the US
stock market flips from closed to open, so you don't have to keep
refreshing the OrderTrade AI dashboard to find out.

Meant to be run on a schedule (cron), not continuously -- see the
crontab line in the deployment notes below. Each run is a single cheap
check: ask Alpaca's real market clock (via broker.get_market_clock(),
the same clock the rest of this project already trusts for
market_open/next_market_close) whether the market is open right now,
compare that to what it was on the last run, and only send a Telegram
message on the closed -> open transition. Without that transition
check, a 5-minute cron would spam a message every 5 minutes for the
entire trading day instead of alerting once.

State (was the market open on the last run) is kept in a small JSON
file next to this script, not in memory -- each cron invocation is a
fresh process with no memory of the last one, same reasoning as
emergency_stop.py's file-based flag in this project.

Uses Alpaca's clock specifically (not eToro's or Binance's) because
Alpaca's stocks are what the rest of this project's "market open"
concept has always meant -- eToro's stock CFDs actually trade on a
wider 24/5 schedule (confirmed live 2026-08-02/03: an eToro AAPL order
placed Saturday filled around 1am UK time Monday, well before NYSE's
9:30am ET open), so alerting on Alpaca's clock is the meaningful "is it
worth checking the dashboard" signal for this project's stock trading.
"""

import json
import os
from zoneinfo import ZoneInfo

import broker
import telegram_notifier

_STATE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "market_open_alert_state.json"
)

# User is in London -- show both zones in the alert so "Closes: ..." is
# never ambiguous, especially across the few weeks a year where US and
# UK daylight-saving changes don't land on the same date and the gap
# between them shifts from the usual 5 hours to 4.
_US_EASTERN = ZoneInfo("America/New_York")
_UK_LONDON = ZoneInfo("Europe/London")


def _format_close_time(next_close):
    if next_close is None:
        return None
    try:
        et = next_close.astimezone(_US_EASTERN)
        uk = next_close.astimezone(_UK_LONDON)
        return f"{et.strftime('%H:%M %Z')} ET / {uk.strftime('%H:%M %Z')} London"
    except Exception:
        return str(next_close)


def _load_last_state():
    try:
        with open(_STATE_PATH) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"was_open": False}


def _save_state(was_open):
    with open(_STATE_PATH, "w") as f:
        json.dump({"was_open": was_open}, f)


def check_and_alert():
    try:
        clock = broker.get_market_clock()
        is_open = bool(clock.is_open)
    except Exception as e:
        print(f"[market_open_alert] could not read Alpaca market clock: {e}")
        return

    last_state = _load_last_state()
    was_open = bool(last_state.get("was_open", False))

    if is_open and not was_open:
        next_close = getattr(clock, "next_close", None)
        message = "🔔 US stock market is now OPEN."
        close_text = _format_close_time(next_close)
        if close_text is not None:
            message += f"\nCloses: {close_text}"
        sent = telegram_notifier.send_telegram_message(message)
        print(f"[market_open_alert] transition closed->open, alert sent={sent}")
        if not sent and not telegram_notifier.is_configured():
            print("[market_open_alert] Telegram not configured -- see telegram_notifier.py setup notes.")

    _save_state(is_open)


if __name__ == "__main__":
    check_and_alert()
