"""
session_tracker.py — Records every live trade in the current bot session.

A "session" = one continuous run of the bot from start to stop.
Each session is saved to:  sessions/YYYYMMDD_HHMMSS_SYMBOL.json

JSON structure
--------------
{
  "session_id":      "20260322_123510_BTCUSDm",
  "symbol":          "BTCUSDm",
  "tf":              "M5",
  "setup":           "Open/Close",
  "tps":             "Trailing",
  "started_at":      "2026-03-22T12:35:10",
  "ended_at":        "2026-03-22T18:00:00",   <- null while running
  "status":          "running" | "finished",
  "initial_balance": 5000.0,
  "final_balance":   5200.0,                  <- null while running
  "trades": [
    {
      "n":             1,
      "dir":           "LONG" | "SHORT",
      "entry_time":    "2026-03-22T12:40:00",
      "exit_time":     "2026-03-22T14:25:00", <- null while open
      "entry_px":      69000.0,
      "exit_px":       69500.0,               <- null while open
      "lot":           0.01,
      "pnl_abs":       50.0,                  <- null while open
      "balance_after": 5050.0,               <- balance after close
      "status":        "open" | "closed"
    }
  ]
}
"""

import json
import logging
import os
from datetime import datetime

log = logging.getLogger("session_tracker")

SESSIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")


class SessionTracker:
    """Records trades during one live bot run (start → stop)."""

    def __init__(self, symbol: str, tf: str, setup: str, tps: str,
                 initial_balance: float, login: int = None):
        os.makedirs(SESSIONS_DIR, exist_ok=True)
        now = datetime.now()
        self.session_id   = f"{now.strftime('%Y%m%d_%H%M%S')}_{symbol}"
        self.path         = os.path.join(SESSIONS_DIR, f"{self.session_id}.json")
        self._data = {
            "session_id":      self.session_id,
            "login":           login,
            "symbol":          symbol,
            "tf":              tf,
            "setup":           setup,
            "tps":             tps,
            "started_at":      now.strftime("%Y-%m-%dT%H:%M:%S"),
            "ended_at":        None,
            "status":          "running",
            "initial_balance": round(initial_balance, 2),
            "final_balance":   None,
            "trades":          [],
        }
        # Balance at the time the current position was opened (for P&L calc)
        self._open_balance = initial_balance
        self._trade_count  = 0
        self.save()
        log.info("Session started  id=%s  balance=%.2f",
                 self.session_id, initial_balance)

    # ──────────────────────────────────────────────────────────────────────────

    def on_open(self, direction: str, entry_px: float, lot: float,
                ticket: int, balance: float):
        """Record a new trade entry. balance = account balance after the buy/sell executes."""
        self._open_balance = balance
        self._trade_count += 1
        self._data["trades"].append({
            "n":             self._trade_count,
            "dir":           direction,
            "entry_time":    datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S"),
            "exit_time":     None,
            "entry_px":      round(entry_px, 5),
            "exit_px":       None,
            "lot":           lot,
            "pnl_abs":       None,
            "balance_after": None,
            "status":        "open",
        })
        self.save()

    def on_close(self, direction: str, exit_px: float, balance: float):
        """Record a trade exit.
        P&L = balance_after_close − balance_after_open (includes spread & swap).
        Finds the most recent open trade in the given direction.
        """
        for trade in reversed(self._data["trades"]):
            if trade["status"] == "open" and trade["dir"] == direction:
                trade["exit_time"]     = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S")
                trade["exit_px"]       = round(exit_px, 5)
                trade["pnl_abs"]       = round(balance - self._open_balance, 2)
                trade["balance_after"] = round(balance, 2)
                trade["status"]        = "closed"
                self.save()
                log.info("Trade closed  n=%d  dir=%s  pnl=%.2f",
                         trade["n"], direction, trade["pnl_abs"])
                return

    def finalize(self, final_balance: float):
        """Called when the bot stops. Marks session as finished."""
        self._data["ended_at"]      = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self._data["status"]        = "finished"
        self._data["final_balance"] = round(final_balance, 2)
        self.save()
        log.info("Session finished  id=%s  final_balance=%.2f",
                 self.session_id, final_balance)

    def save(self):
        """Persist session data to JSON (atomic write via temp file)."""
        try:
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)
        except Exception as exc:
            log.warning("SessionTracker.save() failed: %s", exc)
