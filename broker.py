from alpaca.trading.client import TradingClient
from alpaca.trading.requests import (
    MarketOrderRequest,
    GetOrdersRequest,
    GetPortfolioHistoryRequest,
)
from alpaca.trading.enums import OrderSide, TimeInForce
from dotenv import load_dotenv
from alpaca.trading.enums import QueryOrderStatus
import os

# Load .env file
load_dotenv()

API_KEY = os.getenv("ALPACA_API_KEY")
SECRET_KEY = os.getenv("ALPACA_SECRET_KEY")

# Connect to Alpaca Paper Trading
client = TradingClient(
    API_KEY,
    SECRET_KEY,
    paper=True
)
class Broker:
    def get_positions(self):
        return client.get_all_positions()

    def get_account(self):
        return client.get_account()

broker = Broker()

def get_account():
    """Return Alpaca account details."""
    return client.get_account()


def submit_buy_order(symbol, qty):
    """Submit a BUY market order."""
    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY
    )
    return client.submit_order(order)


def submit_sell_order(symbol, qty):
    """Submit a SELL market order."""
    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY
    )
    return client.submit_order(order)

def buy_stock(symbol, dollars):

    account = client.get_account()

    buying_power = float(account.buying_power)

    if dollars > buying_power:
        raise Exception("Not enough buying power.")

    order = MarketOrderRequest(
        symbol=symbol,
        notional=dollars,
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY
    )

    return client.submit_order(order_data=order)

def sell_stock(symbol, qty):

    order = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY
    )

    return client.submit_order(order_data=order)

def get_open_positions():
    return client.get_all_positions()

def get_orders():
    request = GetOrdersRequest(
        status=QueryOrderStatus.ALL,
        limit=50
    )
    return client.get_orders(filter=request)

from datetime import datetime
from zoneinfo import ZoneInfo


def _enum_value(value):
    """
    Safely converts Alpaca enum values such as OrderStatus.FILLED
    into simple lowercase strings such as 'filled'.
    """
    if value is None:
        return ""

    return str(getattr(value, "value", value)).lower()


def get_filled_orders_today():
    """
    Return Alpaca orders filled during the current US market date.

    Alpaca is the authoritative source when broker mode is enabled.
    """
    eastern_time = ZoneInfo("America/New_York")
    today_et = datetime.now(eastern_time).date()

    filled_orders = []

    for order in get_orders():
        submitted_at = getattr(order, "submitted_at", None)
        status = _enum_value(getattr(order, "status", None))

        if submitted_at is None:
            continue

        order_date_et = submitted_at.astimezone(eastern_time).date()

        if order_date_et == today_et and status == "filled":
            filled_orders.append(order)

    return filled_orders


def get_broker_trades_today_count():
    """
    Count today's filled BUY orders in Alpaca Paper Trading.

    This represents new long-position entries opened today.
    SELL orders are treated as exits and do not consume another
    daily entry slot.
    """
    try:
        from datetime import datetime, timezone
        from alpaca.trading.enums import OrderSide, OrderStatus

        orders = get_orders()
        today_utc = datetime.now(timezone.utc).date()

        filled_buy_count = 0

        for order in orders:
            submitted_at = getattr(order, "submitted_at", None)
            filled_at = getattr(order, "filled_at", None)
            order_status = getattr(order, "status", None)
            order_side = getattr(order, "side", None)

            order_time = filled_at or submitted_at

            if order_time is None:
                continue

            if order_time.tzinfo is None:
                order_time = order_time.replace(tzinfo=timezone.utc)

            is_today = order_time.astimezone(timezone.utc).date() == today_utc

            is_filled = (
                order_status == OrderStatus.FILLED
                or str(order_status).lower() == "filled"
            )

            is_buy = (
                order_side == OrderSide.BUY
                or str(order_side).lower() == "buy"
            )

            if is_today and is_filled and is_buy:
                filled_buy_count += 1

        return filled_buy_count

    except Exception as error:
        print(f"Could not count today's Alpaca entry trades: {error}")
        return 0
    
def get_market_clock():
    """Return the current Alpaca market clock."""
    return client.get_clock()


def check_broker_connection():
    """
    Validate the Alpaca paper-trading connection.

    Returns a structured broker health result so the dashboard
    and execution engine can safely determine broker availability.
    """

    try:
        account = client.get_account()
        clock = client.get_clock()

        return {
            "connected": True,
            "account_status": str(account.status),
            "trading_blocked": bool(account.trading_blocked),
            "buying_power": float(account.buying_power),
            "cash": float(account.cash),
            "equity": float(account.equity),
            "market_open": bool(clock.is_open),
            "market_timestamp": clock.timestamp,
            "next_market_open": clock.next_open,
            "next_market_close": clock.next_close,
            "error": None
        }

    except Exception as e:
        return {
            "connected": False,
            "account_status": None,
            "trading_blocked": True,
            "buying_power": 0.0,
            "cash": 0.0,
            "equity": 0.0,
            "market_open": False,
            "market_timestamp": None,
            "next_market_open": None,
            "next_market_close": None,
            "error": str(e)
        }
        
def get_portfolio_history(
    period="1M",
    timeframe="1D",
):
    """
    Get Alpaca paper-account portfolio history.

    Returns broker-backed equity and P/L history.
    """

    try:
        request = GetPortfolioHistoryRequest(
            period=period,
            timeframe=timeframe,
        )

        response = client.get_portfolio_history(request)

        return {
            "timestamp": list(
                getattr(response, "timestamp", []) or []
            ),
            "equity": list(
                getattr(response, "equity", []) or []
            ),
            "profit_loss": list(
                getattr(response, "profit_loss", []) or []
            ),
            "profit_loss_pct": list(
                getattr(response, "profit_loss_pct", []) or []
            ),
            "base_value": getattr(
                response,
                "base_value",
                None,
            ),
            "base_value_asof": getattr(
                response,
                "base_value_asof",
                None,
            ),
            "error": None,
        }

    except Exception as e:
        return {
            "timestamp": [],
            "equity": [],
            "profit_loss": [],
            "profit_loss_pct": [],
            "base_value": None,
            "base_value_asof": None,
            "error": str(e),
        }