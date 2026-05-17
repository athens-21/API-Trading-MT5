"""
risk_manager.py — Donchian-based TP/SL level calculator and order state machine.

PurpleRain Strategy TP/SL logic (mirrors pine.text):
  SL  = Donchian lower (long) / upper (short)   → slDist = close − dcLower (long)
  TP1 = entry ± slDist × TP1_MULT              → closes TP1_QTY_PCT (75%) of position
  TP2 = entry ± slDist × TP2_MULT              → closes remaining position
  After TP1 hit: SL moves to entry (breakeven)

Condition state machine:
  condition == 0.0  → flat
  condition == 1.0  → Long active, waiting for TP1
  condition == 1.1  → Long TP1 hit (SL at breakeven), waiting for TP2
  condition == 1.2  → Long fully closed

  condition == -1.0 → Short active, waiting for TP1
  condition == -1.1 → Short TP1 hit (SL at breakeven), waiting for TP2
  condition == -1.2 → Short fully closed
"""

import math
from dataclasses import dataclass, field
from typing import Optional

from config import (
    TP1_QTY_PCT, TP2_QTY_PCT,
    TP1_MULT, TP2_MULT,
)


# ---------------------------------------------------------------------------
# TP/SL level computation
# ---------------------------------------------------------------------------

def compute_tp_sl(entry_price: float, sl_dist: float, direction: int) -> dict:
    """
    Compute TP1, TP2 and SL price levels from entry price and Donchian SL distance.

    Pine Script:
        slDist = close − dcLower            (long)  / dcUpper − close  (short)
        SL     = entry − slDist             (long)  / entry + slDist   (short)
        TP1    = entry + slDist × TP1_MULT  (long)  / entry − slDist × TP1_MULT
        TP2    = entry + slDist × TP2_MULT  (long)  / entry − slDist × TP2_MULT

    Parameters
    ----------
    entry_price : float  — entry price (close of signal bar)
    sl_dist     : float  — distance from entry to SL (always positive)
    direction   : int    — +1 long, -1 short

    Returns dict with keys: tp1, tp2, sl
    """
    tp1_dist = sl_dist * TP1_MULT
    tp2_dist = sl_dist * TP2_MULT

    if direction == 1:
        return {
            "tp1": entry_price + tp1_dist,
            "tp2": entry_price + tp2_dist,
            "sl":  entry_price - sl_dist,
        }
    else:
        return {
            "tp1": entry_price - tp1_dist,
            "tp2": entry_price - tp2_dist,
            "sl":  entry_price + sl_dist,
        }


# ---------------------------------------------------------------------------
# State machine for live ATR-mode tracking
# ---------------------------------------------------------------------------

@dataclass
class PositionState:
    """
    Tracks the current position state for PurpleRain strategy.
    """
    condition:     float         = 0.0      # 0=flat, ±1.0, ±1.1, ±1.2
    direction:     int           = 0        # +1 long, -1 short, 0 flat
    entry_price:   float         = 0.0
    entry_sl_dist: float         = 0.0      # slDist at entry (Donchian distance)
    levels:        dict          = field(default_factory=dict)   # tp1/tp2/sl
    qty_remaining: float         = 1.0      # fraction of original position (0–1)
    mt5_ticket:    Optional[int] = None

    def reset(self):
        self.condition     = 0.0
        self.direction     = 0
        self.entry_price   = 0.0
        self.entry_sl_dist = 0.0
        self.levels        = {}
        self.qty_remaining = 1.0
        self.mt5_ticket    = None

    def enter_long(self, entry_price: float, sl_dist: float):
        self.direction     = 1
        self.condition     = 1.0
        self.entry_price   = entry_price
        self.entry_sl_dist = sl_dist
        self.levels        = compute_tp_sl(entry_price, sl_dist, 1)
        self.qty_remaining = 1.0

    def enter_short(self, entry_price: float, sl_dist: float):
        self.direction     = -1
        self.condition     = -1.0
        self.entry_price   = entry_price
        self.entry_sl_dist = sl_dist
        self.levels        = compute_tp_sl(entry_price, sl_dist, -1)
        self.qty_remaining = 1.0

    def on_tp1_hit(self):
        self.condition     = self.direction * 1.1
        self.qty_remaining -= TP1_QTY_PCT / 100.0

    def on_tp2_hit(self):
        self.condition     = self.direction * 1.2
        self.qty_remaining = 0.0

    def on_sl_hit(self):
        self.qty_remaining = 0.0

    @property
    def is_flat(self) -> bool:
        return self.direction == 0 or self.qty_remaining <= 0.0

    @property
    def is_long(self) -> bool:
        return self.direction == 1 and self.qty_remaining > 0.0

    @property
    def is_short(self) -> bool:
        return self.direction == -1 and self.qty_remaining > 0.0

    def check_tp_sl_bar(self, high: float, low: float) -> list:
        """Check if TP or SL was crossed during a bar (for backtest)."""
        events = []
        if self.direction == 1:
            if low <= self.levels["sl"]:
                return ["sl"]
            if self.condition == 1.0 and high >= self.levels["tp1"]:
                events.append("tp1")
            elif self.condition == 1.1 and high >= self.levels["tp2"]:
                events.append("tp2")
        elif self.direction == -1:
            if high >= self.levels["sl"]:
                return ["sl"]
            if self.condition == -1.0 and low <= self.levels["tp1"]:
                events.append("tp1")
            elif self.condition == -1.1 and low <= self.levels["tp2"]:
                events.append("tp2")
        return events
