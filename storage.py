"""SQLite への時系列データ蓄積モジュール"""
import json
import sqlite3
from pathlib import Path
from datetime import datetime, timezone, timedelta

JST = timezone(timedelta(hours=9))

DB_PATH = Path(__file__).parent / "data.db"


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker       TEXT    NOT NULL,
                fetched_at   TEXT    NOT NULL,
                price        REAL,
                change_pct   REAL,
                volume_ratio REAL,
                rsi          REAL,
                ma5          REAL,
                ma25         REAL,
                ma75         REAL,
                market_cap   INTEGER,
                per          REAL,
                signal       TEXT,
                reasons      TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_ticker_time ON quotes(ticker, fetched_at)"
        )


def save_entries(entries: list[dict]):
    init_db()
    now = datetime.now(JST).strftime("%Y-%m-%dT%H:%M:%S")
    rows = []
    for e in entries:
        if "error" in e:
            continue
        sig = e.get("signal")
        mas = e.get("mas", {})
        rows.append((
            e["ticker"], now,
            e.get("current"), e.get("change_pct"), e.get("volume_ratio"),
            e.get("rsi"),
            mas.get("ma5"), mas.get("ma25"), mas.get("ma75"),
            e.get("market_cap"), e.get("per"),
            sig.level if sig else None,
            json.dumps(sig.reasons, ensure_ascii=False) if sig else None,
        ))
    if not rows:
        return
    with _conn() as conn:
        conn.executemany("""
            INSERT INTO quotes
                (ticker, fetched_at, price, change_pct, volume_ratio,
                 rsi, ma5, ma25, ma75, market_cap, per, signal, reasons)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, rows)


def load_history(ticker: str, days: int = 30) -> list[dict]:
    init_db()
    with _conn() as conn:
        rows = conn.execute("""
            SELECT * FROM quotes
            WHERE ticker = ?
              AND fetched_at >= datetime('now', '+9 hours', ?)
            ORDER BY fetched_at ASC
        """, (ticker, f"-{days} days")).fetchall()
    return [dict(r) for r in rows]


def list_tickers() -> list[str]:
    init_db()
    with _conn() as conn:
        rows = conn.execute(
            "SELECT DISTINCT ticker FROM quotes ORDER BY ticker"
        ).fetchall()
    return [r[0] for r in rows]


def stats() -> list[dict]:
    """各銘柄の蓄積件数と期間を返す"""
    init_db()
    with _conn() as conn:
        rows = conn.execute("""
            SELECT ticker,
                   COUNT(*) AS cnt,
                   MIN(fetched_at) AS oldest,
                   MAX(fetched_at) AS newest
            FROM quotes
            GROUP BY ticker
            ORDER BY ticker
        """).fetchall()
    return [dict(r) for r in rows]
