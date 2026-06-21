#!/usr/bin/env python3
"""場中アラート通知のテストスクリプト
実行方法:
  SLACK_WEBHOOK_URL=https://hooks.slack.com/... python test_notify_intraday.py
"""
from screener import Signal
from notifier import notify_intraday

mock_entries = [
    {
        "ticker": "4385.T",
        "name": "メルカリ",
        "market": "Growth",
        "current": 2340.0,
        "change_pct": 6.2,
        "volume_ratio": 4.5,
        "signal": Signal(level="buy", reasons=["急騰 +6.2%", "出来高 4.5倍"]),
    },
    {
        "ticker": "4478.T",
        "name": "フリー",
        "market": "Growth",
        "current": 1850.0,
        "change_pct": -7.1,
        "volume_ratio": 5.2,
        "signal": Signal(level="strong_sell", reasons=["急落 -7.1%", "出来高 5.2倍"]),
    },
]

n = notify_intraday(mock_entries)
print(f"送信件数: {n}")
