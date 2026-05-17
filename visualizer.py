"""
visualizer.py — Backtest Dashboard  (ParanoidSignals™ 7.9-X)

Layout
------
  Row 0  KPI metric boxes  [full width, compact]
  Row 1  Price chart       [full width, tallest]
  Row 2  Equity curve  |  Monthly returns heatmap
  Row 3  Per-trade P&L bars  |  Win/Loss donut
"""

import matplotlib
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.patches as mpatches
import matplotlib.colors as mcolors
import matplotlib.ticker as mticker
import numpy as np
import pandas as pd

from backtest_engine import BacktestResult, Trade
from config import SYMBOL, TIMEFRAME, SETUP_TYPE, TPS_TYPE, SIDEWAYS_FILTER_ENABLED, HTF_MINUTES

plt.style.use("dark_background")

_GREEN  = "#26a69a"
_RED    = "#ef5350"
_YELLOW = "#ffd54f"
_BLUE   = "#42a5f5"
_PURPLE = "#ab47bc"
_GRAY   = "#546e7a"
_BG     = "#0d1117"
_PANEL  = "#161b22"
_PANEL2 = "#21262d"
_WHITE  = "#e6edf3"
_DIM    = "#8b949e"


def plot_results(result: BacktestResult, df_price: pd.DataFrame = None):
    """Display full backtest dashboard as an interactive matplotlib window."""
    trades = result.closed_trades

    fig = plt.figure(figsize=(22, 13), facecolor=_BG)
    _htf_label = f"{HTF_MINUTES}m" if HTF_MINUTES < 60 else f"{HTF_MINUTES//60}H{HTF_MINUTES%60 or ''}"
    try:
        fig.canvas.manager.set_window_title(
            f"Backtest Dashboard \u2014 {SYMBOL} {TIMEFRAME}  HTF:{_htf_label}"
        )
    except Exception:
        pass

    gs = gridspec.GridSpec(
        4, 4,
        figure=fig,
        height_ratios=[0.65, 2.8, 1.8, 1.8],
        hspace=0.58,
        wspace=0.38,
        left=0.05, right=0.98,
        top=0.93, bottom=0.05,
    )

    ax_kpi     = fig.add_subplot(gs[0, :])
    ax_price   = fig.add_subplot(gs[1, :])
    ax_equity  = fig.add_subplot(gs[2, :2])
    ax_monthly = fig.add_subplot(gs[2, 2:])
    ax_trades  = fig.add_subplot(gs[3, :3])
    ax_dist    = fig.add_subplot(gs[3, 3])

    for ax in [ax_equity, ax_monthly, ax_trades]:
        _style_ax(ax)

    # Main title with period
    t0 = trades[0].entry_time if trades else ""
    t1 = (trades[-1].exit_time or trades[-1].entry_time) if trades else ""
    fmt = "%Y-%m-%d"
    t0s = t0.strftime(fmt) if hasattr(t0, "strftime") else str(t0)
    t1s = t1.strftime(fmt) if hasattr(t1, "strftime") else str(t1)
    fig.suptitle(
        f"ParanoidSignals\u2122 7.9-X  \u00b7  {SYMBOL} {TIMEFRAME}  \u00b7  "
        f"HTF:{_htf_label}  \u00b7  {SETUP_TYPE} / {TPS_TYPE}  \u00b7  {t0s}  \u2192  {t1s}",
        color=_WHITE, fontsize=12, fontweight="bold", y=0.98,
    )

    _plot_kpi(ax_kpi, result)
    _plot_price(ax_price, df_price, trades)
    _plot_equity(ax_equity, result)
    _plot_monthly(ax_monthly, trades, result.initial_capital)
    _plot_trade_pnl(ax_trades, trades)
    _plot_winloss(ax_dist, result)

    plt.tight_layout(rect=[0, 0, 1, 0.96])
    plt.show()


# =============================================================================
# KPI METRIC BOXES  (Row 0)
# =============================================================================

def _plot_kpi(ax, result: BacktestResult):
    """Draw coloured KPI card boxes across the top of the dashboard."""
    ax.set_facecolor(_BG)
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")

    pf_color = _GREEN if result.profit_factor >= 2 else (_YELLOW if result.profit_factor >= 1 else _RED)
    kpis = [
        ("Total Trades",   f"{result.total_trades}",                         _BLUE),
        ("Win Rate",       f"{result.win_rate:.2f}%",                        _GREEN),
        ("Profit Factor",  f"{result.profit_factor:.2f}",                    pf_color),
        ("Net Profit",     f"${result.net_profit:+,.2f}",
         _GREEN if result.net_profit >= 0 else _RED),
        ("Return",         f"{result.return_pct:+.2f}%",
         _GREEN if result.return_pct >= 0 else _RED),
        ("Max Drawdown",   f"-{result.max_drawdown_pct:.2f}%",               _RED),
        ("Wins / Losses",  f"{len(result.wins)} / {len(result.losses)}",     _GRAY),
        ("Avg Win",        f"${result.avg_win:,.2f}",                        _GREEN),
        ("Avg Loss",       f"-${result.avg_loss:,.2f}",                      _RED),
    ]

    n    = len(kpis)
    gap  = 0.007
    bw   = (1.0 - gap * (n + 1)) / n

    for i, (label, value, color) in enumerate(kpis):
        x0 = gap + i * (bw + gap)
        rect = mpatches.FancyBboxPatch(
            (x0, 0.06), bw, 0.86,
            boxstyle="round,pad=0.015",
            facecolor=_PANEL2,
            edgecolor=color,
            linewidth=1.4,
            transform=ax.transAxes,
            clip_on=False,
        )
        ax.add_patch(rect)
        cx = x0 + bw / 2
        ax.text(cx, 0.76, label,
                transform=ax.transAxes,
                ha="center", va="center",
                color=_DIM, fontsize=7, fontweight="normal")
        ax.text(cx, 0.32, value,
                transform=ax.transAxes,
                ha="center", va="center",
                color=color, fontsize=12, fontweight="bold")


# =============================================================================
# PANEL HELPERS
# =============================================================================

def _style_ax(ax):
    ax.set_facecolor(_PANEL)
    ax.tick_params(colors=_DIM, labelsize=8)
    ax.spines["bottom"].set_color("#252b36")
    ax.spines["left"].set_color("#252b36")
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    ax.grid(True, color="#1c2130", linewidth=0.5, alpha=0.9)
    ax.title.set_color(_WHITE)
    ax.xaxis.label.set_color(_DIM)
    ax.yaxis.label.set_color(_DIM)


def _plot_price(ax, df: pd.DataFrame, trades: list):
    """Price close-line with entry/exit markers."""
    _style_ax(ax)
    ax.set_title(
        "Price Chart  \u00b7  \u25b2 Long entry   \u25bc Short entry   "
        "(green = win  \u00b7  red = loss)",
        fontsize=9, color=_WHITE,
    )

    if df is not None and not df.empty:
        close = df["close"]
        times = np.arange(len(df))
        ax.plot(times, close.values, color="#3d4a5c", linewidth=0.6,
                alpha=0.9, zorder=1)

        if len(trades) <= 800:
            for t in trades:
                ei  = _nearest_idx(df.index, t.entry_time)
                xi  = _nearest_idx(df.index, t.exit_time)
                col = _GREEN if t.pnl_abs > 0 else _RED
                if t.direction == 1:
                    ax.scatter(ei, t.entry_price, marker="^",
                               color=_GREEN, s=25, zorder=5, linewidths=0)
                    ax.scatter(xi, t.exit_price, marker="v",
                               color=col, s=18, zorder=5, linewidths=0)
                else:
                    ax.scatter(ei, t.entry_price, marker="v",
                               color=_RED, s=25, zorder=5, linewidths=0)
                    ax.scatter(xi, t.exit_price, marker="^",
                               color=col, s=18, zorder=5, linewidths=0)
        else:
            # Too many trades – shade win/loss zones with vertical lines
            for t in trades:
                ei  = _nearest_idx(df.index, t.entry_time)
                col = _GREEN if t.pnl_abs > 0 else _RED
                ax.axvline(ei, color=col, linewidth=0.25, alpha=0.2, zorder=2)

        step     = max(1, len(df) // 10)
        tick_pos = list(range(0, len(df), step))
        tick_lbl = [df.index[i].strftime("%b %d '%y") for i in tick_pos]
        ax.set_xticks(tick_pos)
        ax.set_xticklabels(tick_lbl, rotation=20, ha="right", fontsize=7)
        ax.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda x, _: f"{x:,.0f}"))
    elif trades:
        _plot_price_from_trades(ax, trades)

    ax.legend(
        handles=[
            mpatches.Patch(color=_GREEN, label="Long / Win"),
            mpatches.Patch(color=_RED,   label="Short / Loss"),
        ],
        loc="upper left", fontsize=7,
        facecolor=_PANEL, edgecolor=_GRAY, labelcolor=_WHITE,
    )


def _plot_price_from_trades(ax, trades: list):
    """Fallback when no OHLCV data is available."""
    for t in trades:
        col = _GREEN if t.pnl_abs > 0 else _RED
        ax.plot([t.entry_time, t.exit_time],
                [t.entry_price, t.exit_price],
                color=col, linewidth=0.8, alpha=0.5)


def _plot_equity(ax, result: BacktestResult):
    """Equity curve with profit/drawdown shading."""
    ax.set_title("Equity Curve", fontsize=9, color=_WHITE)
    eq = result.equity_curve
    if eq.empty:
        return

    times  = np.arange(len(eq))
    values = eq.values
    start  = result.initial_capital

    ax.plot(times, values, color=_BLUE, linewidth=1.5, zorder=3)

    # Green fill above starting capital
    ax.fill_between(times, start, values,
                    where=values >= start,
                    color=_GREEN, alpha=0.10, zorder=2)
    # Red fill below starting capital
    ax.fill_between(times, start, values,
                    where=values < start,
                    color=_RED, alpha=0.18, zorder=2)
    # Drawdown shading (peak to current)
    peak = np.maximum.accumulate(values)
    ax.fill_between(times, values, peak,
                    where=values < peak,
                    color=_RED, alpha=0.13, zorder=2)

    ax.axhline(start, color=_GRAY, linewidth=0.8, linestyle="--",
               alpha=0.6, label=f"Start ${start:,.0f}")

    ax.annotate(
        f"${values[-1]:,.0f}",
        xy=(times[-1], values[-1]),
        xytext=(-55, 10), textcoords="offset points",
        color=_BLUE, fontsize=9, fontweight="bold",
        arrowprops=dict(arrowstyle="-", color=_BLUE, lw=0.5),
    )

    step     = max(1, len(eq) // 6)
    tick_pos = list(range(0, len(eq), step))
    tick_lbl = [eq.index[i].strftime("%b '%y") for i in tick_pos]
    ax.set_xticks(tick_pos)
    ax.set_xticklabels(tick_lbl, rotation=20, ha="right", fontsize=7)
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax.legend(fontsize=7, facecolor=_PANEL, edgecolor=_GRAY,
              labelcolor=_WHITE, loc="upper left")


def _plot_trade_pnl(ax, trades: list):
    """Per-trade P&L bars + cumulative P&L overlay."""
    ax.set_title(
        "Per-Trade P&L ($)  \u00b7  Yellow line = Cumulative P&L",
        fontsize=9, color=_WHITE,
    )
    if not trades:
        return

    pnls   = [t.pnl_abs for t in trades]
    colors = [_GREEN if p > 0 else _RED for p in pnls]
    xs     = np.arange(len(pnls))

    ax.bar(xs, pnls, color=colors, width=0.8, zorder=2)
    ax.axhline(0, color=_GRAY, linewidth=0.8)

    ax2 = ax.twinx()
    ax2.set_facecolor(_PANEL)
    ax2.tick_params(colors=_DIM, labelsize=7)
    ax2.spines["top"].set_visible(False)
    ax2.spines["left"].set_color("#252b36")
    ax2.spines["right"].set_color("#252b36")
    cumulative = np.cumsum(pnls)
    ax2.plot(xs, cumulative, color=_YELLOW, linewidth=1.5, zorder=3)
    ax2.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax2.set_ylabel("Cumulative $", color=_YELLOW, fontsize=7)

    ax.set_xlabel("Trade #", fontsize=8, color=_DIM)
    ax.set_ylabel("P&L / trade ($)", fontsize=8, color=_DIM)
    ax.yaxis.set_major_formatter(
        mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))


def _plot_monthly(ax, trades: list, initial_capital: float):
    """Monthly returns heatmap."""
    ax.set_title("Monthly Returns  (%)", fontsize=9, color=_WHITE)
    if not trades:
        return

    monthly = {}
    for t in trades:
        if t.exit_time is None:
            continue
        et = t.exit_time
        if hasattr(et, "tzinfo") and et.tzinfo is not None:
            et = et.replace(tzinfo=None)
        key = (et.year, et.month)
        monthly[key] = monthly.get(key, 0.0) + t.pnl_abs

    if not monthly:
        return

    years       = sorted({k[0] for k in monthly})
    months      = list(range(1, 13))
    month_names = ["Jan","Feb","Mar","Apr","May","Jun",
                   "Jul","Aug","Sep","Oct","Nov","Dec"]

    data    = np.full((len(years), 12), np.nan)
    capital = initial_capital
    for yi, yr in enumerate(years):
        for mi, mo in enumerate(months):
            pnl = monthly.get((yr, mo), 0.0)
            pct = pnl / capital * 100.0 if capital else 0.0
            capital += pnl
            if (yr, mo) in monthly:
                data[yi, mi] = pct

    cmap = mcolors.LinearSegmentedColormap.from_list(
        "rg", [_RED, _PANEL2, _GREEN], N=256
    )
    vmax = max(np.nanmax(np.abs(data)), 1.0) if not np.all(np.isnan(data)) else 1.0

    im = ax.imshow(data, aspect="auto", cmap=cmap,
                   vmin=-vmax, vmax=vmax, origin="upper")

    ax.set_xticks(range(12))
    ax.set_xticklabels(month_names, fontsize=8, color=_DIM)
    ax.set_yticks(range(len(years)))
    ax.set_yticklabels([str(y) for y in years], fontsize=8, color=_DIM)
    ax.tick_params(colors=_DIM)

    for yi in range(len(years)):
        for mi in range(12):
            v = data[yi, mi]
            if not np.isnan(v):
                ax.text(mi, yi, f"{v:+.1f}%",
                        ha="center", va="center",
                        fontsize=6.5, color=_WHITE, fontweight="bold")

    cb = plt.colorbar(im, ax=ax, fraction=0.018, pad=0.02)
    cb.ax.tick_params(labelsize=7, colors=_DIM)
    cb.set_label("Return %", color=_DIM, fontsize=7)


# =============================================================================
# WIN / LOSS DONUT  (Row 3 right)
# =============================================================================

def _plot_winloss(ax, result: BacktestResult):
    """Donut chart + stats for win/loss breakdown."""
    ax.set_facecolor(_PANEL)
    for sp in ax.spines.values():
        sp.set_visible(False)
    ax.tick_params(left=False, bottom=False, labelleft=False, labelbottom=False)
    ax.set_title("Win / Loss", fontsize=9, color=_WHITE)

    wins   = len(result.wins)
    losses = len(result.losses)
    if wins + losses == 0:
        return

    wedge_props = dict(width=0.42, edgecolor=_PANEL, linewidth=2.5)
    ax.pie(
        [wins, losses],
        colors=[_GREEN, _RED],
        startangle=90,
        wedgeprops=wedge_props,
        radius=0.68,
        center=(0.5, 0.58),
    )
    # Centre label
    ax.text(0.5, 0.58, f"{result.win_rate:.1f}%",
            transform=ax.transAxes,
            ha="center", va="center",
            color=_GREEN, fontsize=15, fontweight="bold")
    ax.text(0.5, 0.42, "Win Rate",
            transform=ax.transAxes,
            ha="center", va="center",
            color=_DIM, fontsize=7)

    # Stats rows below donut
    rows = [
        (f"Wins    {wins}", _GREEN),
        (f"Losses  {losses}", _RED),
        (f"Avg Win  ${result.avg_win:,.2f}", _GREEN),
        (f"Avg Loss  -${result.avg_loss:,.2f}", _RED),
        (f"Gross Profit  ${result.gross_profit:,.2f}", _GREEN),
        (f"Gross Loss  -${result.gross_loss:,.2f}", _RED),
    ]
    for i, (txt, col) in enumerate(rows):
        ax.text(0.5, 0.19 - i * 0.048, txt,
                transform=ax.transAxes,
                ha="center", va="center",
                color=col, fontsize=7.2)


# =============================================================================
# UTILITY
# =============================================================================

def _nearest_idx(index: pd.DatetimeIndex, ts) -> int:
    """Return the integer position of the nearest timestamp in index."""
    if ts is None:
        return 0
    ts = pd.Timestamp(ts)
    if ts.tzinfo is None and index.tzinfo is not None:
        ts = ts.tz_localize("UTC")
    elif ts.tzinfo is not None and index.tzinfo is None:
        ts = ts.tz_localize(None)
    pos = index.searchsorted(ts, side="left")
    return min(int(pos), len(index) - 1)
