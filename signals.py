"""
signals.py — Signal computation for ParanoidSignals™ 7.9-X

This file is not included in the public repository.
It contains the proprietary PurpleRain strategy logic.

To use this bot, implement the following interface:

    get_current_signal(df: pd.DataFrame) -> dict
        Returns: {"signal": "BUY" | "SELL" | None, "case": str, "sl_dist": float}

    compute_purplerain_signals(df: pd.DataFrame) -> pd.DataFrame
        Adds columns: buy, sell, signal_case to the DataFrame

See README.md for architecture overview.
"""

# Signal implementation is proprietary and not distributed publicly.
raise NotImplementedError(
    "signals.py is not included in the public release. "
    "See README.md for the signal interface specification."
)
