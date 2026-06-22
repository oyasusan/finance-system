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
    if not ALERT_WEBHOOK_URL:
        print("[notifier] SLACK_ALERT_WEBHOOK_URL が未設定のため場中アラートをスキップ")
        return 0
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
    """夜間レポートを Slack に送信する。4セクション構成。"""
    today = datetime.now(JST).strftime("%Y-%m-%d")
    valid = [e for e in entries if "error" not in e]
    if not valid:
        return 0

    blocks = [_header(f"🌙 夜間レポート  {today}")]

    # ── ① 市況サマリー ───────────────────────────────────────────
    rising  = [e for e in valid if (e.get("change_pct") or 0) >  0.5]
    falling = [e for e in valid if (e.get("change_pct") or 0) < -0.5]
    flat    = [e for e in valid if abs(e.get("change_pct") or 0) <= 0.5]
    avg_chg = sum(e.get("change_pct") or 0 for e in valid) / len(valid)

    top_up   = sorted(rising,  key=lambda e: e.get("change_pct") or 0, reverse=True)[:3]
    top_down = sorted(falling, key=lambda e: e.get("change_pct") or 0)[:3]

    lines = [
        f"上昇 *{len(rising)}* 件  /  下落 *{len(falling)}* 件  /  横ばい *{len(flat)}* 件"
        f"　　平均変化率: *{avg_chg:+.2f}%*",
    ]
    if top_up:
        items = "　".join(
            f"`{e['ticker'].replace('.T','')}` {e.get('change_pct',0):+.1f}%" for e in top_up
        )
        lines.append(f"🔺 上昇 TOP: {items}")
    if top_down:
        items = "　".join(
            f"`{e['ticker'].replace('.T','')}` {e.get('change_pct',0):+.1f}%" for e in top_down
        )
        lines.append(f"🔻 下落 TOP: {items}")

    blocks += [_divider(), _section("*① 市況サマリー*\n" + "\n".join(lines))]

    # ── ② シグナルハイライト ────────────────────────────────────
    strong_buys  = [e for e in valid if e.get("signal") and e["signal"].level == "strong_buy"]
    strong_sells = [e for e in valid if e.get("signal") and e["signal"].level == "strong_sell"]

    if strong_buys or strong_sells:
        sig_lines = []
        for e in strong_buys:
            code = e["ticker"].replace(".T", "")
            sig_lines.append(
                f"🟢 *{e.get('name', code)}* `{code}`"
                f"  {e.get('change_pct', 0):+.1f}%  RSI {e.get('rsi') or '-'}"
            )
        for e in strong_sells:
            code = e["ticker"].replace(".T", "")
            sig_lines.append(
                f"🔴 *{e.get('name', code)}* `{code}`"
                f"  {e.get('change_pct', 0):+.1f}%  RSI {e.get('rsi') or '-'}"
            )
        blocks += [_divider(), _section("*② シグナルハイライト*\n" + "\n".join(sig_lines))]

    # ── ③ テクニカルイベント ────────────────────────────────────
    crosses_gc, crosses_dc, rsi_hot, rsi_cold = [], [], [], []

    for e in valid:
        code = e["ticker"].replace(".T", "")
        name = e.get("name", code)
        cross = _detect_cross(e["ticker"])
        if cross == "golden":
            crosses_gc.append(f"🟡 *{name}* `{code}`")
        elif cross == "dead":
            crosses_dc.append(f"⚫ *{name}* `{code}`")
        rsi = e.get("rsi")
        if rsi is not None:
            if rsi >= 75:
                rsi_hot.append(f"`{code}` {rsi:.0f}")
            elif rsi <= 25:
                rsi_cold.append(f"`{code}` {rsi:.0f}")

    tech_lines = []
    if crosses_gc:
        tech_lines.append("GC（ゴールデンクロス）: " + "  ".join(crosses_gc))
    if crosses_dc:
        tech_lines.append("DC（デッドクロス）: " + "  ".join(crosses_dc))
    if rsi_hot:
        tech_lines.append("RSI 過熱（≥75）: " + "  ".join(rsi_hot))
    if rsi_cold:
        tech_lines.append("RSI 売られすぎ（≤25）: " + "  ".join(rsi_cold))

    if tech_lines:
        blocks += [_divider(), _section("*③ テクニカルイベント*\n" + "\n".join(tech_lines))]
    else:
        blocks += [_divider(), _section("*③ テクニカルイベント*\n特記なし")]

    # ── ④ 翌日注目候補 ──────────────────────────────────────────
    predictions = []
    for e in valid:
        pred = _predict_buy(e)
        if pred:
            code = e["ticker"].replace(".T", "")
            predictions.append(
                f"⭐ *{e.get('name', code)}* `{code}`  {' / '.join(pred)}"
            )

    if predictions:
        blocks += [_divider(), _section("*④ 翌日注目候補*\n" + "\n".join(predictions))]
    else:
        blocks += [_divider(), _section("*④ 翌日注目候補*\n候補なし")]

    _post({"blocks": blocks})
    return len(valid)
