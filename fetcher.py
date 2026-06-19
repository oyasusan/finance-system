"""Yahoo Finance からデータを取得するモジュール"""
import contextlib
import io
import os
import yfinance as yf
import pandas as pd
from datetime import datetime
from typing import Optional

@contextlib.contextmanager
def _suppress_stderr():
    """yfinance が標準エラーに出すノイズを抑制する"""
    with open(os.devnull, "w") as devnull:
        old = os.dup(2)
        os.dup2(devnull.fileno(), 2)
        try:
            yield
        finally:
            os.dup2(old, 2)
            os.close(old)


def fetch_quote(ticker: str) -> Optional[dict]:
    """単一銘柄のリアルタイムクォートを取得する"""
    try:
        t = yf.Ticker(ticker)
        info = t.info
        fast = t.fast_info

        current = fast.last_price or info.get("currentPrice") or info.get("regularMarketPrice")
        prev_close = fast.previous_close or info.get("previousClose") or info.get("regularMarketPreviousClose")

        if not current or not prev_close:
            return None

        change = current - prev_close
        change_pct = (change / prev_close) * 100

        volume = fast.three_month_average_volume or info.get("averageVolume3Month", 0)
        today_vol = info.get("regularMarketVolume") or fast.last_volume or 0

        return {
            "ticker": ticker,
            "current": current,
            "prev_close": prev_close,
            "change": change,
            "change_pct": change_pct,
            "today_volume": today_vol,
            "avg_volume": volume,
            "volume_ratio": today_vol / volume if volume > 0 else 0,
            "market_cap": info.get("marketCap"),
            "day_high": fast.day_high or info.get("dayHigh"),
            "day_low": fast.day_low or info.get("dayLow"),
            "week52_high": fast.year_high or info.get("fiftyTwoWeekHigh"),
            "week52_low": fast.year_low or info.get("fiftyTwoWeekLow"),
            "per": info.get("trailingPE"),
            "pbr": info.get("priceToBook"),
            "fetched_at": datetime.now(),
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def fetch_history(ticker: str, period: str = "3mo") -> Optional[pd.DataFrame]:
    """過去の価格データを取得する"""
    try:
        with _suppress_stderr():
            t = yf.Ticker(ticker)
            df = t.history(period=period)
        return df if not df.empty else None
    except Exception:
        return None


def calc_rsi(df: pd.DataFrame, period: int = 14) -> float:
    """RSIを計算する"""
    if df is None or len(df) < period + 1:
        return float("nan")
    close = df["Close"]
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(period).mean()
    loss = (-delta.clip(upper=0)).rolling(period).mean()
    rs = gain / loss
    rsi = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 1)


def calc_moving_averages(df: pd.DataFrame) -> dict:
    """移動平均を計算する"""
    if df is None or df.empty:
        return {}
    close = df["Close"]
    result = {}
    for period in [5, 25, 75]:
        if len(close) >= period:
            result[f"ma{period}"] = round(float(close.rolling(period).mean().iloc[-1]), 2)
    return result
