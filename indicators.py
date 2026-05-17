"""
indicators.py — Technical indicator computation for ParanoidSignals™ 7.9-X

This file is not included in the public repository.

It computes all indicators used by the signal engine:
  - Heikin-Ashi (standard + HTF)
  - EMA, ATR, ADX, CHOP
  - Weekly VWAP with upper/lower bands
  - Donchian Channel

Interface:

    compute_all(df: pd.DataFrame) -> pd.DataFrame
        Input:  raw OHLCV DataFrame from MT5
        Output: same DataFrame with all indicator columns added

    weekly_vwap(df) -> pd.DataFrame
        Returns columns: vwap, vwap_upper, vwap_lower, vwap_upper2
"""

raise NotImplementedError(
    "indicators.py is not included in the public release."
)
