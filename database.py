import sqlite3
import os
from datetime import datetime
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "stockbot.db")
STARTING_BALANCE = float(os.getenv("STARTING_BALANCE", 100000))


def init_db():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id TEXT PRIMARY KEY,
                balance REAL NOT NULL DEFAULT %f
            )
        """ % STARTING_BALANCE)
        c.execute("""
            CREATE TABLE IF NOT EXISTS holdings (
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                shares REAL NOT NULL,
                avg_price REAL NOT NULL,
                PRIMARY KEY (user_id, symbol)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                action TEXT NOT NULL,
                shares REAL NOT NULL,
                price REAL NOT NULL,
                timestamp TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS watchlist (
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                PRIMARY KEY (user_id, symbol)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                target_price REAL NOT NULL,
                direction TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
        """)
        conn.commit()


@contextmanager
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    try:
        yield conn
    finally:
        conn.close()


def ensure_user(user_id: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)",
                   (user_id, STARTING_BALANCE))
        conn.commit()


def get_balance(user_id: str) -> float:
    ensure_user(user_id)
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT balance FROM users WHERE user_id=?", (user_id,))
        row = c.fetchone()
        return row[0] if row else STARTING_BALANCE


def set_balance(user_id: str, amount: float):
    ensure_user(user_id)
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (amount, user_id))
        conn.commit()


def get_holdings(user_id: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT symbol, shares, avg_price FROM holdings WHERE user_id=? AND shares > 0", (user_id,))
        return c.fetchall()


def get_holding(user_id: str, symbol: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT shares, avg_price FROM holdings WHERE user_id=? AND symbol=?", (user_id, symbol))
        return c.fetchone()


def record_transaction(user_id: str, symbol: str, action: str, shares: float, price: float):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO transactions (user_id, symbol, action, shares, price, timestamp) VALUES (?,?,?,?,?,?)",
            (user_id, symbol, action, shares, price, datetime.utcnow().isoformat())
        )
        conn.commit()


def buy_stock(user_id: str, symbol: str, shares: float, price: float) -> bool:
    ensure_user(user_id)
    cost = shares * price
    balance = get_balance(user_id)
    if cost > balance:
        return False

    existing = get_holding(user_id, symbol)
    with get_conn() as conn:
        c = conn.cursor()
        if existing:
            old_shares, old_avg = existing
            new_shares = old_shares + shares
            new_avg = ((old_shares * old_avg) + (shares * price)) / new_shares
            c.execute("UPDATE holdings SET shares=?, avg_price=? WHERE user_id=? AND symbol=?",
                      (new_shares, new_avg, user_id, symbol))
        else:
            c.execute("INSERT INTO holdings (user_id, symbol, shares, avg_price) VALUES (?,?,?,?)",
                      (user_id, symbol, shares, price))
        conn.commit()

    set_balance(user_id, balance - cost)
    record_transaction(user_id, symbol, "BUY", shares, price)
    return True


def sell_stock(user_id: str, symbol: str, shares: float, price: float) -> bool:
    existing = get_holding(user_id, symbol)
    if not existing or existing[0] < shares:
        return False

    old_shares, old_avg = existing
    new_shares = old_shares - shares
    with get_conn() as conn:
        c = conn.cursor()
        if new_shares <= 0:
            c.execute("DELETE FROM holdings WHERE user_id=? AND symbol=?", (user_id, symbol))
        else:
            c.execute("UPDATE holdings SET shares=? WHERE user_id=? AND symbol=?",
                      (new_shares, user_id, symbol))
        conn.commit()

    proceeds = shares * price
    balance = get_balance(user_id)
    set_balance(user_id, balance + proceeds)
    record_transaction(user_id, symbol, "SELL", shares, price)
    return True


def reset_user(user_id: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM holdings WHERE user_id=?", (user_id,))
        c.execute("DELETE FROM transactions WHERE user_id=?", (user_id,))
        c.execute("UPDATE users SET balance=? WHERE user_id=?", (STARTING_BALANCE, user_id))
        c.execute("INSERT OR IGNORE INTO users (user_id, balance) VALUES (?, ?)", (user_id, STARTING_BALANCE))
        conn.commit()


def get_history(user_id: str, limit: int = 10):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            "SELECT symbol, action, shares, price, timestamp FROM transactions WHERE user_id=? ORDER BY id DESC LIMIT ?",
            (user_id, limit)
        )
        return c.fetchall()


def add_watchlist(user_id: str, symbol: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("INSERT OR IGNORE INTO watchlist (user_id, symbol) VALUES (?, ?)", (user_id, symbol))
        conn.commit()


def remove_watchlist(user_id: str, symbol: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM watchlist WHERE user_id=? AND symbol=?", (user_id, symbol))
        conn.commit()


def get_watchlist(user_id: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT symbol FROM watchlist WHERE user_id=?", (user_id,))
        return [r[0] for r in c.fetchall()]


def get_all_user_ids():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT user_id FROM users")
        return [r[0] for r in c.fetchall()]


def add_alert(user_id: str, symbol: str, target_price: float, direction: str) -> int:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute(
            "INSERT INTO alerts (user_id, symbol, target_price, direction, active) VALUES (?,?,?,?,1)",
            (user_id, symbol, target_price, direction)
        )
        conn.commit()
        return c.lastrowid


def get_active_alerts():
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id, user_id, symbol, target_price, direction FROM alerts WHERE active=1")
        return c.fetchall()


def get_user_alerts(user_id: str):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("SELECT id, symbol, target_price, direction FROM alerts WHERE user_id=? AND active=1", (user_id,))
        return c.fetchall()


def deactivate_alert(alert_id: int):
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("UPDATE alerts SET active=0 WHERE id=?", (alert_id,))
        conn.commit()


def remove_alert(user_id: str, alert_id: int) -> bool:
    with get_conn() as conn:
        c = conn.cursor()
        c.execute("DELETE FROM alerts WHERE id=? AND user_id=?", (alert_id, user_id))
        conn.commit()
        return c.rowcount > 0
