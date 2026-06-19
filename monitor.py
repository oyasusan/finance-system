#!/usr/bin/env python3
"""
日本新興市場・小型株モニタリングシステム
使い方:
  python monitor.py             # ウォッチリストを監視（1回取得して表示）
  python monitor.py --loop      # 定期更新モード
  python monitor.py --add 1234.T 会社名   # ウォッチリストに追加
  python monitor.py --remove 1234.T       # ウォッチリストから削除
  python monitor.py --screener            # スクリーナーモード（シグナルあり銘柄のみ）
"""
import argparse
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from rich.console import Console
from rich.table import Table
from rich.text import Text
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, BarColumn, MofNCompleteColumn, TextColumn
from rich import box

from fetcher import fetch_quote, fetch_history, calc_rsi, calc_moving_averages
from screener import evaluate, Signal
from storage import save_entries, stats as db_stats
from notifier import notify_intraday, notify_daily

WATCHLIST_PATH = Path(__file__).parent / "watchlist.json"
console = Console()


def load_config() -> dict:
    with open(WATCHLIST_PATH, encoding="utf-8") as f:
        return json.load(f)


def save_config(cfg: dict):
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def fmt_price(v) -> str:
    if v is None:
        return "-"
    return f"{v:,.1f}"


def fmt_pct(v) -> Text:
    if v is None:
        return Text("-")
    s = f"{v:+.2f}%"
    if v >= 5:
        return Text(s, style="bold bright_red")
    elif v >= 2:
        return Text(s, style="red")
    elif v <= -5:
        return Text(s, style="bold bright_cyan")
    elif v <= -2:
        return Text(s, style="cyan")
    return Text(s, style="white")


def fmt_volume_ratio(v) -> Text:
    if not v:
        return Text("-")
    s = f"{v:.1f}x"
    if v >= 5:
        return Text(s, style="bold bright_magenta")
    elif v >= 3:
        return Text(s, style="magenta")
    elif v >= 1.5:
        return Text(s, style="yellow")
    return Text(s, style="dim")


def signal_badge(sig: Signal) -> Text:
    badges = {
        "strong_buy": Text("★ 強買", style="bold bright_green"),
        "buy":        Text("▲ 買い", style="green"),
        "watch":      Text("● 中立", style="dim white"),
        "sell":       Text("▼ 売り", style="yellow"),
        "strong_sell":Text("▽ 強売", style="bold bright_red"),
    }
    return badges.get(sig.level, Text("-"))


def fmt_market_cap(v) -> str:
    if not v:
        return "-"
    if v >= 1e12:
        return f"{v/1e12:.2f}兆"
    elif v >= 1e8:
        return f"{v/1e8:.0f}億"
    else:
        return f"{v/1e4:.0f}万"


def build_table(entries: list[dict], screener_mode: bool = False) -> Table:
    table = Table(
        title="[bold]日本新興市場・小型株モニター[/bold]",
        box=box.SIMPLE_HEAVY,
        show_header=True,
        header_style="bold cyan",
        border_style="grey50",
        expand=False,
        min_width=120,
    )
    table.add_column("コード", style="bold", no_wrap=True, width=7)
    table.add_column("銘柄名", no_wrap=True, width=16)
    table.add_column("市場", no_wrap=True, width=9)
    table.add_column("現在値", justify="right", no_wrap=True, width=9)
    table.add_column("前日比", justify="right", no_wrap=True, width=9)
    table.add_column("出来高比", justify="right", no_wrap=True, width=8)
    table.add_column("RSI", justify="right", no_wrap=True, width=6)
    table.add_column("時価総額", justify="right", no_wrap=True, width=9)
    table.add_column("PER", justify="right", no_wrap=True, width=6)
    table.add_column("シグナル", no_wrap=True, width=10)
    table.add_column("理由", width=20)

    for e in entries:
        if "error" in e:
            table.add_row(
                e["ticker"], e.get("name", "-"), e.get("market", "-"),
                Text("取得失敗", style="dim red"), *(["-"] * 7),
                Text(str(e["error"])[:30], style="dim red"),
            )
            continue

        sig = e.get("signal")
        if screener_mode and sig and sig.level == "watch":
            continue

        rsi_val = e.get("rsi", float("nan"))
        try:
            rsi_str = "-" if math.isnan(rsi_val) else f"{rsi_val:.0f}"
        except Exception:
            rsi_str = "-"

        reasons = "、".join(sig.reasons) if sig and sig.reasons else ""

        table.add_row(
            e["ticker"].replace(".T", ""),
            e.get("name", "-"),
            e.get("market", "-"),
            fmt_price(e.get("current")),
            fmt_pct(e.get("change_pct")),
            fmt_volume_ratio(e.get("volume_ratio")),
            rsi_str,
            fmt_market_cap(e.get("market_cap")),
            f"{e['per']:.1f}" if e.get("per") else "-",
            signal_badge(sig) if sig else "-",
            Text(reasons, style="dim"),
        )

    return table


def _fetch_one(item: dict, alerts: dict) -> dict:
    ticker = item["ticker"]
    quote = fetch_quote(ticker)
    if quote is None or "error" in quote:
        return {
            "ticker": ticker,
            "name": item["name"],
            "market": item.get("market", "-"),
            "error": quote.get("error", "データなし") if quote else "データなし",
        }
    hist = fetch_history(ticker)
    rsi = calc_rsi(hist)
    mas = calc_moving_averages(hist)
    sig = evaluate(quote, rsi, mas, alerts)
    return {
        **quote,
        "name": item["name"],
        "market": item.get("market", "-"),
        "rsi": rsi,
        "mas": mas,
        "signal": sig,
    }


def fetch_all(cfg: dict, max_workers: int = 8) -> list[dict]:
    alerts = cfg.get("alerts", {})
    items = cfg["watchlist"]
    ordered: dict[str, dict] = {item["ticker"]: {} for item in items}

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TextColumn("[dim]{task.fields[last]}"),
        console=console,
        transient=True,
    ) as progress:
        task_id = progress.add_task(
            f"データ取得中 (並列{max_workers})", total=len(items), last=""
        )
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(_fetch_one, item, alerts): item for item in items}
            for future in as_completed(futures):
                result = future.result()
                ordered[result["ticker"]] = result
                progress.update(task_id, advance=1, last=result.get("name", result["ticker"]))

    return [ordered[item["ticker"]] for item in items]


def run_once(cfg: dict, screener_mode: bool = False, notify_mode: str | None = None):
    start = datetime.now()
    entries = fetch_all(cfg)
    elapsed = (datetime.now() - start).seconds

    console.clear()
    table = build_table(entries, screener_mode=screener_mode)
    console.print(table)

    alerts_text = []
    for e in entries:
        sig = e.get("signal")
        if sig and sig.level in ("strong_buy", "strong_sell") and sig.reasons:
            color = "bright_green" if sig.level == "strong_buy" else "bright_red"
            alerts_text.append(
                f"[{color}]{e.get('name', e['ticker'])}[/{color}]: " + "、".join(sig.reasons)
            )
    if alerts_text:
        console.print(Panel("\n".join(alerts_text), title="[bold yellow]アラート[/bold yellow]", border_style="yellow"))

    save_entries(entries)

    notif_str = ""
    if notify_mode == "intraday":
        n = notify_intraday(entries)
        notif_str = f"  [yellow]Slack通知 {n}件[/yellow]" if n else "  [dim]Slack通知なし[/dim]"
    elif notify_mode == "daily":
        n = notify_daily(entries, cfg["watchlist"])
        notif_str = f"  [yellow]Slack夜間レポート送信[/yellow]"

    ok = sum(1 for e in entries if "error" not in e)
    ng = len(entries) - ok
    ng_str = f" / [red]失敗{ng}[/red]" if ng else ""
    console.print(
        f"\n  最終更新: [dim]{start.strftime('%Y-%m-%d %H:%M:%S')}[/dim]"
        f"  取得{ok}銘柄{ng_str}  [dim]({elapsed}秒)[/dim]  [dim]DB保存済[/dim]{notif_str}"
    )
    return entries


def cmd_add(cfg: dict, ticker: str, name: str, market: str = "Growth"):
    if not ticker.endswith(".T"):
        ticker = ticker + ".T"
    for item in cfg["watchlist"]:
        if item["ticker"] == ticker:
            console.print(f"[yellow]{ticker} は既に登録済みです[/yellow]")
            return
    cfg["watchlist"].append({"ticker": ticker, "name": name, "market": market})
    save_config(cfg)
    console.print(f"[green]追加しました: {ticker} ({name})[/green]")


def cmd_remove(cfg: dict, ticker: str):
    if not ticker.endswith(".T"):
        ticker = ticker + ".T"
    before = len(cfg["watchlist"])
    cfg["watchlist"] = [i for i in cfg["watchlist"] if i["ticker"] != ticker]
    if len(cfg["watchlist"]) < before:
        save_config(cfg)
        console.print(f"[green]削除しました: {ticker}[/green]")
    else:
        console.print(f"[yellow]{ticker} はウォッチリストに存在しません[/yellow]")


def cmd_chart(cfg: dict, target: str, days: int):
    from chart import generate_chart, generate_all
    from rich.progress import Progress, SpinnerColumn, TextColumn

    if target.lower() == "all":
        console.print(f"[bold]全銘柄チャート生成中 (過去{days}日分)[/bold]")
        results = generate_all(cfg["watchlist"], days=days)
        for ticker, result in results:
            if isinstance(result, Exception):
                console.print(f"  [red]✗[/red] {ticker}: {result}")
            else:
                console.print(f"  [green]✓[/green] {ticker} → {result}")
    else:
        ticker = target if target.endswith(".T") else target + ".T"
        item = next((i for i in cfg["watchlist"] if i["ticker"] == ticker), None)
        name = item["name"] if item else ""
        try:
            path = generate_chart(ticker, name, days=days)
            console.print(f"[green]チャート生成完了:[/green] {path}")
        except Exception as e:
            console.print(f"[red]エラー:[/red] {e}")


def cmd_dbstats():
    rows = db_stats()
    if not rows:
        console.print("[yellow]蓄積データがありません。まず monitor を実行してください。[/yellow]")
        return
    table = Table(title="蓄積データ統計", box=box.SIMPLE_HEAVY, header_style="bold cyan")
    table.add_column("銘柄コード", width=10)
    table.add_column("件数", justify="right", width=8)
    table.add_column("開始", width=20)
    table.add_column("最新", width=20)
    for r in rows:
        table.add_row(r["ticker"], str(r["cnt"]), r["oldest"], r["newest"])
    console.print(table)


def main():
    parser = argparse.ArgumentParser(description="日本新興・小型株モニター")
    parser.add_argument("--loop", action="store_true", help="定期更新モード")
    parser.add_argument("--screener", action="store_true", help="シグナルあり銘柄のみ表示")
    parser.add_argument("--add", nargs="+", metavar=("TICKER", "NAME"), help="銘柄追加 (例: --add 1234 会社名)")
    parser.add_argument("--remove", metavar="TICKER", help="銘柄削除")
    parser.add_argument("--market", default="Growth", help="--add 時の市場区分 (Growth/Standard/Prime)")
    parser.add_argument("--chart", metavar="TICKER|all", help="チャート生成 (例: --chart 4385 / --chart all)")
    parser.add_argument("--days", type=int, default=30, help="チャートの表示期間（日数、デフォルト30）")
    parser.add_argument("--stats", action="store_true", help="蓄積データの統計を表示")
    parser.add_argument("--mode", choices=["intraday", "daily"], default=None,
                        help="Slack通知モード: intraday=場中アラート, daily=夜間レポート")
    args = parser.parse_args()

    cfg = load_config()

    if args.add:
        ticker = args.add[0]
        name = args.add[1] if len(args.add) > 1 else ticker
        cmd_add(cfg, ticker, name, args.market)
        return

    if args.remove:
        cmd_remove(cfg, args.remove)
        return

    if args.chart:
        cmd_chart(cfg, args.chart, days=args.days)
        return

    if args.stats:
        cmd_dbstats()
        return

    if args.loop:
        interval = cfg.get("refresh_interval_sec", 60)
        console.print(f"[bold]ループモード開始 (更新間隔: {interval}秒)[/bold]  Ctrl+C で終了")
        try:
            while True:
                run_once(cfg, screener_mode=args.screener, notify_mode=args.mode)
                time.sleep(interval)
        except KeyboardInterrupt:
            console.print("\n[dim]終了しました[/dim]")
    else:
        run_once(cfg, screener_mode=args.screener, notify_mode=args.mode)


if __name__ == "__main__":
    main()
