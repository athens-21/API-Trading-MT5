"""
mt5_broker.py — MetaTrader 5 connection and order management.

Provides:
  - MT5Broker class for connecting, fetching data, placing/closing orders
  - All MT5 interaction is isolated here so the rest of the bot never
    imports MetaTrader5 directly.
"""

import logging
import math
import time as _time
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import numpy as np

log = logging.getLogger("mt5_broker")

try:
    import MetaTrader5 as mt5
    _MT5_AVAILABLE = True
except ImportError:
    _MT5_AVAILABLE = False
    log.warning("MetaTrader5 package not installed. "
                "Install it with: pip install MetaTrader5")

from config import (
    MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH,
    SYMBOL, TIMEFRAME, MAGIC, COMMENT,
    LOT_SIZE, LOT_RISK_PCT,
)

# Map config timeframe strings to MT5 timeframe constants
_TF_MAP = {
    "M1":  1,   "M2":  2,   "M3":  3,   "M4":  4,   "M5":  5,
    "M6":  6,   "M10": 10,  "M12": 12,  "M15": 13,  "M20": 14,
    "M30": 15,  "H1":  16,  "H2":  17,  "H3":  18,  "H4":  19,
    "H6":  20,  "H8":  21,  "H12": 22,  "D1":  23,  "W1":  24,
    "MN1": 25,
}

# Timeframe string → bar duration in minutes
_TF_MINUTES = {
    "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5,
    "M6": 6, "M10": 10, "M12": 12, "M15": 15, "M20": 20,
    "M30": 30, "H1": 60, "H2": 120, "H3": 180, "H4": 240,
    "H6": 360, "H8": 480, "H12": 720, "D1": 1440, "W1": 10080,
    "MN1": 43200,
}


def _mt5_tf(tf_str: str) -> int:
    """Return the integer MT5 timeframe constant for a string like 'M5'."""
    if not _MT5_AVAILABLE:
        return 0
    tf_map_mt5 = {
        "M1":  mt5.TIMEFRAME_M1,
        "M2":  mt5.TIMEFRAME_M2,
        "M3":  mt5.TIMEFRAME_M3,
        "M4":  mt5.TIMEFRAME_M4,
        "M5":  mt5.TIMEFRAME_M5,
        "M6":  mt5.TIMEFRAME_M6,
        "M10": mt5.TIMEFRAME_M10,
        "M12": mt5.TIMEFRAME_M12,
        "M15": mt5.TIMEFRAME_M15,
        "M20": mt5.TIMEFRAME_M20,
        "M30": mt5.TIMEFRAME_M30,
        "H1":  mt5.TIMEFRAME_H1,
        "H2":  mt5.TIMEFRAME_H2,
        "H3":  mt5.TIMEFRAME_H3,
        "H4":  mt5.TIMEFRAME_H4,
        "H6":  mt5.TIMEFRAME_H6,
        "H8":  mt5.TIMEFRAME_H8,
        "H12": mt5.TIMEFRAME_H12,
        "D1":  mt5.TIMEFRAME_D1,
        "W1":  mt5.TIMEFRAME_W1,
        "MN1": mt5.TIMEFRAME_MN1,
    }
    return tf_map_mt5.get(tf_str.upper(), mt5.TIMEFRAME_M5)


class MT5Broker:
    """Thin wrapper around the MetaTrader5 Python package."""

    def __init__(self):
        if not _MT5_AVAILABLE:
            raise RuntimeError(
                "MetaTrader5 package is not installed. "
                "Run: pip install MetaTrader5"
            )
        self._connected = False
        self._login    = MT5_LOGIN
        self._password = MT5_PASSWORD
        self._server   = MT5_SERVER

    # ------------------------------------------------------------------
    # CONNECTION
    # ------------------------------------------------------------------

    def connect(self) -> bool:
        """Initialise the MT5 connection."""
        kwargs = {}
        if MT5_PATH:
            kwargs["path"] = MT5_PATH
        if self._login:
            kwargs["login"]    = self._login
            kwargs["password"] = self._password
            kwargs["server"]   = self._server

        if not mt5.initialize(**kwargs):
            log.error("MT5 initialize() failed: %s", mt5.last_error())
            return False

        log.info("Connected to MT5 — %s  (build %s)",
                 mt5.terminal_info().name,
                 mt5.version()[1])
        self._connected = True
        return True

    def disconnect(self):
        mt5.shutdown()
        self._connected = False
        log.info("MT5 disconnected.")

    def _require_connection(self):
        if not self._connected:
            raise RuntimeError("MT5Broker: not connected. Call connect() first.")

    # ------------------------------------------------------------------
    # DATA FETCHING
    # ------------------------------------------------------------------

    def get_rates(self,
                  symbol: str = SYMBOL,
                  timeframe: str = TIMEFRAME,
                  count: int = 2000) -> pd.DataFrame:
        """
        Fetch `count` bars of OHLCV data for `symbol` on `timeframe`.

        Returns a DataFrame with columns:
            open, high, low, close, tick_volume
        and a UTC DatetimeIndex.
        """
        self._require_connection()
        tf_const = _mt5_tf(timeframe)
        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, count)
        if rates is None or len(rates) == 0:
            log.error("get_rates failed for %s %s: %s",
                      symbol, timeframe, mt5.last_error())
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("time", inplace=True)
        df = df[["open", "high", "low", "close", "tick_volume"]]
        # Drop any bar that is still forming: keep only bars whose close time
        # (open_time + bar_duration) is already in the past.
        # This correctly handles both cases: MT5 already opened the new bar
        # (its forming bar gets dropped), and MT5 hasn't opened a new bar yet
        # (nothing gets dropped, all returned bars are complete).
        tf_minutes = _TF_MINUTES.get(timeframe.upper(), 5)
        bar_duration = pd.Timedelta(minutes=tf_minutes)
        now_utc = pd.Timestamp.now(tz="UTC")
        df = df[df.index + bar_duration <= now_utc]
        return df

    def get_rates_range(self,
                        symbol: str = SYMBOL,
                        timeframe: str = TIMEFRAME,
                        date_from: datetime = None,
                        date_to: datetime = None) -> pd.DataFrame:
        """
        Fetch all available bars then slice to [date_from, date_to].
        Uses copy_rates_from_pos(max=99999) which is the most reliable
        method across all MT5 terminal versions.
        """
        self._require_connection()
        tf_const = _mt5_tf(timeframe)

        # Fetch maximum available history
        rates = mt5.copy_rates_from_pos(symbol, tf_const, 0, 99999)
        if rates is None or len(rates) == 0:
            log.error("get_rates_range: copy_rates_from_pos failed for %s %s: %s",
                      symbol, timeframe, mt5.last_error())
            return pd.DataFrame()

        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df.set_index("time", inplace=True)
        df = df[["open", "high", "low", "close", "tick_volume"]]
        # Drop forming bar using time-based filter (same logic as get_rates)
        tf_minutes = _TF_MINUTES.get(timeframe.upper(), 5)
        bar_duration = pd.Timedelta(minutes=tf_minutes)
        now_utc = pd.Timestamp.now(tz="UTC")
        df = df[df.index + bar_duration <= now_utc]

        # Filter to requested date range
        if date_from is not None:
            if date_from.tzinfo is None:
                date_from = date_from.replace(tzinfo=timezone.utc)
            df = df[df.index >= date_from]
        if date_to is not None:
            if date_to.tzinfo is None:
                date_to = date_to.replace(tzinfo=timezone.utc)
            df = df[df.index <= date_to]

        log.info("get_rates_range: %d bars  (%s → %s)",
                 len(df),
                 df.index.min() if len(df) else "N/A",
                 df.index.max() if len(df) else "N/A")
        return df

    def get_symbol_info(self, symbol: str = SYMBOL) -> dict:
        """Return key symbol info (digits, point, min_lot, etc.)."""
        self._require_connection()
        info = mt5.symbol_info(symbol)
        if info is None:
            log.error("symbol_info failed for %s: %s", symbol, mt5.last_error())
            return {}
        return {
            "digits":    info.digits,
            "point":     info.point,
            "min_lot":   info.volume_min,
            "max_lot":   info.volume_max,
            "lot_step":  info.volume_step,
            "trade_contract_size": info.trade_contract_size,
        }

    def get_account_info(self) -> dict:
        """Return balance, equity, and margin info."""
        self._require_connection()
        acc = mt5.account_info()
        if acc is None:
            return {}
        return {
            "balance": acc.balance,
            "equity":  acc.equity,
            "margin":  acc.margin,
            "free_margin": acc.margin_free,
            "currency": acc.currency,
        }

    # ------------------------------------------------------------------
    # LOT SIZE CALCULATION
    # ------------------------------------------------------------------

    def calculate_lot_size(self,
                            symbol: str,
                            sl_distance: float,
                            risk_pct: float = LOT_RISK_PCT,
                            balance_override: float = None) -> float:
        """
        Calculate lot size based on a percentage risk of account balance.
        If config.LOT_SIZE > 0 uses fixed lot size instead.
        balance_override: ถ้าระบุจะใช้แทน account balance จริง (สำหรับ ref_balance logic)
        """
        if LOT_SIZE > 0:
            return LOT_SIZE

        acc   = self.get_account_info()
        sinfo = self.get_symbol_info(symbol)
        if not acc or not sinfo:
            return 0.01

        balance   = balance_override if balance_override is not None else acc["balance"]
        risk_amt  = balance * (risk_pct / 100.0)
        contract  = sinfo["trade_contract_size"]
        point     = sinfo["point"]
        digits    = sinfo["digits"]

        # SL distance in price units → pips → value per lot
        sl_pips      = sl_distance / point
        pip_value    = (point * contract) / 1.0   # simplified; for cross pairs adjust

        if pip_value == 0 or sl_pips == 0:
            return sinfo["min_lot"]

        lot = risk_amt / (sl_pips * pip_value)
        lot = math.floor(lot / sinfo["lot_step"]) * sinfo["lot_step"]
        lot = max(sinfo["min_lot"], min(sinfo["max_lot"], lot))
        return lot

    def _round_lot(self, lot: float, symbol: str) -> float:
        sinfo = self.get_symbol_info(symbol)
        step  = sinfo.get("lot_step", 0.01)
        lot   = math.floor(lot / step) * step
        lot   = max(sinfo.get("min_lot", 0.01),
                    min(sinfo.get("max_lot", 100.0), lot))
        return round(lot, 2)

    # ------------------------------------------------------------------
    # ORDER PLACEMENT
    # ------------------------------------------------------------------

    def _send_order(self, request: dict) -> Optional[int]:
        """Send a trade request and return ticket on success, None on failure."""
        result = mt5.order_send(request)
        if result is None:
            log.error("order_send returned None. Last error: %s", mt5.last_error())
            return None
        if result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error("order_send failed  retcode=%s  comment=%s",
                      result.retcode, result.comment)
            return None
        log.info("Order placed  ticket=%s  price=%s  lot=%s",
                 result.order, result.price, result.volume)
        return result.order

    def buy(self,
            symbol: str = SYMBOL,
            lot: float  = LOT_SIZE,
            sl: float   = 0.0,
            tp: float   = 0.0,
            comment: str = COMMENT) -> Optional[int]:
        """Open a market buy (long) order."""
        self._require_connection()
        sinfo  = self.get_symbol_info(symbol)
        tick   = mt5.symbol_info_tick(symbol)
        if tick is None:
            log.error("Cannot get tick for %s", symbol)
            return None

        price  = tick.ask
        digits = sinfo.get("digits", 5)
        lot    = self._round_lot(lot, symbol)

        req = {
            "action":   mt5.TRADE_ACTION_DEAL,
            "symbol":   symbol,
            "volume":   lot,
            "type":     mt5.ORDER_TYPE_BUY,
            "price":    price,
            "sl":       round(sl, digits) if sl else 0.0,
            "tp":       round(tp, digits) if tp else 0.0,
            "deviation": 20,
            "magic":    MAGIC,
            "comment":  comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        return self._send_order(req)

    def sell(self,
             symbol: str = SYMBOL,
             lot: float  = LOT_SIZE,
             sl: float   = 0.0,
             tp: float   = 0.0,
             comment: str = COMMENT) -> Optional[int]:
        """Open a market sell (short) order."""
        self._require_connection()
        sinfo  = self.get_symbol_info(symbol)
        tick   = mt5.symbol_info_tick(symbol)
        if tick is None:
            return None

        price  = tick.bid
        digits = sinfo.get("digits", 5)
        lot    = self._round_lot(lot, symbol)

        req = {
            "action":   mt5.TRADE_ACTION_DEAL,
            "symbol":   symbol,
            "volume":   lot,
            "type":     mt5.ORDER_TYPE_SELL,
            "price":    price,
            "sl":       round(sl, digits) if sl else 0.0,
            "tp":       round(tp, digits) if tp else 0.0,
            "deviation": 20,
            "magic":    MAGIC,
            "comment":  comment,
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        return self._send_order(req)

    # ------------------------------------------------------------------
    # POSITION MANAGEMENT
    # ------------------------------------------------------------------

    def get_positions(self, symbol: str = SYMBOL) -> list:
        """Return all open positions for the symbol with this bot's magic."""
        self._require_connection()
        positions = mt5.positions_get(symbol=symbol)
        if positions is None:
            return []
        return [p for p in positions if p.magic == MAGIC]

    def get_position_direction(self, symbol: str = SYMBOL) -> int:
        """Return +1 if net long, -1 if net short, 0 if flat."""
        positions = self.get_positions(symbol)
        net = 0
        for p in positions:
            if p.type == mt5.POSITION_TYPE_BUY:
                net += p.volume
            else:
                net -= p.volume
        if net > 0:
            return 1
        elif net < 0:
            return -1
        return 0

    def close_position(self, ticket: int, symbol: str = SYMBOL,
                       lot: Optional[float] = None) -> bool:
        """
        Close a position by ticket.
        If `lot` is specified, do a partial close.
        """
        self._require_connection()
        positions = mt5.positions_get(ticket=ticket)
        if not positions:
            log.warning("close_position: ticket %s not found.", ticket)
            return False

        pos    = positions[0]
        sinfo  = self.get_symbol_info(symbol)
        tick   = mt5.symbol_info_tick(symbol)
        digits = sinfo.get("digits", 5)

        close_lot = lot if lot else pos.volume
        close_lot = self._round_lot(close_lot, symbol)

        if pos.type == mt5.POSITION_TYPE_BUY:
            order_type = mt5.ORDER_TYPE_SELL
            price      = tick.bid
        else:
            order_type = mt5.ORDER_TYPE_BUY
            price      = tick.ask

        req = {
            "action":    mt5.TRADE_ACTION_DEAL,
            "symbol":    symbol,
            "volume":    close_lot,
            "type":      order_type,
            "position":  ticket,
            "price":     price,
            "deviation": 20,
            "magic":     MAGIC,
            "comment":   "close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(req)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            log.error("close_position failed  ticket=%s  retcode=%s",
                      ticket, result.retcode if result else "None")
            return False
        log.info("Position closed  ticket=%s  lot=%s", ticket, close_lot)
        return True

    def close_all(self, symbol: str = SYMBOL) -> int:
        """Close all open positions for this symbol/magic. Returns count closed."""
        closed = 0
        for pos in self.get_positions(symbol):
            if self.close_position(pos.ticket, symbol):
                closed += 1
        return closed

    def modify_sl_tp(self, ticket: int, sl: float = 0.0,
                      tp: float = 0.0, symbol: str = SYMBOL) -> bool:
        """Modify SL/TP of an open position. Retries 3 times on failure."""
        self._require_connection()
        sinfo  = self.get_symbol_info(symbol)
        digits = sinfo.get("digits", 5)
        req = {
            "action":   mt5.TRADE_ACTION_SLTP,
            "symbol":   symbol,
            "position": ticket,
            "sl":       round(sl, digits),
            "magic":    MAGIC,
        }
        # Exness rejects tp=0 — only include tp if it has a real value
        if tp and tp != 0.0:
            req["tp"] = round(tp, digits)
        for attempt in range(3):
            result = mt5.order_send(req)
            if result is None:
                err = mt5.last_error()
                log.error("modify_sl_tp failed  attempt=%d  ticket=%s  retcode=None  last_error=%s",
                          attempt + 1, ticket, err)
                if attempt < 2:
                    _time.sleep(0.5)
                continue
            # 10025 = TRADE_RETCODE_NO_CHANGES — SL/TP already at requested value
            if result.retcode in (mt5.TRADE_RETCODE_DONE, 10025):
                if result.retcode == 10025:
                    log.info("modify_sl_tp ticket=%s — already set (no changes needed)", ticket)
                return True
            err = mt5.last_error()
            log.error("modify_sl_tp failed  attempt=%d  ticket=%s  retcode=%s  last_error=%s",
                      attempt + 1, ticket, result.retcode, err)
            if attempt < 2:
                _time.sleep(0.5)
        return False

    # ------------------------------------------------------------------
    # UTILITY
    # ------------------------------------------------------------------

    def wait_for_bar_close(self, tf_seconds: int):
        """
        Sleep until the current bar on `tf_seconds`-second timeframe closes.
        Used by the live trading loop.
        """
        now    = _time.time()
        offset = now % tf_seconds
        wait   = tf_seconds - offset + 1   # +1 second buffer
        log.debug("Waiting %.1f s for bar close …", wait)
        _time.sleep(wait)
