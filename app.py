"""Streamlit ダッシュボード - 日本新興市場・小型株モニター"""
import json
import math
import sqlite3
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

# ─── ページ設定 ────────────────────────────────────────────────────
st.set_page_config(
    page_title="新興株モニター",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown("""
<style>
/* スマホ対応: フォントサイズと余白を調整 */
.block-container { padding: 4.5rem 0.5rem 2rem !important; }
[data-testid="stMetricValue"] { font-size: 1.1rem; }
[data-testid="stMetricDelta"] { font-size: 0.8rem; }
/* タイトル折り返し */
h1 { font-size: 1.4rem !important; white-space: normal !important; word-break: break-word; }
/* カードの余白を小さく */
[data-testid="stVerticalBlockBorderWrapper"] {
    border-color: #333355 !important;
    border-radius: 8px !important;
    padding: 6px 10px !important;
    margin-bottom: 4px !important;
}
/* お気に入りボタンをコンパクトに */
[data-testid="stVerticalBlockBorderWrapper"] button {
    padding: 0 6px !important;
    min-height: 28px !important;
    font-size: 1rem !important;
}
/* ファボクイックボタン */
.fav-quick button {
    border: 1px solid #444466 !important;
    border-radius: 6px !important;
}
</style>
""", unsafe_allow_html=True)

DB_PATH        = Path(__file__).parent / "data.db"
WATCH_PATH     = Path(__file__).parent / "watchlist.json"
FAVORITES_PATH = Path(__file__).parent / "favorites.json"

# ─── データ読み込み ────────────────────────────────────────────────
@st.cache_data(ttl=60)
def load_watchlist() -> dict:
    with open(WATCH_PATH, encoding="utf-8") as f:
        cfg = json.load(f)
    return {i["ticker"]: i for i in cfg["watchlist"]}


@st.cache_data(ttl=60)
def load_latest_quotes() -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT q.*
        FROM quotes q
        INNER JOIN (
            SELECT ticker, MAX(fetched_at) AS max_time
            FROM quotes GROUP BY ticker
        ) t ON q.ticker = t.ticker AND q.fetched_at = t.max_time
        ORDER BY q.ticker
    """, conn)
    conn.close()
    return df


@st.cache_data(ttl=60)
def load_ticker_history(ticker: str, days: int = 30) -> pd.DataFrame:
    if not DB_PATH.exists():
        return pd.DataFrame()
    conn = sqlite3.connect(DB_PATH)
    df = pd.read_sql("""
        SELECT * FROM quotes
        WHERE ticker = ?
          AND fetched_at >= datetime('now', '+9 hours', ?)
        ORDER BY fetched_at ASC
    """, conn, params=(ticker, f"-{days} days"))
    conn.close()
    if not df.empty:
        df["fetched_at"] = pd.to_datetime(df["fetched_at"])
    return df


def signal_label(s: str | None) -> str:
    return {
        "strong_buy":  "★ 強買",
        "buy":         "▲ 買い",
        "watch":       "● 中立",
        "sell":        "▼ 売り",
        "strong_sell": "▽ 強売",
    }.get(s or "", "-")


def signal_color(s: str | None) -> str:
    return {
        "strong_buy":  "🟢",
        "buy":         "🔼",
        "watch":       "⬜",
        "sell":        "🔽",
        "strong_sell": "🔴",
    }.get(s or "", "")


def fmt_cap(v) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "-"
    if v >= 1e12:
        return f"{v/1e12:.2f}兆"
    elif v >= 1e8:
        return f"{v/1e8:.0f}億"
    return f"{v/1e4:.0f}万"


# ─── お気に入り管理 ────────────────────────────────────────────────
def load_favorites() -> set:
    if FAVORITES_PATH.exists():
        data = json.loads(FAVORITES_PATH.read_text(encoding="utf-8"))
        return set(data.get("favorites", []))
    return set()


def save_favorites(favs: set) -> None:
    FAVORITES_PATH.write_text(
        json.dumps({"favorites": sorted(favs)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if "favorites" not in st.session_state:
    st.session_state.favorites = load_favorites()


# ─── チャート生成 ─────────────────────────────────────────────────
def make_chart(df: pd.DataFrame, name: str) -> go.Figure:
    fig = make_subplots(
        rows=3, cols=1,
        shared_xaxes=True,
        row_heights=[0.55, 0.22, 0.23],
        vertical_spacing=0.03,
        subplot_titles=("価格・移動平均", "RSI", "出来高比"),
    )
    fig.add_trace(go.Scatter(
        x=df["fetched_at"], y=df["price"],
        name="現在値", line=dict(color="#00d4ff", width=2)
    ), row=1, col=1)
    for col, color, dash in [("ma5", "#ffaa00", "dash"), ("ma25", "#ff6b6b", "dash"), ("ma75", "#a8ff78", "dot")]:
        valid = df[col].notna()
        if valid.sum() > 1:
            fig.add_trace(go.Scatter(
                x=df["fetched_at"][valid], y=df[col][valid],
                name=col.upper(), line=dict(color=color, width=1, dash=dash), opacity=0.8
            ), row=1, col=1)

    rsi_valid = df["rsi"].notna()
    fig.add_trace(go.Scatter(
        x=df["fetched_at"][rsi_valid], y=df["rsi"][rsi_valid],
        name="RSI", line=dict(color="#ff9f43", width=1.5), fill="tozeroy",
        fillcolor="rgba(255,159,67,0.05)"
    ), row=2, col=1)
    for level, color in [(75, "rgba(255,107,107,0.3)"), (25, "rgba(72,219,251,0.3)")]:
        fig.add_hline(y=level, line_dash="dash", line_color=color, row=2, col=1)

    vr = df["volume_ratio"].fillna(0)
    bar_colors = ["#ff6b6b" if v >= 3 else "#ffd700" if v >= 1.5 else "#4a4a7a" for v in vr]
    fig.add_trace(go.Bar(
        x=df["fetched_at"], y=vr, name="出来高比",
        marker_color=bar_colors, opacity=0.85
    ), row=3, col=1)
    fig.add_hline(y=3.0, line_dash="dash", line_color="rgba(255,107,107,0.5)", row=3, col=1)

    fig.update_layout(
        title=dict(text=name, font=dict(size=14, color="white")),
        paper_bgcolor="#1a1a2e",
        plot_bgcolor="#0f0f1e",
        font=dict(color="#cccccc", size=11),
        legend=dict(orientation="h", y=1.02, font=dict(size=10)),
        height=480,
        margin=dict(l=40, r=20, t=60, b=30),
        showlegend=True,
    )
    fig.update_xaxes(gridcolor="#1e1e3e", showgrid=True)
    fig.update_yaxes(gridcolor="#1e1e3e", showgrid=True)
    return fig


# ─── メイン画面 ───────────────────────────────────────────────────
st.title("📈 新興市場・小型株モニター")

watchlist    = load_watchlist()
df_latest    = load_latest_quotes()

if df_latest.empty:
    st.warning("データがありません。monitor.py を実行してデータを蓄積してください。")
    st.stop()

if "fetched_at" in df_latest.columns:
    last_update_str = pd.to_datetime(df_latest["fetched_at"].max()).strftime("%Y-%m-%d %H:%M JST")
else:
    last_update_str = "-"
st.caption(f"最終更新: {last_update_str}")

tab1, tab2, tab3 = st.tabs(["📋 ウォッチリスト", "📊 チャート", "🔔 アラート"])

# ─── Tab1: ウォッチリスト（カードレイアウト）───────────────────────
with tab1:
    df_view = df_latest.copy()
    df_view["銘柄名"]   = df_view["ticker"].map(lambda t: watchlist.get(t, {}).get("name", t))
    df_view["市場"]     = df_view["ticker"].map(lambda t: watchlist.get(t, {}).get("market", "-"))
    df_view["シグナル"] = df_view["signal"].map(lambda s: signal_color(s) + " " + signal_label(s))
    df_view["前日比%"]  = df_view["change_pct"].map(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")
    df_view["出来高比"] = df_view["volume_ratio"].map(lambda v: f"{v:.1f}x" if pd.notna(v) else "-")
    df_view["RSI"]      = df_view["rsi"].map(lambda v: f"{v:.0f}" if pd.notna(v) else "-")
    df_view["時価総額"] = df_view["market_cap"].map(fmt_cap)

    col_filter, col_sort = st.columns([2, 2])
    with col_filter:
        markets = ["全て"] + sorted(df_view["市場"].unique().tolist())
        sel_market = st.selectbox("市場フィルタ", markets)
    with col_sort:
        sort_key = st.selectbox("並び替え", ["シグナル順", "前日比↓", "前日比↑", "出来高比↓"])

    if sel_market != "全て":
        df_view = df_view[df_view["市場"] == sel_market]

    sort_map = {
        "シグナル順": ("signal", False),
        "前日比↓":   ("change_pct", False),
        "前日比↑":   ("change_pct", True),
        "出来高比↓": ("volume_ratio", False),
    }
    sk, asc = sort_map[sort_key]
    df_view = df_view.sort_values(sk, ascending=asc, na_position="last")

    # ── カードレイアウト ───────────────────────────────────────────
    for _, row in df_view.iterrows():
        ticker = row["ticker"]
        is_fav = ticker in st.session_state.favorites
        code   = ticker.replace(".T", "")
        price  = row["price"]
        change = row["前日比%"]

        price_str = f"¥{price:,.0f}" if pd.notna(price) else "-"
        if isinstance(change, str) and change.startswith("+"):
            change_str = f"🟢 {change}"
        elif isinstance(change, str) and change.startswith("-"):
            change_str = f"🔴 {change}"
        else:
            change_str = change

        with st.container(border=True):
            c_info, c_fav = st.columns([9, 1])
            with c_info:
                st.markdown(
                    f"**{row['銘柄名']}** `{code}` "
                    f"<span style='font-size:0.75em;color:#888'>{row['市場']}</span>"
                    f"&nbsp;&nbsp;{row['シグナル']}",
                    unsafe_allow_html=True,
                )
            with c_fav:
                btn_label = "⭐" if is_fav else "☆"
                if st.button(btn_label, key=f"fav_{ticker}", help="お気に入り"):
                    if is_fav:
                        st.session_state.favorites.discard(ticker)
                    else:
                        st.session_state.favorites.add(ticker)
                    save_favorites(st.session_state.favorites)
                    st.rerun()

            st.caption(
                f"{price_str} · {change_str} · "
                f"RSI {row['RSI']} · 出来高 {row['出来高比']} · {row['時価総額']}"
            )

    # ── サマリーメトリクス ─────────────────────────────────────────
    st.markdown("---")
    buy_n   = (df_latest["signal"].isin(["strong_buy", "buy"])).sum()
    sell_n  = (df_latest["signal"].isin(["strong_sell", "sell"])).sum()
    surge_n = (df_latest["volume_ratio"].fillna(0) >= 3).sum()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("総銘柄数", len(df_latest))
    c2.metric("買いシグナル", buy_n)
    c3.metric("売りシグナル", sell_n)
    c4.metric("出来高3倍超", surge_n)

# ─── Tab2: チャート ───────────────────────────────────────────────
with tab2:
    ticker_list   = df_latest["ticker"].tolist()
    ticker_labels = [
        f"{watchlist.get(t, {}).get('name', t)} ({t.replace('.T', '')})"
        for t in ticker_list
    ]
    label_to_ticker = dict(zip(ticker_labels, ticker_list))

    # ── お気に入りクイックアクセス ─────────────────────────────────
    fav_in_list = [t for t in sorted(st.session_state.favorites) if t in ticker_list]
    if fav_in_list:
        st.markdown("**⭐ お気に入り（ワンタップで表示）**")
        n_cols = min(len(fav_in_list), 3)
        for i in range(0, len(fav_in_list), n_cols):
            chunk = fav_in_list[i : i + n_cols]
            btn_cols = st.columns(n_cols)
            for j, ft in enumerate(chunk):
                fn = watchlist.get(ft, {}).get("name", ft)
                with btn_cols[j]:
                    target_label = f"{fn} ({ft.replace('.T', '')})"
                    if st.button(fn, key=f"quick_{ft}", use_container_width=True):
                        st.session_state["chart_select"] = target_label
        st.divider()

    # ── 銘柄選択 ───────────────────────────────────────────────────
    sel_label  = st.selectbox("銘柄を選択", ticker_labels, key="chart_select")
    sel_ticker = label_to_ticker.get(sel_label, ticker_list[0])

    days_opt = st.slider("表示期間（日）", min_value=1, max_value=90, value=30)

    df_hist = load_ticker_history(sel_ticker, days=days_opt)
    if df_hist.empty or len(df_hist) < 2:
        st.info(f"データ蓄積中です（現在 {len(df_hist)} 件）。しばらく monitor.py を実行してください。")
    else:
        name = watchlist.get(sel_ticker, {}).get("name", sel_ticker)
        fig  = make_chart(df_hist, f"{name} ({sel_ticker})")
        st.plotly_chart(fig, use_container_width=True)

        # チャート画面でもお気に入りトグル
        is_fav    = sel_ticker in st.session_state.favorites
        fav_label = "⭐ お気に入りから削除" if is_fav else "☆ お気に入りに追加"
        if st.button(fav_label, key="chart_fav_toggle"):
            if is_fav:
                st.session_state.favorites.discard(sel_ticker)
            else:
                st.session_state.favorites.add(sel_ticker)
            save_favorites(st.session_state.favorites)
            st.rerun()

        st.caption(f"{len(df_hist)} 件のデータ")

# ─── Tab3: アラート ───────────────────────────────────────────────
with tab3:
    alert_df = df_latest[df_latest["signal"].isin(["strong_buy", "buy", "strong_sell", "sell"])].copy()
    if alert_df.empty:
        st.info("現在アクティブなシグナルはありません。")
    else:
        alert_df["銘柄名"]  = alert_df["ticker"].map(lambda t: watchlist.get(t, {}).get("name", t))
        alert_df["前日比%"] = alert_df["change_pct"].map(lambda v: f"{v:+.2f}%" if pd.notna(v) else "-")
        alert_df["出来高比"] = alert_df["volume_ratio"].map(lambda v: f"{v:.1f}x" if pd.notna(v) else "-")
        alert_df["シグナル"] = alert_df["signal"].map(lambda s: signal_color(s) + " " + signal_label(s))

        for _, row in alert_df.sort_values("signal").iterrows():
            with st.container():
                col_a, col_b = st.columns([3, 2])
                col_a.markdown(f"**{row['銘柄名']}** `{row['ticker'].replace('.T','')}`  {row['シグナル']}")
                col_b.markdown(f"前日比 **{row['前日比%']}** | 出来高 **{row['出来高比']}** | RSI `{row['rsi']:.0f}`" if pd.notna(row.get('rsi')) else "")
                if row.get("reasons"):
                    try:
                        reasons = json.loads(row["reasons"])
                        st.caption("　" + " / ".join(reasons))
                    except Exception:
                        pass
                st.markdown("---")
