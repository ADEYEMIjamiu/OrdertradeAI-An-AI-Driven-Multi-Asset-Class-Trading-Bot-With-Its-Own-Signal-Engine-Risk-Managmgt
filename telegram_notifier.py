"""
Telegram Notifier — pushes trade-fill alerts and on-demand performance
digests to a Telegram chat via the Bot API.

Setup: create a bot with @BotFather on Telegram (message it, /newbot,
follow the prompts) to get a bot token. Message your new bot once
(anything), then visit
    https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
in a browser to find your numeric chat id in the response. Add both to
.env:
    TELEGRAM_BOT_TOKEN=...
    TELEGRAM_CHAT_ID=...

If either is missing, every function here is a safe no-op -- the
trading app must never fail, slow down, or block a trade because
Telegram is unreachable or not yet configured. All network calls are
wrapped in try/except with a short timeout for exactly that reason.
"""

import os
import time

import requests

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

_API_URL = "https://api.telegram.org/bot{token}/sendMessage"

# Telegram enforces roughly one message per second per chat. A batch of
# many trade fills in one execution pass (e.g. 5 stocks + forex +
# commodities all filling together) previously fired that many sends
# within milliseconds of each other, tripping flood control after the
# first few. A first attempt at fixing this only reacted to the 429
# *after* it happened, with the retry wait arbitrarily capped at 5
# seconds -- too short whenever Telegram's actual cooldown (which scales
# up with repeated violations) was longer, so the retry got 429'd again
# and, being allowed only once, gave up for good. This throttles sends
# proactively instead, so the limit is rarely even hit, and backs off
# for Telegram's own requested wait time (not an arbitrary cap) with a
# few retries as a fallback for whatever slips through.
_MIN_SEND_INTERVAL_SECONDS = 1.1
_MAX_RETRIES = 3
_last_send_time = 0.0


def is_configured():
    return bool(TELEGRAM_BOT_TOKEN) and bool(TELEGRAM_CHAT_ID)


def send_telegram_message(text):
    """
    Best-effort send. Returns True on success, False otherwise --
    including "not configured yet", which is expected before setup,
    not an error. Never raises: a Telegram outage or bad token must
    never take down trade execution or the dashboard.
    """
    global _last_send_time

    if not is_configured():
        return False

    for attempt in range(_MAX_RETRIES + 1):
        elapsed = time.monotonic() - _last_send_time
        if elapsed < _MIN_SEND_INTERVAL_SECONDS:
            time.sleep(_MIN_SEND_INTERVAL_SECONDS - elapsed)

        try:
            response = requests.post(
                _API_URL.format(token=TELEGRAM_BOT_TOKEN),
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "text": text,
                    "parse_mode": "Markdown",
                },
                timeout=5,
            )
        except Exception as e:
            _last_send_time = time.monotonic()
            print(f"[telegram] send raised exception (attempt {attempt}): {e}")
            return False

        _last_send_time = time.monotonic()

        if response.status_code == 200:
            return True

        if response.status_code == 429 and attempt < _MAX_RETRIES:
            retry_after = 2
            try:
                retry_after = int(
                    response.json()
                    .get("parameters", {})
                    .get("retry_after", 2)
                )
            except Exception:
                pass
            print(
                f"[telegram] 429 on attempt {attempt}, "
                f"retry_after={retry_after}s, body={response.text[:200]!r}"
            )
            # Honor Telegram's actual requested wait, not an arbitrary
            # cap -- but don't let one stuck message block trade
            # execution indefinitely either.
            time.sleep(min(retry_after, 20))
            continue

        print(
            f"[telegram] send failed, status={response.status_code}, "
            f"body={response.text[:200]!r}"
        )
        return False

    print("[telegram] gave up after exhausting retries")
    return False


def _format_price(price):
    price = float(price)
    # Forex/some commodities trade at small values (e.g. EURUSD ~1.08)
    # where 2 decimal places would hide almost all the meaningful digits.
    return f"${price:,.4f}" if price < 10 else f"${price:,.2f}"


def _format_units(shares):
    """
    Up to 6 decimal places (needed for fractional crypto like
    0.001234 BTC), but trims trailing zeros so a 10-share stock trade
    doesn't read as "10.000000 units".
    """
    text = f"{float(shares):,.6f}".rstrip("0").rstrip(".")
    return text if text else "0"


def _format_signed_dollars(value):
    value = float(value)
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,.2f}"


def notify_trade_fill(
    ticker,
    action,
    price,
    shares,
    amount,
    asset_class="UNKNOWN",
    mode="LOCAL_PAPER",
    confidence=None,
    trade_grade=None,
    realized_pnl=None,
):
    """
    Format and send a trade-fill alert. Call this AFTER a fill is fully
    recorded (trade_log + trade_journal), so a Telegram hiccup can never
    block or duplicate the actual trade record -- notification is purely
    downstream of it.
    """
    emoji = "🟢" if str(action).upper() == "BUY" else "🔴"

    lines = [
        f"{emoji} *{str(action).upper()}* — {ticker} ({asset_class})",
        f"Price: {_format_price(price)}",
        f"Size: {_format_units(shares)} units (${float(amount):,.2f})",
    ]

    if confidence is not None:
        lines.append(f"Confidence: {float(confidence):.1f}%")
    if trade_grade:
        lines.append(f"Grade: {trade_grade}")
    if realized_pnl is not None:
        result = "profit" if realized_pnl >= 0 else "loss"
        lines.append(f"Realized {result}: {_format_signed_dollars(realized_pnl)}")

    lines.append(f"Mode: {mode}")

    send_telegram_message("\n".join(lines))


def notify_digest(period_label, overall, by_asset_class):
    """
    Format and send a Performance Digest as a Telegram message.
    `overall` and `by_asset_class` match the dicts returned by
    engines.digest_engine.calculate_performance_digest().
    """
    lines = [f"📅 *Performance Digest — {period_label}*", ""]

    lines.append(
        f"Trades Closed: {overall['trades_closed']} | "
        f"Win Rate: {overall['win_rate']:.1f}% | "
        f"Total P&L: {_format_signed_dollars(overall['total_pnl'])}"
    )

    for asset_class, stats in sorted(by_asset_class.items()):
        pf = stats["profit_factor"]
        pf_text = "N/A" if pf is None else f"{pf:.2f}"
        lines.append(
            f"\n*{asset_class}*: {stats['trades_closed']} closed, "
            f"{stats['win_rate']:.1f}% win rate, "
            f"{_format_signed_dollars(stats['total_pnl'])} P&L, PF {pf_text}"
        )

    send_telegram_message("\n".join(lines))
