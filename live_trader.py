"""
live_trader.py — Live trading loop connected to MetaTrader 5.

Execution model (matches Pine Script process_orders_on_close = true):
  1. Wait for current bar to close
  2. Fetch the last N confirmed bars from MT5
  3. Compute indicators + signals on confirmed data
  4. If signal → place / reverse order via MT5
  5. In ATR mode → monitor TP/SL hits between bar closes

Signal flow:
  Trailing TPS:
      BUY  → close any Short, open Long
      SELL → close any Long, open Short

  ATR TPS:
      BUY  → close any Short, open Long + set TP1 SL in MT5
      SELL → close any Long, open Short + set TP1 SL in MT5
      TP2/TP3/SL → partial-close managed by live monitoring loop
"""

import json
import logging
import math
import os
import signal as _signal
import threading
import time as _time
from datetime import datetime, timezone

from config import (
    SYMBOL, TIMEFRAME, SETUP_TYPE, TPS_TYPE, SIDEWAYS_FILTER_ENABLED,
    LOT_SIZE, LOT_RISK_PCT, MAGIC, COMMENT,
    TP1_QTY_PCT, TP2_QTY_PCT, MT5_LOGIN,
    SKIP_SUNDAY,
)
from discord_notifier import notify_trade, notify_start, notify_stop, notify_error, notify_heartbeat, notify_restore, notify_sunday_block, notify_sunday_unblock
from trade_csv_logger import append_trade as _csv_append_trade
from session_tracker import SessionTracker
from indicators import compute_all, timeframe_to_minutes
from signals import get_current_signal
from risk_manager import PositionState, compute_tp_sl
from mt5_broker import MT5Broker

log = logging.getLogger("live_trader")

_TRADE_STATE_FILE = "trade_state.json"


def _is_trading_allowed() -> bool:
    """
    Return False during Sunday (Thai time) until Monday 04:00 Thai.
    Thai = UTC+7:
      Block starts : Saturday 17:00 UTC  (= Sunday 00:00 Thai)
      Block ends   : Sunday   21:00 UTC  (= Monday 04:00 Thai)
    """
    now = datetime.now(timezone.utc)
    wd  = now.weekday()   # 0=Mon … 5=Sat … 6=Sun
    h   = now.hour
    if wd == 5 and h >= 17:   # Saturday 17:00+ UTC
        return False
    if wd == 6 and h < 21:    # Sunday UTC before 21:00
        return False
    return True

# Bars used for each normal tick (signal computation)
_TICK_BARS = 4000
# Bars fetched on the FIRST tick only — maximises HA convergence toward TradingView.
# MT5 typically stores ~100,000+ M5 bars; fetching all of them once at startup
# lets the HTF HA seed from years of history (same as TradingView) rather than
# just the last 4000 bars, preventing crossover-timing divergence after restarts.
_WARMUP_BARS = 99999


class LiveTrader:
    """Connects to MT5 and executes trades based on computed signals."""

    def __init__(self,
                 symbol:   str = SYMBOL,
                 timeframe: str = TIMEFRAME,
                 setup_type: str = SETUP_TYPE,
                 tps_type:   str = TPS_TYPE,
                 sideways_filter_enabled: bool = SIDEWAYS_FILTER_ENABLED):
        self.symbol   = symbol
        self.timeframe = timeframe
        self.setup_type = setup_type
        self.tps_type   = tps_type
        self.sideways_filter_enabled = sideways_filter_enabled
        self.broker   = MT5Broker()
        self.pos_state = PositionState()  # used only for ATR mode state tracking
        self._running  = True
        self.tracker   = None  # SessionTracker — assigned in run() after MT5 connects

        self._stop_notified = False   # guard against double Discord notification
        self._first_tick = True            # catch-up logic on first bar after (re)start
        self._sunday_blocked = False       # track Sunday block state for one-time notification
        self._open_balance: float = 0.0   # balance at entry — ใช้คำนวณ P&L ตอนปิด
        self._tp_lock = threading.Lock()   # prevent main loop + monitor thread colliding
        self._last_heartbeat: float = 0.0  # timestamp ของ heartbeat ล่าสุด

        # Trade open info — used by CSV logger when trade closes
        self._open_entry:       float = 0.0
        self._open_lot:         float = 0.0
        self._open_signal_case: str   = ""
        self._open_date:        str   = ""

        # Graceful shutdown on CTRL+C / SIGTERM
        _signal.signal(_signal.SIGINT,  self._handle_stop)
        _signal.signal(_signal.SIGTERM, self._handle_stop)

    def _handle_stop(self, *_):
        log.info("Stop signal received — shutting down after this bar …")
        self._running = False
        # บันทึก trade state ก่อน shutdown เพื่อให้ restart ครั้งต่อไป restore ได้
        try:
            self._save_trade_state()
        except Exception:
            pass
        # Notify Discord immediately so the message is sent before any force-kill
        # can terminate the process before the finally block runs.
        if not self._stop_notified:
            self._stop_notified = True
            try:
                notify_stop("Manual stop")
            except Exception:
                pass

    # ------------------------------------------------------------------
    # PUBLIC ENTRY
    # ------------------------------------------------------------------

    def run(self):
        """Start the live trading loop. Blocks until stopped."""
        log.info("LiveTrader starting  symbol=%s  tf=%s  setup=%s  tps=%s",
                 self.symbol, self.timeframe, self.setup_type, self.tps_type)

        if not self.broker.connect():
            log.critical("Cannot connect to MT5 — exiting.")
            return

        tf_secs = timeframe_to_minutes(self.timeframe) * 60
        log.info("MT5 connected. Entering main loop (bar=%ds) …", tf_secs)

        # ── Restore pos_state จาก trade_state.json ถ้ามี position เปิดค้างอยู่ ──
        self._restore_trade_state()

        acc = self.broker.get_account_info()
        notify_start(balance=acc.get("balance") if acc else None,
                     equity=acc.get("equity") if acc else None)

        self.tracker = SessionTracker(
            symbol          = self.symbol,
            tf              = self.timeframe,
            setup           = self.setup_type,
            tps             = self.tps_type,
            initial_balance = acc.get("balance", 0.0) if acc else 0.0,
            login           = MT5_LOGIN,
        )

        # ── Heartbeat thread (ส่ง Discord ทุก 3 ชั่วโมง) ──────────────────
        hb = threading.Thread(target=self._heartbeat_loop, daemon=True)
        hb.start()

        # ── ATR TP monitor thread (ตรวจ TP ทุก 5 วิ ไม่ต้องรอ bar close) ──
        if self.tps_type == "ATR":
            monitor = threading.Thread(target=self._tp_monitor_loop, daemon=True)
            monitor.start()

        try:
            while self._running:
                self._tick()
                if self._running:
                    self.broker.wait_for_bar_close(tf_secs)
        except Exception as exc:
            log.exception("Unexpected error in live_trader: %s", exc)
            notify_error(str(exc))
        finally:
            log.info("LiveTrader stopped.")
            if not self._stop_notified:
                self._stop_notified = True
                notify_stop()
            if self.tracker:
                fin_acc = self.broker.get_account_info()
                self.tracker.finalize(fin_acc.get("balance", 0.0) if fin_acc else 0.0)
            self.broker.disconnect()

    # ------------------------------------------------------------------
    # PER-BAR LOGIC
    # ------------------------------------------------------------------

    def _tick(self):
        """Execute one trading iteration (one confirmed bar)."""
        # First tick: fetch max history so HA warms up from years of data (≈ TradingView).
        # Subsequent ticks: use _TICK_BARS for speed.
        bars = _WARMUP_BARS if self._first_tick else _TICK_BARS
        df_raw = self.broker.get_rates(self.symbol, self.timeframe, bars)
        if df_raw.empty or len(df_raw) < 50:
            log.warning("Not enough bars fetched (%d). Skipping.", len(df_raw))
            return
        self._first_tick = False

        # Sunday block (Thai time) — skip signal processing, allow TP monitor to keep running
        if SKIP_SUNDAY and not _is_trading_allowed():
            if not self._sunday_blocked:
                self._sunday_blocked = True
                log.info("Sunday block started — no new entries until Mon 04:00 Thai.")
                notify_sunday_block()
            else:
                log.info("[%s] Sunday block active.",
                         datetime.utcnow().strftime("%Y-%m-%d %H:%M"))
            return

        # Sunday unblock — ตลาดกลับมาเปิด
        if SKIP_SUNDAY and self._sunday_blocked:
            self._sunday_blocked = False
            log.info("Sunday block lifted — resuming trading.")
            notify_sunday_unblock()

        # Compute indicators
        df = compute_all(df_raw, self.timeframe, self.setup_type)

        # Get signal on the last confirmed bar
        sig = get_current_signal(df, self.setup_type, self.sideways_filter_enabled)

        buy         = sig["buy"]
        sell        = sig["sell"]
        close_price = sig["close"]
        atr_val     = sig["atr_risk"]

        dc_lower = sig.get("dc_lower", float("nan"))
        dc_upper = sig.get("dc_upper", float("nan"))
        log.info("[%s] close=%.2f  buy=%s  sell=%s  adx_ok=%s  "
                 "dc_lower=%.2f  dc_upper=%.2f",
                 datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
                 close_price, buy, sell, sig["trend_filter"],
                 dc_lower, dc_upper)

        signal_case = sig.get("signal_case", "")

        if self.tps_type == "Trailing":
            self._execute_trailing(buy, sell, close_price, atr_val,
                                   signal_case=signal_case)
        elif self.tps_type == "ATR":
            self._execute_atr(buy, sell, close_price, atr_val,
                              dc_lower=sig.get("dc_lower", float("nan")),
                              dc_upper=sig.get("dc_upper", float("nan")),
                              signal_case=signal_case)
        # "Options" mode: entry only, no auto-exit

    # ------------------------------------------------------------------
    # TRAILING MODE
    # ------------------------------------------------------------------

    def _execute_trailing(self, buy: bool, sell: bool,
                          close_price: float, atr_val: float,
                          signal_case: str = ""):
        """
        Trailing TPS logic (Pine Script lxTrigger=false):
            if buy:  close Short (reversal) + open Long
            if sell: close Long  (reversal) + open Short
        Positions close only via reversal entry — no signal-based exits.
        """
        current_dir = self.broker.get_position_direction(self.symbol)
        # Trailing mode: estimate sl_dist from ATR
        sl_dist_est = atr_val * 2.5 if not math.isnan(atr_val) and atr_val > 0 else 0.0

        # ── Open new long ──
        if buy and current_dir <= 0:
            lot = self._get_lot(close_price, sl_dist_est)
            ticket = self.broker.buy(
                symbol  = self.symbol,
                lot     = lot,
                comment = f"{COMMENT} long",
            )
            if ticket:
                log.info("Entered LONG  ticket=%s  price=%.5f", ticket, close_price)
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                self._open_balance = bal or 0.0
                self._open_entry       = close_price
                self._open_lot         = lot
                self._open_signal_case = signal_case
                self._open_date        = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
                notify_trade("LONG", "OPEN", close_price, lot, ticket=ticket, atr=atr_val,
                             balance=bal, equity=acc.get("equity") if acc else None)
                self._record_open("LONG", close_price, lot, ticket, bal or 0.0)

        # ── Open new short ──
        elif sell and current_dir >= 0:
            lot = self._get_lot(close_price, sl_dist_est)
            ticket = self.broker.sell(
                symbol  = self.symbol,
                lot     = lot,
                comment = f"{COMMENT} short",
            )
            if ticket:
                log.info("Entered SHORT  ticket=%s  price=%.5f", ticket, close_price)
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                self._open_balance = bal or 0.0
                self._open_entry       = close_price
                self._open_lot         = lot
                self._open_signal_case = signal_case
                self._open_date        = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
                notify_trade("SHORT", "OPEN", close_price, lot, ticket=ticket, atr=atr_val,
                             balance=bal, equity=acc.get("equity") if acc else None)
                self._record_open("SHORT", close_price, lot, ticket, bal or 0.0)

    # ------------------------------------------------------------------
    # ATR / SANI MODE
    # ------------------------------------------------------------------

    def _execute_atr(self, buy: bool, sell: bool,
                     close_price: float, atr_val: float,
                     dc_lower: float = float("nan"),
                     dc_upper: float = float("nan"),
                     signal_case: str = ""):
        """
        PurpleRain TPS logic (lxTrigger=false):
        - SL = Donchian lower/upper
        - TP1 = entry ± slDist × TP1_MULT  (closes 75%)
        - TP2 = entry ± slDist × TP2_MULT  (closes remaining 25%)
        - After TP1: SL moves to breakeven
        """
        # Compute sl_dist from Donchian (PurpleRain strategy)
        sl_dist_buy  = close_price - dc_lower  if not math.isnan(dc_lower)  else float("nan")
        sl_dist_sell = dc_upper - close_price  if not math.isnan(dc_upper)  else float("nan")

        if math.isnan(sl_dist_buy) and math.isnan(sl_dist_sell):
            log.warning("dc_lower/dc_upper NaN — cannot compute Donchian SL. Skipping.")
            return
        if not math.isnan(sl_dist_buy) and sl_dist_buy <= 0:
            log.warning("sl_dist_buy <= 0 (%.5f) — skipping LONG entry.", sl_dist_buy)
            sl_dist_buy = float("nan")
        if not math.isnan(sl_dist_sell) and sl_dist_sell <= 0:
            log.warning("sl_dist_sell <= 0 (%.5f) — skipping SHORT entry.", sl_dist_sell)
            sl_dist_sell = float("nan")

        current_dir = self.broker.get_position_direction(self.symbol)

        if buy and not math.isnan(sl_dist_buy):
            if current_dir == 1:
                log.info("Already LONG — skipping buy signal.")
                return
            levels_buy = compute_tp_sl(close_price, sl_dist_buy, 1)
            if current_dir == -1:
                # ปิด Short แล้วกลับทาง Long
                self.broker.close_all(self.symbol)
                self.pos_state.reset()
                self._delete_trade_state()
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                notify_trade("SHORT", "CLOSE", close_price, 0,
                             reason="signal",
                             pnl=round(bal - self._open_balance, 2) if bal else None,
                             balance=bal, equity=acc.get("equity") if acc else None)
                self._record_close("SHORT", close_price, bal or 0.0, reason="signal")
                current_dir = 0

            lot = self._get_lot(close_price, sl_dist_buy)
            ticket = self.broker.buy(
                symbol  = self.symbol,
                lot     = lot,
                sl      = levels_buy["sl"],
                tp      = 0,
                comment = f"{COMMENT} long",
            )
            if ticket:
                self.pos_state.enter_long(close_price, sl_dist_buy)
                self.pos_state.mt5_ticket = ticket
                self._save_trade_state()
                log.info("LONG  ticket=%s  sl=%.2f  tp1=%.2f  tp2=%.2f  sl_dist=%.2f",
                         ticket, levels_buy["sl"], levels_buy["tp1"],
                         levels_buy["tp2"], sl_dist_buy)
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                self._open_balance = bal or 0.0
                self._open_entry       = close_price
                self._open_lot         = lot
                self._open_signal_case = signal_case
                self._open_date        = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
                notify_trade("LONG", "OPEN", close_price, lot, ticket=ticket,
                             tp1=levels_buy["tp1"], tp2=levels_buy["tp2"],
                             sl=levels_buy["sl"],
                             balance=bal, equity=acc.get("equity") if acc else None)
                self._record_open("LONG", close_price, lot, ticket, bal or 0.0)

        elif sell and not math.isnan(sl_dist_sell):
            if current_dir == -1:
                log.info("Already SHORT — skipping sell signal.")
                return
            levels_sell = compute_tp_sl(close_price, sl_dist_sell, -1)
            if current_dir == 1:
                # ปิด Long แล้วกลับทาง Short
                self.broker.close_all(self.symbol)
                self.pos_state.reset()
                self._delete_trade_state()
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                notify_trade("LONG", "CLOSE", close_price, 0,
                             reason="signal",
                             pnl=round(bal - self._open_balance, 2) if bal else None,
                             balance=bal, equity=acc.get("equity") if acc else None)
                self._record_close("LONG", close_price, bal or 0.0, reason="signal")

            lot = self._get_lot(close_price, sl_dist_sell)
            ticket = self.broker.sell(
                symbol  = self.symbol,
                lot     = lot,
                sl      = levels_sell["sl"],
                tp      = 0,
                comment = f"{COMMENT} short",
            )
            if ticket:
                self.pos_state.enter_short(close_price, sl_dist_sell)
                self.pos_state.mt5_ticket = ticket
                self._save_trade_state()
                log.info("SHORT  ticket=%s  sl=%.2f  tp1=%.2f  tp2=%.2f  sl_dist=%.2f",
                         ticket, levels_sell["sl"], levels_sell["tp1"],
                         levels_sell["tp2"], sl_dist_sell)
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                self._open_balance = bal or 0.0
                self._open_entry       = close_price
                self._open_lot         = lot
                self._open_signal_case = signal_case
                self._open_date        = datetime.utcnow().strftime("%Y-%m-%d %H:%M")
                notify_trade("SHORT", "OPEN", close_price, lot, ticket=ticket,
                             tp1=levels_sell["tp1"], tp2=levels_sell["tp2"],
                             sl=levels_sell["sl"],
                             balance=bal, equity=acc.get("equity") if acc else None)
                self._record_open("SHORT", close_price, lot, ticket, bal or 0.0)

        # TP check ที่ bar close (monitor thread จะจัดการระหว่าง bar อยู่แล้ว)
        with self._tp_lock:
            self._check_atr_partial_close()

    _HEARTBEAT_INTERVAL = 3 * 3600  # 3 ชั่วโมง

    def _heartbeat_loop(self):
        """Background thread: ส่ง Discord heartbeat ทุก 3 ชั่วโมง"""
        while self._running:
            now = _time.time()
            if now - self._last_heartbeat >= self._HEARTBEAT_INTERVAL:
                try:
                    acc = self.broker.get_account_info()
                    notify_heartbeat(
                        balance=acc.get("balance") if acc else None,
                        equity=acc.get("equity") if acc else None,
                    )
                    self._last_heartbeat = now
                    log.info("Heartbeat sent.")
                except Exception as e:
                    log.warning("Heartbeat error: %s", e)
            _time.sleep(60)  # เช็คทุก 1 นาที

    def _tp_monitor_loop(self):
        """Background thread: ตรวจ ATR TP/SL ทุก 5 วิ (ไม่ต้องรอ bar close)"""
        while self._running:
            try:
                if not self.pos_state.is_flat:
                    with self._tp_lock:
                        self._check_atr_partial_close()
            except Exception as e:
                log.warning("TP monitor error: %s", e)
            _time.sleep(5)

    def _check_atr_partial_close(self):
        """
        PurpleRain TP monitor: TP1 closes 75%, TP2 closes remaining 25%.
        After TP1 → SL moves to breakeven (entry price).

        condition 1.0 / -1.0 → waiting for TP1
        condition 1.1 / -1.1 → TP1 hit (SL at breakeven), waiting for TP2
        """
        if self.pos_state.is_flat:
            return

        positions = self.broker.get_positions(self.symbol)
        if not positions:
            if not self.pos_state.is_flat:
                direction = "LONG" if self.pos_state.is_long else "SHORT"
                log.info("%s SL hit (position closed by MT5)", direction)
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                if bal:
                    notify_trade(direction, "CLOSE", 0, 0, reason="sl",
                                 pnl=round(bal - self._open_balance, 2),
                                 balance=bal, equity=acc.get("equity") if acc else None)
                    self._record_close(direction, 0, bal, reason="sl")
            self.pos_state.reset()
            self._delete_trade_state()
            return

        import MetaTrader5 as mt5
        tick = mt5.symbol_info_tick(self.symbol)
        if tick is None:
            return
        current_price = tick.bid if self.pos_state.is_long else tick.ask

        levels    = self.pos_state.levels
        total_lot = sum(p.volume for p in positions)
        sinfo     = self.broker.get_symbol_info(self.symbol) or {}
        min_lot   = sinfo.get("min_lot", 0.01)

        if self.pos_state.is_long:
            if self.pos_state.condition == 1.0 and current_price >= levels.get("tp1", math.inf):
                close_lot = max(round(total_lot * (TP1_QTY_PCT / 100.0), 2), min_lot)
                for pos in positions:
                    self.broker.close_position(pos.ticket, self.symbol, close_lot)
                _time.sleep(0.3)
                for pos in self.broker.get_positions(self.symbol):
                    self.broker.modify_sl_tp(pos.ticket, sl=self.pos_state.entry_price,
                                             tp=0, symbol=self.symbol)
                self.pos_state.on_tp1_hit()
                self._save_trade_state()
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                notify_trade("LONG", "CLOSE", current_price, close_lot, reason="tp1",
                             pnl=round(bal - self._open_balance, 2) if bal else None,
                             balance=bal, equity=acc.get("equity") if acc else None)
                log.info("LONG TP1 hit — partial close %.2f lots, SL → breakeven", close_lot)

            elif self.pos_state.condition == 1.1 and current_price >= levels.get("tp2", math.inf):
                for pos in positions:
                    self.broker.close_position(pos.ticket, self.symbol)
                self.pos_state.on_tp2_hit()
                self._delete_trade_state()
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                notify_trade("LONG", "CLOSE", current_price, total_lot, reason="tp2",
                             pnl=round(bal - self._open_balance, 2) if bal else None,
                             balance=bal, equity=acc.get("equity") if acc else None)
                self._record_close("LONG", current_price, bal or 0.0, reason="tp2")
                log.info("LONG TP2 hit — position fully closed.")

        elif self.pos_state.is_short:
            if self.pos_state.condition == -1.0 and current_price <= levels.get("tp1", -math.inf):
                close_lot = max(round(total_lot * (TP1_QTY_PCT / 100.0), 2), min_lot)
                for pos in positions:
                    self.broker.close_position(pos.ticket, self.symbol, close_lot)
                _time.sleep(0.3)
                for pos in self.broker.get_positions(self.symbol):
                    self.broker.modify_sl_tp(pos.ticket, sl=self.pos_state.entry_price,
                                             tp=0, symbol=self.symbol)
                self.pos_state.on_tp1_hit()
                self._save_trade_state()
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                notify_trade("SHORT", "CLOSE", current_price, close_lot, reason="tp1",
                             pnl=round(bal - self._open_balance, 2) if bal else None,
                             balance=bal, equity=acc.get("equity") if acc else None)
                log.info("SHORT TP1 hit — partial close %.2f lots, SL → breakeven", close_lot)

            elif self.pos_state.condition == -1.1 and current_price <= levels.get("tp2", -math.inf):
                for pos in positions:
                    self.broker.close_position(pos.ticket, self.symbol)
                self.pos_state.on_tp2_hit()
                self._delete_trade_state()
                acc = self.broker.get_account_info()
                bal = acc.get("balance") if acc else None
                notify_trade("SHORT", "CLOSE", current_price, total_lot, reason="tp2",
                             pnl=round(bal - self._open_balance, 2) if bal else None,
                             balance=bal, equity=acc.get("equity") if acc else None)
                self._record_close("SHORT", current_price, bal or 0.0, reason="tp2")
                log.info("SHORT TP2 hit — position fully closed.")

    # ------------------------------------------------------------------
    # TRADE STATE PERSISTENCE  (restore pos_state หลัง restart)
    # ------------------------------------------------------------------

    def _save_trade_state(self):
        """บันทึก pos_state ลงดิสก์ เพื่อ restore หลัง restart"""
        if self.pos_state.is_flat:
            self._delete_trade_state()
            return
        state = {
            "symbol":        self.symbol,
            "direction":     self.pos_state.direction,
            "entry_price":   self.pos_state.entry_price,
            "entry_sl_dist": self.pos_state.entry_sl_dist,
            "condition":     self.pos_state.condition,
            "ticket":        self.pos_state.mt5_ticket,
        }
        try:
            with open(_TRADE_STATE_FILE, "w") as f:
                json.dump(state, f)
        except Exception as exc:
            log.warning("Cannot save trade state: %s", exc)

    def _delete_trade_state(self):
        """ลบไฟล์ trade state (position ปิดแล้ว)"""
        try:
            os.remove(_TRADE_STATE_FILE)
        except FileNotFoundError:
            pass

    def _restore_trade_state(self):
        """ตอน startup: โหลด pos_state กลับจากดิสก์ถ้า MT5 ยังมี position เปิดอยู่"""
        if not os.path.exists(_TRADE_STATE_FILE):
            # Fallback: ไม่มีไฟล์ แต่ MT5 อาจมี position (crash / force-kill)
            self._restore_from_mt5_fallback()
            return

        try:
            with open(_TRADE_STATE_FILE) as f:
                state = json.load(f)
        except Exception as exc:
            log.warning("Cannot read trade state file: %s — ignoring.", exc)
            self._delete_trade_state()
            return

        # ตรวจว่า symbol ตรงกัน
        if state.get("symbol") != self.symbol:
            self._delete_trade_state()
            return

        # ตรวจว่า MT5 ยังมี position ticket นั้นอยู่
        positions = self.broker.get_positions(self.symbol)
        if not positions:
            log.info("Restore: ไม่มี open position ใน MT5 — ล้าง trade state.")
            self._delete_trade_state()
            return

        ticket = state.get("ticket")
        pos = next((p for p in positions if p.ticket == ticket), None)
        if pos is None:
            log.info("Restore: ticket %s ไม่พบใน MT5 — ล้าง trade state.", ticket)
            self._delete_trade_state()
            return

        direction     = state["direction"]
        entry_price   = state["entry_price"]
        # Support both new (entry_sl_dist) and legacy (entry_atr) state files
        entry_sl_dist = state.get("entry_sl_dist") or state.get("entry_atr", 0.0)
        condition     = state["condition"]

        # Infer condition from current MT5 SL:
        # If SL is near entry price → TP1 was already hit (SL moved to breakeven)
        current_sl = pos.sl if pos.sl else 0.0
        if direction == 1 and current_sl > 0:
            if abs(current_sl - entry_price) < 1.0:
                condition = max(condition, 1.1)   # SL ≈ entry → TP1 hit
        elif direction == -1 and current_sl > 0:
            if abs(current_sl - entry_price) < 1.0:
                condition = max(abs(condition), 1.1) * -1

        self.pos_state.direction     = direction
        self.pos_state.entry_price   = entry_price
        self.pos_state.entry_sl_dist = entry_sl_dist
        self.pos_state.condition     = condition
        self.pos_state.mt5_ticket    = ticket
        self.pos_state.levels        = compute_tp_sl(entry_price, entry_sl_dist, direction)

        log.info("Restored pos_state: %s entry=%.2f  sl_dist=%.5f  condition=%.1f  ticket=%s",
                 "LONG" if direction == 1 else "SHORT",
                 entry_price, entry_sl_dist, condition, ticket)

        levels = self.pos_state.levels
        try:
            notify_restore(
                direction  = "LONG" if direction == 1 else "SHORT",
                entry_price= entry_price,
                condition  = condition,
                ticket     = ticket,
                tp1        = levels.get("tp1"),
                tp2        = levels.get("tp2"),
                sl         = levels.get("sl"),
                source     = "file",
            )
        except Exception:
            pass

        # Re-apply SL ที่ถูกต้องใน MT5 (กรณี modify_sl_tp ล้มเหลวก่อน restart)
        if abs(condition) == 1.1:
            new_sl = entry_price   # breakeven after TP1
            log.info("Restore: re-applying SL to breakeven %.2f", new_sl)
            for p in positions:
                ok = self.broker.modify_sl_tp(p.ticket, sl=new_sl, tp=p.tp, symbol=self.symbol)
                if ok:
                    log.info("Restore: SL moved to breakeven %.2f on ticket %s", new_sl, p.ticket)

    def _restore_from_mt5_fallback(self):
        """Fallback: reconstruct pos_state จาก MT5 position โดยตรง
        เมื่อไม่มี trade_state.json (crash / force-kill / version เก่า)
        direction  : pos.type == 0 → BUY (1), pos.type == 1 → SELL (-1)
        sl_dist    : abs(entry - current_sl) — ถ้า SL ≈ entry แสดงว่า TP1 hit แล้ว
        """
        positions = self.broker.get_positions(self.symbol)
        if not positions:
            return

        pos = positions[0]
        direction   = 1 if pos.type == 0 else -1   # 0=BUY, 1=SELL
        entry_price = pos.price_open
        current_sl  = pos.sl if pos.sl else 0.0

        if current_sl > 0:
            sl_dist = abs(entry_price - current_sl)
        else:
            log.warning("Restore fallback: position has no SL — sl_dist=0, TP2 tracking disabled")
            sl_dist = 0.0

        # ตรวจ condition: ถ้า SL ≈ entry → TP1 hit แล้ว
        condition = float(direction)          # 1.0 or -1.0
        if current_sl > 0 and abs(current_sl - entry_price) < 1.0:
            condition = 1.1 if direction == 1 else -1.1
            log.warning("Restore fallback: SL ≈ entry (TP1 hit) — sl_dist inaccurate, TP2 may not trigger")

        self.pos_state.direction     = direction
        self.pos_state.entry_price   = entry_price
        self.pos_state.entry_sl_dist = sl_dist
        self.pos_state.condition     = condition
        self.pos_state.mt5_ticket    = pos.ticket
        self.pos_state.levels        = compute_tp_sl(entry_price, sl_dist, direction)

        # บันทึกลงดิสก์ทันที เพื่อให้ restart ครั้งถัดไปใช้ไฟล์แทน
        self._save_trade_state()

        log.info("Restored pos_state from MT5 (fallback): %s entry=%.2f  sl_dist=%.5f  condition=%.1f  ticket=%s",
                 "LONG" if direction == 1 else "SHORT",
                 entry_price, sl_dist, condition, pos.ticket)

        levels = self.pos_state.levels
        try:
            notify_restore(
                direction  = "LONG" if direction == 1 else "SHORT",
                entry_price= entry_price,
                condition  = condition,
                ticket     = pos.ticket,
                tp1        = levels.get("tp1"),
                tp2        = levels.get("tp2"),
                sl         = levels.get("sl"),
                source     = "MT5",
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # SESSION TRACKER HELPERS
    # ------------------------------------------------------------------

    def _record_open(self, direction: str, price: float, lot: float,
                     ticket: int, balance: float):
        """Safe wrapper — records a trade open in the session tracker."""
        if self.tracker:
            self.tracker.on_open(direction, price, lot, ticket, balance)

    def _record_close(self, direction: str, price: float, balance: float,
                      reason: str = ""):
        """Safe wrapper — records a trade close in the session tracker and CSV."""
        if self.tracker:
            self.tracker.on_close(direction, price, balance)
        profit = round(balance - self._open_balance, 2) if self._open_balance else 0.0
        _csv_append_trade(
            open_date    = self._open_date,
            close_date   = datetime.utcnow().strftime("%Y-%m-%d %H:%M"),
            direction    = direction,
            entry_price  = self._open_entry,
            exit_price   = price,
            lot          = self._open_lot,
            profit       = profit,
            balance      = balance,
            signal_case  = self._open_signal_case,
            close_reason = reason,
        )

    # ------------------------------------------------------------------
    # LOT SIZE
    # ------------------------------------------------------------------

    def _get_lot(self, price: float, sl_dist: float) -> float:
        """Compute lot size: risk LOT_RISK_PCT % of balance over sl_dist."""
        if LOT_SIZE > 0:
            return LOT_SIZE

        if not math.isnan(sl_dist) and sl_dist > 0:
            return self.broker.calculate_lot_size(self.symbol, sl_dist, LOT_RISK_PCT)

        # Fallback: 0.5% of current balance / notional
        if price > 0:
            acc   = self.broker.get_account_info()
            sinfo = self.broker.get_symbol_info(self.symbol)
            if acc and sinfo:
                bal      = acc["balance"]
                contract = sinfo["trade_contract_size"]
                min_lot  = sinfo["min_lot"]
                lot_step = sinfo["lot_step"]
                notional = contract * price
                lot = (bal * 0.005) / notional if notional > 0 else min_lot
                lot = round(lot / lot_step) * lot_step
                return max(min_lot, min(sinfo["max_lot"], lot))
        return 0.01

