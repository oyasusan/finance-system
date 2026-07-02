"""Groq API (無料枠) を使ったシグナル銘柄向け一言AIコメント生成モジュール"""
import json
import os
import time
import urllib.error
import urllib.request

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL   = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"

# Cloudflare のボット検知回避のため、汎用UAではなくブラウザ相当のUAを付与する
_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# コメント生成対象とするシグナルレベル
_SIGNAL_TARGETS = {"strong_buy", "buy", "sell", "strong_sell"}

# 無料枠のレート制限に収めるための呼び出し間隔（秒）
_THROTTLE_SEC = 2.2

_SYSTEM_PROMPT = (
    "あなたは日本株の個人投資家向けアシスタントです。"
    "与えられたテクニカルシグナルを踏まえ、40字以内の日本語で一言コメントを書いてください。"
    "断定的な売買助言ではなく、注目ポイントを伝える中立的なコメントにしてください。"
    "コメントのみを出力し、前置きや記号は付けないでください。"
)


def _build_user_prompt(entry: dict) -> str:
    sig = entry["signal"]
    name = entry.get("name", entry["ticker"])
    reasons = "、".join(sig.reasons) if sig.reasons else "特になし"
    rsi = entry.get("rsi")
    rsi_str = f"{rsi:.0f}" if isinstance(rsi, (int, float)) and rsi == rsi else "-"
    return (
        f"銘柄名: {name}\n"
        f"シグナル: {sig.level}\n"
        f"前日比: {entry.get('change_pct', 0):+.2f}%\n"
        f"出来高比: {entry.get('volume_ratio', 0):.1f}倍\n"
        f"RSI: {rsi_str}\n"
        f"根拠: {reasons}"
    )


def generate_comment(entry: dict) -> str | None:
    """シグナルのある銘柄について一言AIコメントを生成する。対象外・失敗時は None。"""
    sig = entry.get("signal")
    if not sig or sig.level not in _SIGNAL_TARGETS:
        return None
    if not GROQ_API_KEY:
        return None

    payload = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(entry)},
        ],
        "max_tokens": 100,
        "temperature": 0.4,
    }
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            GROQ_URL,
            data=data,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "User-Agent": _USER_AGENT,
            },
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode("utf-8"))
        text = result["choices"][0]["message"]["content"].strip()
        return text[:120] or None
    except (urllib.error.URLError, KeyError, IndexError, json.JSONDecodeError) as e:
        print(f"[ai_commentary] コメント生成失敗 ({entry.get('ticker')}): {e}")
        return None


def annotate_entries(entries: list[dict]) -> int:
    """シグナルのある銘柄に entry['ai_comment'] を付与する（インプレース）。生成件数を返す。"""
    if not GROQ_API_KEY:
        print("[ai_commentary] GROQ_API_KEY が未設定のためAIコメント生成をスキップ")
        return 0

    targets = [e for e in entries if "error" not in e and e.get("signal") and e["signal"].level in _SIGNAL_TARGETS]
    count = 0
    for i, e in enumerate(targets):
        comment = generate_comment(e)
        if comment:
            e["ai_comment"] = comment
            count += 1
        if i < len(targets) - 1:
            time.sleep(_THROTTLE_SEC)
    return count
