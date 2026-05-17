"""
trade_csv_logger.py — Append one CSV row per completed trade.

Columns: open_date, close_date, direction, entry_price, exit_price,
         lot, profit, balance, signal_case, close_reason
"""

import csv
import logging
import os

log = logging.getLogger("trade_csv_logger")

_CSV_FILE = "trades.csv"
_FIELDS = [
    "open_date", "close_date", "direction",
    "entry_price", "exit_price", "lot",
    "profit", "balance", "signal_case", "close_reason",
]


def append_trade(open_date: str, close_date: str, direction: str,
                 entry_price: float, exit_price: float, lot: float,
                 profit: float, balance: float,
                 signal_case: str = "", close_reason: str = "") -> None:
    file_exists = os.path.exists(_CSV_FILE)
    try:
        with open(_CSV_FILE, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDS)
            if not file_exists:
                writer.writeheader()
            writer.writerow({
                "open_date":    open_date,
                "close_date":   close_date,
                "direction":    direction,
                "entry_price":  entry_price,
                "exit_price":   exit_price,
                "lot":          lot,
                "profit":       profit,
                "balance":      balance,
                "signal_case":  signal_case,
                "close_reason": close_reason,
            })
    except Exception as exc:
        log.warning("CSV write failed: %s", exc)
