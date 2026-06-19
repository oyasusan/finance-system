"""蓄積データからチャートを生成するモジュール"""
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # サーバー環境（ディスプレイなし）
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import matplotlib.font_manager as fm
import numpy as np
import pandas as pd

from storage import load_history

CHART_DIR = Path(__file__).parent / "charts"

_JP_FONT = "/usr/share/fonts/chromeos/notocjk/NotoSansCJK-Regular.ttc"

def _setup_font():
    try:
        fm.fontManager.addfont(_JP_FONT)
        plt.rcParams["font.family"] = "Noto Sans CJK JP"
    except Exception:
        pass  # フォントがなければシステムデフォルト

_setup_font()

BG      = "#1a1a2e"
PANEL   = "#0f0f1e"
GRID    = "#1e1e3e"
BORDER  = "#333355"
WHITE   = "#e0e0e0"
PRICE   = "#00d4ff"
MA5     = "#ffaa00"
MA25    = "#ff6b6b"
MA75    = "#a8ff78"
RSI_C   = "#ff9f43"
VOL_HI  = "#ff6b6b"
VOL_MID = "#ffd700"
VOL_LO  = "#4a4a7a"


def _style_ax(ax):
    ax.set_facecolor(PANEL)
    ax.tick_params(colors="#aaaaaa", labelsize=8)
    for spine in ax.spines.values():
        spine.set_color(BORDER)
    ax.yaxis.label.set_color(WHITE)
    ax.grid(color=GRID, linewidth=0.5, linestyle="--")


def _bar_width(dates: pd.Series) -> float:
    """データ間隔に応じたバー幅（matplotlib date単位 = 日）を返す"""
    if len(dates) < 2:
        return 0.02
    diff = (dates.iloc[-1] - dates.iloc[0]).total_seconds() / max(len(dates) - 1, 1)
    return max(diff / 86400 * 0.8, 1 / 86400)


def generate_chart(ticker: str, name: str = "", days: int = 30) -> Path:
    """単銘柄のチャートPNGを生成してファイルパスを返す"""
    rows = load_history(ticker, days=days)
    if len(rows) < 2:
        raise ValueError(f"{ticker}: データが不足しています（{len(rows)}件）")

    df = pd.DataFrame(rows)
    df["fetched_at"] = pd.to_datetime(df["fetched_at"])
    df = df.sort_values("fetched_at").reset_index(drop=True)

    CHART_DIR.mkdir(exist_ok=True)
    out_path = CHART_DIR / f"{ticker.replace('.', '_')}.png"

    fig = plt.figure(figsize=(13, 8), facecolor=BG)
    title = f"{name}  ({ticker})" if name else ticker
    fig.suptitle(title, color=WHITE, fontsize=13, fontweight="bold", y=0.98)

    gs = plt.GridSpec(3, 1, figure=fig, height_ratios=[3, 1, 1], hspace=0.06)
    ax1 = fig.add_subplot(gs[0])
    ax2 = fig.add_subplot(gs[1], sharex=ax1)
    ax3 = fig.add_subplot(gs[2], sharex=ax1)

    for ax in (ax1, ax2, ax3):
        _style_ax(ax)

    dates = df["fetched_at"]

    # --- 価格 + 移動平均 ---
    ax1.plot(dates, df["price"], color=PRICE, linewidth=1.5, label="現在値", zorder=3)
    for col, color, label in [("ma5", MA5, "MA5"), ("ma25", MA25, "MA25"), ("ma75", MA75, "MA75")]:
        valid = df[col].notna()
        if valid.sum() > 1:
            ax1.plot(dates[valid], df[col][valid], color=color, linewidth=0.9,
                     linestyle="--", alpha=0.85, label=label)
    ax1.set_ylabel("価格 (円)", color=WHITE, fontsize=9)
    ax1.legend(loc="upper left", fontsize=8, facecolor="#2a2a4a",
               labelcolor=WHITE, framealpha=0.7, edgecolor=BORDER)
    ax1.yaxis.set_major_formatter(plt.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    plt.setp(ax1.xaxis.get_majorticklabels(), visible=False)

    # --- RSI ---
    rsi_valid = df["rsi"].notna()
    if rsi_valid.sum() > 1:
        ax2.plot(dates[rsi_valid], df["rsi"][rsi_valid], color=RSI_C, linewidth=1.2)
        ax2.fill_between(dates[rsi_valid], df["rsi"][rsi_valid], 75,
                         where=df["rsi"][rsi_valid] >= 75, alpha=0.25, color=MA25)
        ax2.fill_between(dates[rsi_valid], df["rsi"][rsi_valid], 25,
                         where=df["rsi"][rsi_valid] <= 25, alpha=0.25, color=PRICE)
    ax2.axhline(75, color=MA25, linewidth=0.7, linestyle="--", alpha=0.8)
    ax2.axhline(25, color=PRICE, linewidth=0.7, linestyle="--", alpha=0.8)
    ax2.axhline(50, color="#444466", linewidth=0.5, linestyle=":")
    ax2.set_ylim(0, 100)
    ax2.set_yticks([25, 50, 75])
    ax2.set_ylabel("RSI", color=WHITE, fontsize=9)
    plt.setp(ax2.xaxis.get_majorticklabels(), visible=False)

    # --- 出来高比 ---
    vr = df["volume_ratio"].fillna(0)
    bw = _bar_width(dates)
    bar_colors = [VOL_HI if v >= 3 else VOL_MID if v >= 1.5 else VOL_LO for v in vr]
    ax3.bar(dates, vr, width=bw, color=bar_colors, alpha=0.85)
    ax3.axhline(3.0, color=VOL_HI, linewidth=0.7, linestyle="--", alpha=0.7)
    ax3.axhline(1.5, color=VOL_MID, linewidth=0.5, linestyle=":", alpha=0.6)
    ax3.set_ylabel("出来高比", color=WHITE, fontsize=9)

    # X軸
    ax3.xaxis.set_major_formatter(mdates.DateFormatter("%m/%d\n%H:%M"))
    ax3.xaxis.set_major_locator(mdates.AutoDateLocator(minticks=4, maxticks=10))
    plt.setp(ax3.xaxis.get_majorticklabels(), color="#aaaaaa", fontsize=7)

    # データ件数・期間を注記
    span = (dates.iloc[-1] - dates.iloc[0])
    note = f"{len(df)}件  {span.days}日{span.seconds//3600}時間分"
    fig.text(0.99, 0.01, note, ha="right", va="bottom", color="#666688", fontsize=7)

    fig.savefig(out_path, dpi=130, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    return out_path


def generate_all(watchlist: list[dict], days: int = 30) -> list[tuple[str, Path | Exception]]:
    """ウォッチリスト全銘柄のチャートを生成する"""
    results = []
    for item in watchlist:
        try:
            path = generate_chart(item["ticker"], item.get("name", ""), days=days)
            results.append((item["ticker"], path))
        except Exception as e:
            results.append((item["ticker"], e))
    return results
