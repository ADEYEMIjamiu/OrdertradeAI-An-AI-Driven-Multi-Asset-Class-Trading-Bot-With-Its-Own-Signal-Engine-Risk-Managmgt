from config import STOP_LOSS, TAKE_PROFIT
class PaperTradingEngine:

    def __init__(self, starting_cash=10000):
        self.cash = starting_cash
        self.positions = {}
        self.trade_log = []

    # =========================
    # BUY
    # =========================
    def buy(self, ticker, price, allocation):

        from engines.broker_sync_engine import broker_already_owns_symbol

        print(f"📦 CURRENT POSITIONS BEFORE BUY: {self.positions}")

        ticker = ticker.upper()

        # 🚫 BLOCK duplicate
        if ticker in self.positions and self.positions[ticker]["shares"] > 0:
            print(f"⛔ PAPER BLOCKED BUY → already holding {ticker}")
            return

        # 🚫 BROKER CHECK
        try:
            if broker_already_owns_symbol(ticker):
                print(f"⛔ BROKER BLOCKED BUY → already holding {ticker}")
                return
        except Exception:
            pass

        amount_to_spend = self.cash * allocation

        if amount_to_spend <= 0:
            return

        shares = amount_to_spend / price
        self.cash -= amount_to_spend

        # ✅ 🔥 THIS IS WHERE YOU ADD STOP LOSS / TAKE PROFIT
        self.positions[ticker] = {
            "shares": shares,
            "entry_price": price,
            "stop_loss": price * (1 - STOP_LOSS),
            "take_profit": price * (1 + TAKE_PROFIT)
        }

        self.trade_log.append({
            "ticker": ticker,
            "action": "BUY",
            "price": price,
            "shares": shares,
            "amount": amount_to_spend
        })

        print(f"✅ PAPER BUY EXECUTED → {ticker} | {shares:.4f} shares")

        print(f"📦 CURRENT POSITIONS AFTER BUY: {self.positions}")
        print(f"💰 CASH LEFT: {self.cash}")

    # =========================
    # SELL
    # =========================
    def sell(self, ticker, price):

        ticker = ticker.upper()

        if ticker not in self.positions:
            print(f"⚠️ PAPER SELL BLOCKED → no position in {ticker}")
            return

        shares = self.positions[ticker]["shares"]
        entry_price = self.positions[ticker]["entry_price"]

        proceeds = shares * price
        profit = proceeds - (shares * entry_price)

        self.cash += proceeds

        del self.positions[ticker]

        self.trade_log.append({
            "ticker": ticker,
            "action": "SELL",
            "price": price,
            "shares": shares,
            "amount": proceeds,
            "profit": profit
        })

        print(f"🔥 PAPER SELL EXECUTED → {ticker} | Profit: {profit:.2f}")

        print(f"📦 CURRENT POSITIONS: {self.positions}")
        print(f"💰 CASH LEFT: {self.cash}")

    # =========================
    # RISK CHECK
    # =========================
    def check_risk(self, ticker, current_price):

        if ticker not in self.positions:
            return None

        position = self.positions[ticker]
        print(f"🔍 CHECKING RISK → {ticker} | Price: {current_price} | SL: {position['stop_loss']} | TP: {position['take_profit']}")

        if current_price <= position["stop_loss"]:
            print(f"🛑 STOP LOSS TRIGGERED → {ticker}")
            return "SELL"

        if current_price >= position["take_profit"]:
            print(f"🎯 TAKE PROFIT TRIGGERED → {ticker}")
            return "SELL"

        return None

    # =========================
    # PORTFOLIO VALUE
    # =========================
    def portfolio_value(self, latest_prices):

        value = self.cash

        for ticker, position in self.positions.items():
            if ticker in latest_prices:
                value += position["shares"] * latest_prices[ticker]

        return value