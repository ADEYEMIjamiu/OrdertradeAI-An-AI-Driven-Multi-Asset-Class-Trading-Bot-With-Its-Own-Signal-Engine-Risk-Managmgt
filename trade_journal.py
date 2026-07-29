import sqlite3
from datetime import datetime

DB_NAME = "trade_journal.db"


def init_trade_journal():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            time TEXT,
            ticker TEXT,
            action TEXT,
            price REAL,
            shares REAL,
            amount REAL,
            confidence REAL,
            trend_score REAL,
            reason TEXT,
            mode TEXT
        )
    """)

    conn.commit()
    conn.close()


def log_trade(ticker, action, price, shares, amount, confidence, trend_score, reason, mode):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO trades (
            time, ticker, action, price, shares, amount,
            confidence, trend_score, reason, mode
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ticker,
        action,
        price,
        shares,
        amount,
        confidence,
        trend_score,
        reason,
        mode
    ))

    conn.commit()
    conn.close()

    print(f"✅ Trade logged: {ticker} | {action} | {price}")


def load_trade_journal():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute("""
        SELECT time, ticker, action, price, shares, amount,
               confidence, trend_score, reason, mode
        FROM trades
        ORDER BY id DESC
    """)

    rows = cursor.fetchall()
    conn.close()

    return rows
