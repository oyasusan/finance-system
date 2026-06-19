"""スクリーニング条件の評価モジュール"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Signal:
    level: str      # "strong_buy" | "buy" | "watch" | "sell" | "strong_sell"
    reasons: list[str] = field(default_factory=list)


def evaluate(quote: dict, rsi: float, mas: dict, alerts: dict) -> Signal:
    """クォートデータからシグナルを判定する"""
    reasons = []
    score = 0

    change_pct = quote.get("change_pct", 0)
    volume_ratio = quote.get("volume_ratio", 0)
    current = quote.get("current", 0)
    alert_change = alerts.get("price_change_pct", 5.0)
    alert_vol = alerts.get("volume_surge_ratio", 3.0)

    # 価格変動チェック
    if change_pct >= alert_change:
        reasons.append(f"急騰 +{change_pct:.1f}%")
        score += 2
    elif change_pct <= -alert_change:
        reasons.append(f"急落 {change_pct:.1f}%")
        score -= 2

    # 出来高サージ
    if volume_ratio >= alert_vol:
        reasons.append(f"出来高 {volume_ratio:.1f}倍")
        score += 1 if change_pct > 0 else -1

    # RSI
    rsi_ob = alerts.get("rsi_overbought", 75)
    rsi_os = alerts.get("rsi_oversold", 25)
    if not _is_nan(rsi):
        if rsi >= rsi_ob:
            reasons.append(f"RSI過熱 {rsi}")
            score -= 1
        elif rsi <= rsi_os:
            reasons.append(f"RSI売られすぎ {rsi}")
            score += 1

    # 移動平均との関係
    ma25 = mas.get("ma25")
    ma75 = mas.get("ma75")
    if ma25 and ma75 and current:
        if current > ma25 > ma75:
            reasons.append("上昇トレンド")
            score += 1
        elif current < ma25 < ma75:
            reasons.append("下降トレンド")
            score -= 1

    # 52週高値更新
    w52h = quote.get("week52_high")
    if w52h and current and current >= w52h * 0.98:
        reasons.append("52週高値圏")
        score += 1

    if score >= 3:
        level = "strong_buy"
    elif score >= 1:
        level = "buy"
    elif score <= -3:
        level = "strong_sell"
    elif score <= -1:
        level = "sell"
    else:
        level = "watch"

    return Signal(level=level, reasons=reasons)


def _is_nan(v) -> bool:
    try:
        import math
        return math.isnan(v)
    except Exception:
        return True
