#!/usr/bin/env python3
"""夜間レポート通知のテストスクリプト
実行方法:
  SLACK_WEBHOOK_URL=https://hooks.slack.com/... python test_notify_daily.py
"""
from screener import Signal
from notifier import notify_daily

mock_entries = [
    {
        "ticker": "4385.T", "name": "メルカリ", "market": "Growth",
        "current": 2340.0, "change_pct": 6.2, "volume_ratio": 4.5,
        "rsi": 72.0,
        "signal": Signal(level="strong_buy", reasons=["急騰 +6.2%", "上昇トレンド", "52週高値圏"]),
    },
    {
        "ticker": "4478.T", "name": "フリー", "market": "Growth",
        "current": 1850.0, "change_pct": -7.1, "volume_ratio": 5.2,
        "rsi": 22.0,
        "signal": Signal(level="strong_sell", reasons=["急落 -7.1%", "下降トレンド"]),
    },
    {
        "ticker": "4443.T", "name": "Sansan", "market": "Prime",
        "current": 3100.0, "change_pct": 1.2, "volume_ratio": 1.8,
        "rsi": 45.0,
        "signal": Signal(level="buy", reasons=["上昇トレンド"]),
    },
    {
        "ticker": "3697.T", "name": "SHIFT", "market": "Prime",
        "current": 8200.0, "change_pct": -2.3, "volume_ratio": 1.1,
        "rsi": 38.0,
        "signal": Signal(level="watch", reasons=[]),
    },
    {
        "ticker": "4425.T", "name": "Kudan", "market": "Growth",
        "current": 1650.0, "change_pct": 0.3, "volume_ratio": 2.5,
        "rsi": 24.0,
        "signal": Signal(level="buy", reasons=["RSI売られすぎ 24", "上昇トレンド"]),
    },
]

watchlist = [{"ticker": e["ticker"], "name": e["name"]} for e in mock_entries]

n = notify_daily(mock_entries, watchlist)
print(f"送信完了: 対象 {n} 銘柄")
