"""Slack 通知モジュール
- notify_intraday: 場中用（急騰・急落・出来高サージ）
- notify_daily:    夜間用（ゴールデンクロス・複合シグナル予測）
"""
import json
import os
import urllib.request
import urllib.error
from datetime import datetime, timezone, timedelta
from pathlib import Path

JST = timezone(timedelta(hours=9))

from storage import load_history

WEBHOOK_URL       = os.environ.get("SLACK_WEBHOOK_URL", "")
ALERT_WEBHOOK_URL = os.environ.get("SLACK_ALERT_WEBHOOK_URL", "")

_ALERT_STATE_PATH = Path(__file__).parent / "alert_state.json"

# シグナルレベルの重大度ランク（大きいほど重大）
_LEVEL_RANK: dict[str, int] = {
    "watch":      0,
    "buy":        1,
    "sell":       1,
    "strong_buy": 2,
    "strong_sell":2,
}


def _load_alert_state(today: str) -> dict:
    if _ALERT_STATE_PATH.exists():
        try:
            state = json.loads(_ALERT_STATE_PATH.read_text(encoding="utf-8"))
            if state.get("date") == today:
                return state
        except Exception:
            pass
    return {"date": today, "sent": {}}


def _save_alert_state(state: dict) -> None:
    _ALERT_STATE_PATH.write_text(
        json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _should_notify(ticker: str, new_level: str, sent: dict[str, str]) -> bool:
    """当日未送信、またはシグナルレベルが昇格・方向転換した場合に True を返す。"""
    if ticker not in sent:
        return True
    prev = sent[ticker]
    # 買い⇔売りの方向転換（watch 除く）
    prev_buy = prev in ("buy", "strong_buy")
    new_buy  = new_level in ("buy", "strong_buy")
    if new_level != "watch" and prev_buy != new_buy:
        return True
    # 同方向でランク昇格
    return _LEVEL_RANK.get(new_level, 0) > _LEVEL_RANK.get(prev, 0)


# ─── Slack 送信 ────────────────────────────────────────────────────

def _post(payload: dict, url: str = "") -> bool:
    target = url or WEBHOOK_URL
    if not target:
        print("[notifier] Webhook URL が未設定のため通知をスキップ")
        return False
    try:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            target, data=data,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.URLError as e:
        print(f"[notifier] Slack 送信エラー: {e}")
        return False


def _divider():
    return {"type": "divider"}


def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _section(md: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": md}}


# ─── テクニカル判定 ────────────────────────────────────────────────

def _detect_cross(ticker: str) -> str | None:
    """ゴールデンクロス(golden) / デッドクロス(dead) を直近2件で判定"""
    rows = load_history(ticker, days=3)
    if len(rows) < 2:
        return None
    prev, curr = rows[-2], rows[-1]
    ma5p, ma25p = prev.get("ma5"), prev.get("ma25")
    ma5c, ma25c = curr.get("ma5"), curr.get("ma25")
    if any(v is None for v in [ma5p, ma25p, ma5c, ma25c]):
        return None
    if ma5p < ma25p and ma5c >= ma25c:
        return "golden"
    if ma5p > ma25p and ma5c <= ma25c:
        return "dead"
    return None


def _predict_buy(entry: dict) -> list[str]:
    """複合シグナルから翌日上昇予測の根拠リストを返す（空なら予測なし）"""
    sig = entry.get("signal")
    if not sig:
        return []
    reasons = []
    r = sig.reasons
    change = entry.get("change_pct", 0)
    rsi = entry.get("rsi", 50)
    vol = entry.get("volume_ratio", 0)

    if rsi and rsi <= 30:
        reasons.append(f"RSI売られすぎ({rsi:.0f})")
    if vol >= 2.0 and change > 0:
        reasons.append(f"出来高急増({vol:.1f}倍)＋上昇")
    if any("上昇トレンド" in x for x in r):
        reasons.append("上昇トレンド継続")
    if any("52週高値" in x for x in r):
        reasons.append("52週高値圏")

    # 2つ以上の根拠があるときのみ予測として扱う
    return reasons if len(reasons) >= 2 else []


# ─── 場中通知 ─────────────────────────────────────────────────────

def notify_intraday(entries: list[dict]) -> int:
    """急騰・急落・出来高サージ銘柄を通知。送信件数を返す。
    当日に同シグナルレベル以下の通知済み銘柄はスキップし、
    レベル昇格または方向転換（買い⇔売り）のときのみ再通知する。
    """
    today = datetime.now(JST).strftime("%Y-%m-%d")
    state = _load_alert_state(today)
    sent  = state["sent"]

    blocks = [_header(f"📈 場中アラート  {datetime.now(JST).strftime('%m/%d %H:%M')} JST")]
    count = 0
    to_update: dict[str, str] = {}

    for e in entries:
        sig = e.get("signal")
        if not sig:
            continue
        intraday = [r for r in sig.reasons if any(k in r for k in ("急騰", "急落", "出来高"))]
        if not intraday:
            continue

        ticker = e["ticker"]
        if not _should_notify(ticker, sig.level, sent):
            continue

        emoji  = "🚀" if "buy" in sig.level else "🔻"
        change = e.get("change_pct", 0)
        vol    = e.get("volume_ratio", 0)
        price  = e.get("current", 0)
        name   = e.get("name", ticker)
        code   = ticker.replace(".T", "")
        # 再通知の場合はレベル昇格ラベルを付加
        label  = "  ⬆ レベル上昇" if ticker in sent else ""

        blocks.append(_divider())
        blocks.append(_section(
            f"{emoji} *{name}*  `{code}`{label}\n"
            f"前日比: *{change:+.2f}%*  |  出来高: *{vol:.1f}倍*  |  現在値: *{price:,.0f}円*\n"
            f"理由: {' / '.join(intraday)}"
        ))
        to_update[ticker] = sig.level
        count += 1

    if count == 0:
        return 0

    _post({"blocks": blocks}, url=ALERT_WEBHOOK_URL)
    sent.update(to_update)
    _save_alert_state(state)
    return count


# ─── 夜間通知 ─────────────────────────────────────────────────────

def notify_daily(entries: list[dict], watchlist: list[dict]) -> int:
    """ゴールデンクロス・デッドクロス・複合シグナル予測を通知。送信件数を返す。"""
    crosses_gc, crosses_dc, predictions = [], [], []

    for e in entries:
        name = e.get("name", e["ticker"])
        code = e["ticker"].replace(".T", "")
        cross = _detect_cross(e["ticker"])
        if cross == "golden":
            crosses_gc.append(f"🟡 *{name}* `{code}`  MA5がMA25を上抜け")
        elif cross == "dead":
            crosses_dc.append(f"⚫ *{name}* `{code}`  MA5がMA25を下抜け")

        pred = _predict_buy(e)
        if pred:
            predictions.append(f"⭐ *{name}* `{code}`\n　　{' / '.join(pred)}")

    if not crosses_gc and not crosses_dc and not predictions:
        return 0

    blocks = [_header(f"🌙 夜間レポート  {datetime.now(JST).strftime('%Y-%m-%d')}")]

    if crosses_gc:
        blocks += [_divider(), _section("*ゴールデンクロス検出*\n" + "\n".join(crosses_gc))]
    if crosses_dc:
        blocks += [_divider(), _section("*デッドクロス検出*\n" + "\n".join(crosses_dc))]
    if predictions:
        blocks += [_divider(), _section("*翌日上昇予測（複合シグナル）*\n" + "\n".join(predictions))]

    _post({"blocks": blocks})
    return len(crosses_gc) + len(crosses_dc) + len(predictions)
