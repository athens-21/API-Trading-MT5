"""
backtest_engine.py — Bar-by-bar backtesting engine.

Simulates the Pine Script ParanoidSignals™ 7.9-X strategy on historical data.
Produces performance metrics that match the Pine Script dashboards:
  - Total Trades, Win Rate
  - Starting / Ending capital
  - Avg Win / Avg Loss
  - Profit Factor
  - Max Run-up, Max Drawdown
  - Monthly P&L table

Supports both TPS modes:
  - Trailing : reverse on signal (the primary strategy)
  - ATR      : 3-level TP + SL
"""

import logging
import math
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional

import numpy as np
import pandas as pd
from tabulate import tabulate
from colorama import Fore, Style, init as colorama_init

colorama_init(autoreset=True)
log = logging.getLogger("backtest")

from config import (
    SYMBOL, TIMEFRAME, SETUP_TYPE, TPS_TYPE, SIDEWAYS_FILTER_ENABLED,
    INITIAL_CAPITAL, COMMISSION_PCT, DEFAULT_QTY_PCT,
    TP1_QTY_PCT, TP2_QTY_PCT,
    FROM_DATE, TO_DATE,
    LOT_RISK_PCT,
)
from indicators import compute_all, timeframe_to_minutes
from signals import compute_signals
from risk_manager import compute_tp_sl


# =============================================================================
# DATA STRUCTURES
# =============================================================================

@dataclass
class Trade:
    entry_time:   datetime
    entry_price:  float
    direction:    int          # +1 long, -1 short
    lot_fraction: float        # fraction of equity used
    exit_time:    Optional[datetime] = None
    exit_price:   float             = 0.0
    exit_reason:  str               = ""   # "signal", "tp1", "tp2", "tp3", "sl"
    pnl_pct:      float             = 0.0  # % P&L on the trade (net of commission)
    pnl_abs:      float             = 0.0  # absolute P&L in $ terms


@dataclass
class BacktestResult:
    trades:          List[Trade] = field(default_factory=list)
    equity_curve:    pd.Series   = field(default_factory=pd.Series)
    initial_capital: float       = INITIAL_CAPITAL

    # --- Summary metrics ---
    @property
    def closed_trades(self) -> List[Trade]:
        return [t for t in self.trades if t.exit_time is not None]

    @property
    def total_trades(self) -> int:
        return len(self.closed_trades)

    @property
    def wins(self) -> List[Trade]:
        return [t for t in self.closed_trades if t.pnl_abs > 0]

    @property
    def losses(self) -> List[Trade]:
        return [t for t in self.closed_trades if t.pnl_abs <= 0]

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return len(self.wins) / self.total_trades * 100.0

    @property
    def gross_profit(self) -> float:
        return sum(t.pnl_abs for t in self.wins)

    @property
    def gross_loss(self) -> float:
        return abs(sum(t.pnl_abs for t in self.losses))

    @property
    def net_profit(self) -> float:
        return sum(t.pnl_abs for t in self.closed_trades)

    @property
    def profit_factor(self) -> float:
        return self.gross_profit / self.gross_loss if self.gross_loss else float("inf")

    @property
    def avg_win(self) -> float:
        return self.gross_profit / len(self.wins) if self.wins else 0.0

    @property
    def avg_loss(self) -> float:
        return self.gross_loss / len(self.losses) if self.losses else 0.0

    @property
    def ending_capital(self) -> float:
        return self.initial_capital + self.net_profit

    @property
    def return_pct(self) -> float:
        return self.net_profit / self.initial_capital * 100.0

    @property
    def max_drawdown_pct(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        peak = self.equity_curve.cummax()
        dd   = (self.equity_curve - peak) / peak
        return abs(dd.min()) * 100.0

    @property
    def max_runup_pct(self) -> float:
        if self.equity_curve.empty:
            return 0.0
        trough = self.equity_curve.cummin()
        ru     = (self.equity_curve - trough) / trough
        return ru.max() * 100.0

    # ------------------------------------------------------------------
    # POSITION-LEVEL AGGREGATION (ATR mode: group TP1+TP2+TP3 per entry)
    # ------------------------------------------------------------------

    @property
    def positions(self) -> List[dict]:
        """
        Aggregate partial-close trades into single position records.

        In ATR mode each entry generates up to 3 Trade records (tp1, tp2, tp3).
        Grouping by (entry_time, entry_price, direction) gives the true per-trade
        view that matches what a trader actually experiences.

        In Trailing mode each Trade is already one complete position.
        """
        if not self.closed_trades:
            return []
        result = []
        i = 0
        ct = self.closed_trades
        while i < len(ct):
            t = ct[i]
            group = [t]
            j = i + 1
            while (j < len(ct)
                   and ct[j].entry_time  == t.entry_time
                   and ct[j].entry_price == t.entry_price
                   and ct[j].direction   == t.direction):
                group.append(ct[j])
                j += 1

            net_pnl = sum(g.pnl_abs for g in group)
            reasons = [g.exit_reason for g in group]
            if "sl" in reasons:
                overall_reason = "sl"
            elif reasons[-1] in ("tp3", "end_of_data"):
                overall_reason = reasons[-1]
            elif reasons[-1] == "tp2":
                overall_reason = "tp2"
            elif reasons[-1] == "tp1":
                overall_reason = "tp1"
            else:
                overall_reason = reasons[-1]

            result.append({
                "entry_time":  t.entry_time,
                "exit_time":   group[-1].exit_time,
                "direction":   t.direction,
                "entry_price": t.entry_price,
                "exit_price":  group[-1].exit_price,
                "exit_reason": overall_reason,
                "pnl_abs":     net_pnl,
                "partials":    len(group),
            })
            i = j
        return result

    @property
    def pos_wins(self) -> List[dict]:
        return [p for p in self.positions if p["pnl_abs"] > 0]

    @property
    def pos_losses(self) -> List[dict]:
        return [p for p in self.positions if p["pnl_abs"] <= 0]

    @property
    def pos_win_rate(self) -> float:
        total = len(self.positions)
        return len(self.pos_wins) / total * 100.0 if total else 0.0

    @property
    def pos_profit_factor(self) -> float:
        gp = sum(p["pnl_abs"] for p in self.pos_wins)
        gl = abs(sum(p["pnl_abs"] for p in self.pos_losses))
        return gp / gl if gl else float("inf")

    @property
    def pos_avg_win(self) -> float:
        w = self.pos_wins
        return sum(p["pnl_abs"] for p in w) / len(w) if w else 0.0

    @property
    def pos_avg_loss(self) -> float:
        l = self.pos_losses
        return abs(sum(p["pnl_abs"] for p in l)) / len(l) if l else 0.0

    def print_report(self):
        """Print a formatted performance report to console."""
        W  = 66
        sep = "─" * W

        # ── Header ──────────────────────────────────────────────────────
        print(f"\n{Fore.CYAN}{'═'*W}")
        print(f"  BACKTEST REPORT  ·  {SYMBOL} {TIMEFRAME}")
        print(f"  Strategy : {SETUP_TYPE}  /  TPS : {TPS_TYPE}")
        print(f"  Filter   : ADX+CHOP {'ON' if SIDEWAYS_FILTER_ENABLED else 'OFF'}")
        if self.closed_trades:
            t0 = self.closed_trades[0].entry_time
            t1 = self.closed_trades[-1].exit_time or self.closed_trades[-1].entry_time
            fmt = "%Y-%m-%d %H:%M"
            t0s = t0.strftime(fmt) if hasattr(t0, "strftime") else str(t0)
            t1s = t1.strftime(fmt) if hasattr(t1, "strftime") else str(t1)
            print(f"  Period   : {t0s}  →  {t1s}")
        print(f"{'═'*W}{Style.RESET_ALL}")

        # ── Summary stats (position-level) ─────────────────────────────
        pos      = self.positions
        n_pos    = len(pos)
        pw_c     = len(self.pos_wins)
        pl_c     = len(self.pos_losses)
        summary = [
            ["Positions",     f"{n_pos}",                "Wins / Losses",  f"{Fore.GREEN}{pw_c}{Style.RESET_ALL} / {Fore.RED}{pl_c}{Style.RESET_ALL}"],
            ["Win Rate",      f"{self.pos_win_rate:.2f}%","Profit Factor",  f"{self.pos_profit_factor:.2f}"],
            ["Starting  $",   f"${self.initial_capital:>10,.2f}",  "Ending  $",  f"${self.ending_capital:>10,.2f}"],
            ["Net Profit",    f"${self.net_profit:>+10,.2f}",       "Return",     f"{self.return_pct:>+.2f}%"],
            ["Avg Win",       f"${self.pos_avg_win:>10,.2f}",       "Avg Loss",   f"-${self.pos_avg_loss:>9,.2f}"],
            ["Gross Profit",  f"${self.gross_profit:>10,.2f}",      "Gross Loss", f"-${self.gross_loss:>9,.2f}"],
            ["Max Run-up",    f"{self.max_runup_pct:.2f}%",         "Max Drawdown", f"-{self.max_drawdown_pct:.2f}%"],
        ]
        if TPS_TYPE == "ATR":
            print(f"  {Fore.YELLOW}Note: ATR mode — {self.total_trades} partial closes → {n_pos} actual positions{Style.RESET_ALL}")
        print(tabulate(summary, tablefmt="plain", colalign=("left","right","left","right")))

        # ── Monthly P&L table ───────────────────────────────────────────
        self._print_monthly_table()

        # ── Detailed trade list ─────────────────────────────────────────
        self._print_trade_list()
        print()

    def _print_monthly_table(self):
        """Print monthly return % table (matches Pine Script monthly dashboard)."""
        monthly = {}
        for t in self.closed_trades:
            if t.exit_time is None:
                continue
            key = (t.exit_time.year, t.exit_time.month)
            monthly[key] = monthly.get(key, 0.0) + t.pnl_abs

        if not monthly:
            return

        years  = sorted({k[0] for k in monthly})
        months = ["Jan","Feb","Mar","Apr","May","Jun",
                  "Jul","Aug","Sep","Oct","Nov","Dec"]
        header = ["Year"] + months + ["Total"]
        rows   = []
        capital = self.initial_capital

        for yr in years:
            row_total = 0.0
            row = [str(yr)]
            for mo in range(1, 13):
                pnl = monthly.get((yr, mo), 0.0)
                row_total += pnl
                pct  = pnl / capital * 100.0
                capital += pnl
                color = Fore.GREEN if pnl >= 0 else Fore.RED
                row.append(f"{color}{pct:+.2f}%{Style.RESET_ALL}")
            yr_pct = row_total / (capital - row_total) * 100.0 if (capital - row_total) else 0
            yr_color = Fore.GREEN if row_total >= 0 else Fore.RED
            row.append(f"{yr_color}{yr_pct:+.2f}%{Style.RESET_ALL}")
            rows.append(row)

        print(f"\n{Fore.CYAN}Monthly P&L %{Style.RESET_ALL}")
        print(tabulate(rows, headers=header, tablefmt="grid"))

    def _print_trade_list(self):
        """Print per-trade detail table."""
        if not self.closed_trades:
            return

        cum = self.initial_capital
        rows = []
        fmt_t = "%Y-%m-%d %H:%M"

        for i, t in enumerate(self.closed_trades, 1):
            direction = f"{Fore.CYAN}LONG {Style.RESET_ALL}" if t.direction == 1 else f"{Fore.MAGENTA}SHORT{Style.RESET_ALL}"
            entry_s   = t.entry_time.strftime(fmt_t) if hasattr(t.entry_time, "strftime") else str(t.entry_time)
            exit_s    = t.exit_time.strftime(fmt_t)  if (t.exit_time and hasattr(t.exit_time, "strftime")) else str(t.exit_time)
            reason    = (t.exit_reason or "")[:10]
            pnl_abs   = t.pnl_abs
            pnl_pct   = t.pnl_pct
            cum       += pnl_abs
            color     = Fore.GREEN if pnl_abs >= 0 else Fore.RED
            rows.append([
                i,
                direction,
                entry_s,
                f"{t.entry_price:.2f}",
                exit_s,
                f"{t.exit_price:.2f}",
                reason,
                f"{color}{pnl_abs:>+9.2f}{Style.RESET_ALL}",
                f"{color}{pnl_pct:>+7.2f}%{Style.RESET_ALL}",
                f"${cum:>10,.2f}",
            ])

        headers = ["#", "Dir", "Entry Time", "Entry $", "Exit Time", "Exit $", "Reason", "P&L $", "P&L %", "Cum Equity"]
        print(f"\n{Fore.CYAN}Trade List  ({len(self.closed_trades)} trades){Style.RESET_ALL}")
        print(tabulate(rows, headers=headers, tablefmt="simple"))

    def export_trades_csv(self, filepath: str):
        """Export trade list to CSV."""
        records = []
        for t in self.closed_trades:
            records.append({
                "entry_time":  t.entry_time,
                "exit_time":   t.exit_time,
                "direction":   "Long" if t.direction == 1 else "Short",
                "entry_price": t.entry_price,
                "exit_price":  t.exit_price,
                "exit_reason": t.exit_reason,
                "pnl_pct":     round(t.pnl_pct, 4),
                "pnl_abs":     round(t.pnl_abs, 4),
            })
        pd.DataFrame(records).to_csv(filepath, index=False)
        log.info("Trades exported to %s", filepath)


# =============================================================================
# BACKTEST ENGINE
# =============================================================================

class BacktestEngine:
    """
    Bar-by-bar backtesting engine that mirrors Pine Script execution.

    Pine Script key behaviors replicated:
    - process_orders_on_close = True  → orders execute at bar close price
    - pyramiding = 0                  → max 1 position at a time
    - default_qty_value = 50          → 50% of equity per trade
    - commission = 0.02%              → applied per trade (entry + exit)
    """

    def __init__(self, df: pd.DataFrame = None):
        """
        Parameters
        ----------
        df : Optional pre-loaded DataFrame. If None, you must call
             run() with data loaded from MT5 or a CSV.
        """
        self._df = df

    def load_from_mt5(self, broker, symbol: str = SYMBOL,
                      timeframe: str = TIMEFRAME, count: int = 5000):
        """Load historical data from an MT5Broker instance."""
        self._df = broker.get_rates(symbol, timeframe, count)
        log.info("Loaded %d bars from MT5 (%s %s)", len(self._df), symbol, timeframe)

    def load_from_mt5_range(self, broker, symbol: str = SYMBOL,
                            timeframe: str = TIMEFRAME,
                            date_from=None, date_to=None):
        """Load historical data from MT5 by date range."""
        self._df = broker.get_rates_range(symbol, timeframe, date_from, date_to)
        log.info("Loaded %d bars from MT5 (%s %s, range %s→%s)",
                 len(self._df), symbol, timeframe, date_from, date_to)

    def load_from_csv(self, filepath: str):
        """
        Load data from CSV. Expects columns: time, open, high, low, close, tick_volume
        time must be parseable as datetime.
        """
        df = pd.read_csv(filepath, parse_dates=["time"])
        df.set_index("time", inplace=True)
        df.index = pd.to_datetime(df.index, utc=True)
        self._df = df
        log.info("Loaded %d bars from CSV %s", len(df), filepath)

    # ------------------------------------------------------------------
    # MAIN ENTRY
    # ------------------------------------------------------------------

    def run(self,
            setup_type: str = SETUP_TYPE,
            tps_type:   str = TPS_TYPE,
            sideways_filter_enabled: bool = SIDEWAYS_FILTER_ENABLED,
            bar_resolution: str = "sl_priority") -> BacktestResult:
        """
        Run the backtest on the loaded data.

        Returns a BacktestResult with all trades and performance metrics.
        """
        if self._df is None or self._df.empty:
            raise ValueError("No data loaded. Call load_from_mt5() or load_from_csv() first.")

        tf_mins = timeframe_to_minutes(TIMEFRAME)
        log.info("Computing indicators …")
        df = compute_all(self._df.copy(), TIMEFRAME, setup_type)

        log.info("Generating signals …")
        df = compute_signals(df, setup_type, sideways_filter_enabled)

        log.info("Running simulation (%d bars) …", len(df))

        if tps_type == "Trailing":
            result = self._simulate_trailing(df, tf_mins)
        elif tps_type == "ATR":
            result = self._simulate_atr(df, tf_mins, bar_resolution)
        else:
            log.warning("TPS type '%s' not fully supported in backtest. "
                        "Falling back to Trailing.", tps_type)
            result = self._simulate_trailing(df, tf_mins)

        log.info("Backtest complete: %d trades, win rate %.1f%%, return %.2f%%",
                 result.total_trades, result.win_rate, result.return_pct)
        return result

    # ------------------------------------------------------------------
    # TRAILING MODE SIMULATION
    # ------------------------------------------------------------------

    def _simulate_trailing(self, df: pd.DataFrame, tf_mins: int) -> BacktestResult:
        """
        Pine Script Trailing TPS mode:
            if buy:  close Short → enter Long
            if sell: close Long  → enter Short
        Always in market after first signal. Reverse on opposite signal.
        """
        result      = BacktestResult()
        capital     = INITIAL_CAPITAL
        equity_list = []
        timestamps  = []

        position    = 0         # +1 long, -1 short, 0 flat
        entry_price = 0.0
        entry_time  = None
        entry_qty   = 0.0      # dollar amount at entry

        for i, (ts, row) in enumerate(df.iterrows()):
            buy  = bool(row.get("buy",  False))
            sell = bool(row.get("sell", False))
            close_price = row["close"]

            # Check for signal to reverse position
            should_enter_long  = buy  and position <= 0
            should_enter_short = sell and position >= 0

            if should_enter_long or should_enter_short:
                # --- Close existing position ---
                if position != 0 and entry_price > 0:
                    direction = position
                    exit_price = close_price
                    pnl_pct = direction * (exit_price - entry_price) / entry_price
                    comm = COMMISSION_PCT / 100.0 * 2
                    net_pnl_pct = pnl_pct - comm
                    pnl_abs = entry_qty * net_pnl_pct

                    trade = Trade(
                        entry_time  = entry_time,
                        entry_price = entry_price,
                        direction   = direction,
                        lot_fraction = DEFAULT_QTY_PCT / 100.0,
                        exit_time   = ts,
                        exit_price  = exit_price,
                        exit_reason = "signal",
                        pnl_pct     = net_pnl_pct * 100.0,
                        pnl_abs     = pnl_abs,
                    )
                    result.trades.append(trade)
                    capital += pnl_abs

                # --- Enter new position ---
                position    = 1 if should_enter_long else -1
                entry_price = close_price
                entry_time  = ts
                entry_qty   = capital * (DEFAULT_QTY_PCT / 100.0)

            equity_list.append(capital)
            timestamps.append(ts)

        # Close any open position at end of data
        if position != 0 and entry_price > 0:
            exit_price  = df["close"].iloc[-1]
            direction   = position
            pnl_pct     = direction * (exit_price - entry_price) / entry_price
            comm        = COMMISSION_PCT / 100.0 * 2
            net_pnl_pct = pnl_pct - comm
            pnl_abs     = entry_qty * net_pnl_pct
            capital    += pnl_abs
            trade = Trade(
                entry_time  = entry_time,
                entry_price = entry_price,
                direction   = direction,
                lot_fraction = DEFAULT_QTY_PCT / 100.0,
                exit_time   = df.index[-1],
                exit_price  = exit_price,
                exit_reason = "end_of_data",
                pnl_pct     = net_pnl_pct * 100.0,
                pnl_abs     = pnl_abs,
            )
            result.trades.append(trade)

        result.equity_curve = pd.Series(equity_list, index=timestamps)
        result.initial_capital = INITIAL_CAPITAL
        return result

    # ------------------------------------------------------------------
    # ATR MODE SIMULATION
    # ------------------------------------------------------------------

    def _simulate_atr(self, df: pd.DataFrame, tf_mins: int,
                       bar_resolution: str = "sl_priority") -> BacktestResult:
        """
        PurpleRain TPS mode (2 TPs, Donchian SL).

        bar_resolution:
          "sl_priority"  : SL always wins when both SL and TP hit same bar
          "random_50_50" : 50/50 random resolution
        """
        result      = BacktestResult()
        capital     = INITIAL_CAPITAL
        equity_list = []
        timestamps  = []

        condition      = 0.0
        direction      = 0
        entry_price    = 0.0
        entry_time     = None
        entry_qty      = 0.0
        tp1 = tp2 = sl = 0.0
        remaining_frac = 1.0

        def _closed_portion(frac: float, exit_price: float, reason: str):
            nonlocal capital
            direction_sign = 1 if direction == 1 else -1
            pnl_pct  = direction_sign * (exit_price - entry_price) / entry_price
            comm     = COMMISSION_PCT / 100.0 * 2
            net_pnl  = pnl_pct - comm
            pnl_abs  = entry_qty * frac * net_pnl
            capital += pnl_abs
            result.trades.append(Trade(
                entry_time   = entry_time,
                entry_price  = entry_price,
                direction    = direction,
                lot_fraction = frac,
                exit_time    = current_ts,
                exit_price   = exit_price,
                exit_reason  = reason,
                pnl_pct      = net_pnl * 100.0,
                pnl_abs      = pnl_abs,
            ))

        import random as _random

        current_ts = df.index[0]

        for i, (ts, row) in enumerate(df.iterrows()):
            current_ts  = ts
            buy         = bool(row.get("buy",  False))
            sell        = bool(row.get("sell", False))
            close_price = row["close"]
            high_price  = row["high"]
            low_price   = row["low"]
            dc_lower    = row.get("dc_lower", float("nan"))
            dc_upper    = row.get("dc_upper", float("nan"))

            # ── Check TP/SL on current bar ──
            if direction == 1 and remaining_frac > 0:
                sl_touched  = low_price  <= sl
                tp1_touched = condition == 1.0 and high_price >= tp1
                tp2_touched = condition == 1.1 and high_price >= tp2
                tp_touched  = tp1_touched or tp2_touched

                if sl_touched and tp_touched:
                    sl_wins = True if bar_resolution == "sl_priority" else (_random.random() < 0.5)
                else:
                    sl_wins = sl_touched

                if sl_wins:
                    sl_reason = "sl" if condition == 1.0 else "be"
                    _closed_portion(remaining_frac, sl, sl_reason)
                    condition = 0.0; direction = 0; remaining_frac = 1.0
                elif tp1_touched:
                    _closed_portion(TP1_QTY_PCT / 100.0, tp1, "tp1")
                    condition = 1.1; remaining_frac -= TP1_QTY_PCT / 100.0
                    sl = entry_price          # SL → breakeven
                elif tp2_touched:
                    _closed_portion(remaining_frac, tp2, "tp2")
                    condition = 1.2; direction = 0; remaining_frac = 0.0

            elif direction == -1 and remaining_frac > 0:
                sl_touched  = high_price >= sl
                tp1_touched = condition == -1.0 and low_price <= tp1
                tp2_touched = condition == -1.1 and low_price <= tp2
                tp_touched  = tp1_touched or tp2_touched

                if sl_touched and tp_touched:
                    sl_wins = True if bar_resolution == "sl_priority" else (_random.random() < 0.5)
                else:
                    sl_wins = sl_touched

                if sl_wins:
                    sl_reason = "sl" if condition == -1.0 else "be"
                    _closed_portion(remaining_frac, sl, sl_reason)
                    condition = 0.0; direction = 0; remaining_frac = 1.0
                elif tp1_touched:
                    _closed_portion(TP1_QTY_PCT / 100.0, tp1, "tp1")
                    condition = -1.1; remaining_frac -= TP1_QTY_PCT / 100.0
                    sl = entry_price          # SL → breakeven
                elif tp2_touched:
                    _closed_portion(remaining_frac, tp2, "tp2")
                    condition = -1.2; direction = 0; remaining_frac = 0.0

            # ── Entry signals — ปิด position เดิมก่อนเสมอ (ทั้ง reversal และ re-entry) ──
            if buy and not math.isnan(dc_lower):
                sl_dist = close_price - dc_lower
                if sl_dist > 0:
                    if direction != 0 and remaining_frac > 0:
                        _closed_portion(remaining_frac, close_price, "signal")
                        remaining_frac = 1.0
                    direction   = 1
                    condition   = 1.0
                    entry_price = close_price
                    entry_time  = ts
                    remaining_frac = 1.0
                    entry_qty   = capital * (DEFAULT_QTY_PCT / 100.0)
                    levels      = compute_tp_sl(close_price, sl_dist, 1)
                    tp1, tp2, sl = levels["tp1"], levels["tp2"], levels["sl"]

            elif sell and not math.isnan(dc_upper):
                sl_dist = dc_upper - close_price
                if sl_dist > 0:
                    if direction != 0 and remaining_frac > 0:
                        _closed_portion(remaining_frac, close_price, "signal")
                        remaining_frac = 1.0
                    direction   = -1
                    condition   = -1.0
                    entry_price = close_price
                    entry_time  = ts
                    remaining_frac = 1.0
                    entry_qty   = capital * (DEFAULT_QTY_PCT / 100.0)
                    levels      = compute_tp_sl(close_price, sl_dist, -1)
                    tp1, tp2, sl = levels["tp1"], levels["tp2"], levels["sl"]

            equity_list.append(capital)
            timestamps.append(ts)

        if direction != 0 and remaining_frac > 0:
            _closed_portion(remaining_frac, df["close"].iloc[-1], "end_of_data")

        result.equity_curve    = pd.Series(equity_list, index=timestamps)
        result.initial_capital = INITIAL_CAPITAL
        return result
