"""
indicators.py — All technical indicator calculations.

Precisely mirrors Pine Script logic from ParanoidSignals™ 7.9-X:
  - Heikin Ashi at higher timeframe (HTF)
  - EMA / crossover / crossunder helpers
  - ATR (Standard)
  - RSI
  - Renko (ATR-based)
  - DEMA ATR (visual, included for completeness)
"""

import numpy as np
import pandas as pd
import config as _config
from config import (
    RENKO_ATR_LEN, EMA1_LENGTH, EMA2_LENGTH,
    ATR_LENGTH, PROFIT_FACTOR,
    DEMA_PERIOD, DEMA_ATR_LEN, DEMA_ATR_FACT,
    ADX_LENGTH, ADX_THRESHOLD, CHOP_LENGTH, CHOP_THRESHOLD,
    LEN_SMOOTH, LEN_FAST, LEN_SLOW, BAND_MULT, BAND_MULT2, DC_LENGTH,
)


# =============================================================================
# HELPERS
# =============================================================================

def timeframe_to_minutes(tf: str) -> int:
    """Convert MT5 timeframe string to minutes."""
    map_ = {
        "M1": 1, "M2": 2, "M3": 3, "M4": 4, "M5": 5,
        "M6": 6, "M10": 10, "M12": 12, "M15": 15, "M20": 20,
        "M30": 30, "H1": 60, "H2": 120, "H3": 180, "H4": 240,
        "H6": 360, "H8": 480, "H12": 720, "D1": 1440,
        "W1": 10080, "MN1": 43800,
    }
    return map_[tf.upper()]


def crossover(a: pd.Series, b: pd.Series) -> pd.Series:
    """
    Pine Script ta.crossover(a, b):
    Returns True when a crosses above b (a[1] <= b[1] and a > b).
    """
    return (a > b) & (a.shift(1) <= b.shift(1))


def crossunder(a: pd.Series, b: pd.Series) -> pd.Series:
    """
    Pine Script ta.crossunder(a, b):
    Returns True when a crosses below b (a[1] >= b[1] and a < b).
    """
    return (a < b) & (a.shift(1) >= b.shift(1))


# =============================================================================
# EMA
# =============================================================================

def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential Moving Average — matches Pine Script ta.ema()."""
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    """Simple Moving Average — matches Pine Script ta.sma()."""
    return series.rolling(window=period).mean()


# =============================================================================
# ATR
# =============================================================================

def atr(df: pd.DataFrame, period: int) -> pd.Series:
    """
    Average True Range — matches Pine Script ta.atr().
    TR = max(high-low, |high-close[1]|, |low-close[1]|)
    ATR = RMA(TR, period)  [RMA = EMA with alpha=1/period]
    """
    high  = df["high"]
    low   = df["low"]
    close = df["close"]
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low  - prev_close).abs(),
    ], axis=1).max(axis=1)
    # Pine Script uses RMA (Wilder's smoothing) for ATR
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


# =============================================================================
# RSI
# =============================================================================

def rsi(series: pd.Series, period: int) -> pd.Series:
    """
    RSI — matches Pine Script ta.rsi() using Wilder's smoothing.
    """
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


# =============================================================================
# HEIKIN ASHI
# =============================================================================

def heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """
    Compute Heikin Ashi bars from standard OHLC DataFrame.
    Returns a DataFrame with ha_open, ha_high, ha_low, ha_close.

    Pine Script:
        ha_close = (open + high + low + close) / 4
        ha_open[i] = (ha_open[i-1] + ha_close[i-1]) / 2
        ha_high = max(high, ha_open, ha_close)
        ha_low  = min(low,  ha_open, ha_close)
    """
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0

    ha_open = ha_close.copy()
    # Seed first bar
    ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2.0
    for i in range(1, len(ha_open)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0

    ha_high = pd.concat([df["high"], ha_open, ha_close], axis=1).max(axis=1)
    ha_low  = pd.concat([df["low"],  ha_open, ha_close], axis=1).min(axis=1)

    result = pd.DataFrame({
        "ha_open":  ha_open,
        "ha_high":  ha_high,
        "ha_low":   ha_low,
        "ha_close": ha_close,
    }, index=df.index)
    return result


def resample_ohlcv(df: pd.DataFrame, target_minutes: int) -> pd.DataFrame:
    """
    Resample a minute-level OHLCV DataFrame to target_minutes bars.
    The DataFrame must have a DatetimeIndex.
    """
    rule = f"{target_minutes}min"
    resampled = df.resample(rule, label="left", closed="left", origin="epoch").agg({
        "open":  "first",
        "high":  "max",
        "low":   "min",
        "close": "last",
        "tick_volume": "sum",
    }).dropna(subset=["open", "close"])
    return resampled




def heikin_ashi_htf(df_base: pd.DataFrame, base_tf_minutes: int) -> pd.DataFrame:
    """
    Compute Heikin Ashi on the higher timeframe (base_tf × TF_MULTIPLIER).
    Returns ha_open and ha_close mapped back to the base timeframe index.
    Matches Pine Script request.security(..., lookahead_on) behavior:
    all base-TF bars in an HTF period share that period's final HA values,
    and ta.crossover fires at the first base-TF bar of a new HTF period.

    Parameters
    ----------
    df_base       : OHLCV DataFrame with DatetimeIndex at base timeframe
    base_tf_minutes : minutes of the base timeframe

    Returns
    -------
    DataFrame with columns 'ha_open_htf' and 'ha_close_htf' aligned
    to df_base index.
    """
    htf_minutes = base_tf_minutes * _config.TF_MULTIPLIER
    df_htf = resample_ohlcv(df_base, htf_minutes)
    ha_htf = heikin_ashi(df_htf)

    # Map each base-TF bar to the most recent HTF bar whose open time <= bar time.
    # This matches Pine Script request.security(..., lookahead_on) behavior:
    # all base-TF bars within an HTF period share that period's HA values,
    # so ta.crossover fires exactly at the first base-TF bar of each new HTF period.
    left = pd.DataFrame({"time": df_base.index})
    right = pd.DataFrame({
        "time":              ha_htf.index,
        "ha_open_htf":       ha_htf["ha_open"].values,
        "ha_close_htf":      ha_htf["ha_close"].values,
        # Previous *completed* HTF bar values — used for direction-change detection.
        # Shifting by 1 in the HTF series gives the last fully-closed bar so that
        # the crossover comparison is always between two *different* HTF periods.
        "ha_open_htf_prev":  ha_htf["ha_open"].shift(1).values,
        "ha_close_htf_prev": ha_htf["ha_close"].shift(1).values,
    })
    merged = pd.merge_asof(left, right, on="time", direction="backward")
    merged.index = df_base.index

    return merged[["ha_open_htf", "ha_close_htf",
                   "ha_open_htf_prev", "ha_close_htf_prev"]]


# =============================================================================
# RENKO
# =============================================================================

def build_renko_series(df: pd.DataFrame, base_tf_minutes: int) -> pd.DataFrame:
    """
    Build ATR Renko brick series and map renko_close / renko_open back to
    the base timeframe (forward-fill — matches Pine Script lookahead_on).

    Pine Script:
        param = ticker.renko(symbol, "ATR", atrLen=3)
        renko_close = request.security(param, my_time, close, lookahead_on)
        renko_open  = request.security(param, my_time, open,  lookahead_on)

    Then EMA(2) and EMA(10) are applied to renko_close on the HTF.
    We replicate this by:
      1. Resampling to HTF
      2. Building ATR Renko on HTF data
      3. Mapping back to base TF via forward-fill
    """
    htf_minutes = base_tf_minutes * _config.TF_MULTIPLIER
    df_htf = resample_ohlcv(df, htf_minutes)

    # Box size = ATR(RENKO_ATR_LEN) on HTF
    box_sizes = atr(df_htf, RENKO_ATR_LEN)

    renko_open_list  = []
    renko_close_list = []
    renko_times      = []

    brick_high: float = float("nan")
    brick_low:  float = float("nan")
    direction:  int   = 0  # 1=up, -1=down

    for i, (ts, row) in enumerate(df_htf.iterrows()):
        box = box_sizes.iloc[i]
        if np.isnan(box) or np.isnan(row["close"]):
            continue

        c = row["close"]

        if np.isnan(brick_high):
            # Seed first brick
            brick_high = row["open"] + box
            brick_low  = row["open"]
            direction  = 1

        # Check how many bricks price has moved
        if direction == 1:
            while c >= brick_high + box:
                renko_times.append(ts)
                renko_open_list.append(brick_high)
                brick_high += box
                brick_low  = brick_high - box
                renko_close_list.append(brick_high)
            if c <= brick_low - box:
                direction = -1
                while c <= brick_low - box:
                    renko_times.append(ts)
                    renko_open_list.append(brick_low)
                    brick_low  -= box
                    brick_high  = brick_low + box
                    renko_close_list.append(brick_low)
        else:  # direction == -1
            while c <= brick_low - box:
                renko_times.append(ts)
                renko_open_list.append(brick_low)
                brick_low  -= box
                brick_high  = brick_low + box
                renko_close_list.append(brick_low)
            if c >= brick_high + box:
                direction = 1
                while c >= brick_high + box:
                    renko_times.append(ts)
                    renko_open_list.append(brick_high)
                    brick_high += box
                    brick_low   = brick_high - box
                    renko_close_list.append(brick_high)

    if not renko_times:
        # Not enough data
        empty = pd.DataFrame({
            "renko_open_htf":  np.nan,
            "renko_close_htf": np.nan,
        }, index=df.index)
        return empty

    renko_df = pd.DataFrame({
        "renko_open":  renko_open_list,
        "renko_close": renko_close_list,
    }, index=pd.DatetimeIndex(renko_times))

    # Keep last renko value per HTF timestamp
    renko_df = renko_df[~renko_df.index.duplicated(keep="last")]

    # Forward-fill EMA on renko close (series has irregular length)
    renko_ema1 = ema(renko_df["renko_close"], EMA1_LENGTH)
    renko_ema2 = ema(renko_df["renko_close"], EMA2_LENGTH)

    renko_df["ema1"] = renko_ema1
    renko_df["ema2"] = renko_ema2

    # Map back to base TF using merge_asof (handles market-closed gaps)
    left = pd.DataFrame({"time": df.index})
    right = pd.DataFrame({
        "time":            renko_df.index,
        "renko_open_htf":  renko_df["renko_open"].values,
        "renko_close_htf": renko_df["renko_close"].values,
        "renko_ema1_htf":  renko_df["ema1"].values,
        "renko_ema2_htf":  renko_df["ema2"].values,
    })

    merged = pd.merge_asof(left, right, on="time", direction="backward")
    merged.index = df.index

    return merged[["renko_open_htf", "renko_close_htf",
                   "renko_ema1_htf", "renko_ema2_htf"]]


# =============================================================================
# DEMA ATR (visual indicator — included for completeness)
# =============================================================================

def dema_atr_bands(df: pd.DataFrame, use_ha: bool = True) -> pd.Series:
    """
    DEMA ATR trailing indicator by BackQuant.
    Used for visual confirmation, not entry signals.
    Returns the DemaAtr series.
    """
    if use_ha:
        ha = heikin_ashi(df)
        source = ha["ha_close"]
    else:
        source = df["close"]

    ema1_d  = ema(source, DEMA_PERIOD)
    ema2_d  = ema(ema1_d, DEMA_PERIOD)
    dema    = 2 * ema1_d - ema2_d

    atr_val = atr(df, DEMA_ATR_LEN) * DEMA_ATR_FACT

    upper = dema + atr_val
    lower = dema - atr_val

    result = dema.copy()
    for i in range(1, len(result)):
        prev = result.iloc[i - 1]
        if lower.iloc[i] > prev:
            result.iloc[i] = lower.iloc[i]
        elif upper.iloc[i] < prev:
            result.iloc[i] = upper.iloc[i]
        else:
            result.iloc[i] = prev

    return result


# =============================================================================
# SANI MOMENTUM: Glide smoother, Weekly VWAP, Donchian Channel
# =============================================================================

def glide_smooth(series: pd.Series, length: int) -> pd.Series:
    """
    Pine Script f_smooth: custom EMA with alpha = 2/(len+1).
    Seeded at first non-NaN value (var float val = na).
    """
    alpha = 2.0 / (length + 1)
    result = series.copy().astype(float) * float("nan")
    first_loc = series.first_valid_index()
    if first_loc is None:
        return result
    idx = series.index.get_loc(first_loc)
    result.iloc[idx] = float(series.iloc[idx])
    for i in range(idx + 1, len(series)):
        if pd.isna(series.iloc[i]):
            result.iloc[i] = float("nan")
        else:
            result.iloc[i] = alpha * series.iloc[i] + (1.0 - alpha) * result.iloc[i - 1]
    return result


def weekly_vwap(df: pd.DataFrame, band_mult: float = 1.0) -> pd.DataFrame:
    """
    Weekly VWAP with standard deviation bands.
    Resets every Monday (ISO week boundary).
    Returns vwap_value, vwap_upper, vwap_lower aligned to df.index.
    """
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
    vol  = df["tick_volume"].astype(float)

    # ISO week key (Monday–Sunday) — strip tz before to_period (Period has no tz)
    week_key = df.index.tz_localize(None).to_period("W") if df.index.tz is not None \
               else df.index.to_period("W")

    pv  = hlc3 * vol
    pv2 = hlc3 ** 2 * vol

    cum_pv  = pv.groupby(week_key).cumsum()
    cum_vol = vol.groupby(week_key).cumsum()
    cum_pv2 = pv2.groupby(week_key).cumsum()

    safe_vol  = cum_vol.replace(0, float("nan"))
    vwap_val  = cum_pv / safe_vol
    variance  = (cum_pv2 / safe_vol) - vwap_val ** 2
    stdev     = variance.clip(lower=0.0).apply(np.sqrt)

    return pd.DataFrame({
        "vwap_value":  vwap_val,
        "vwap_upper":  vwap_val + stdev * band_mult,
        "vwap_lower":  vwap_val - stdev * band_mult,
        "vwap_upper2": vwap_val + stdev * BAND_MULT2,
    }, index=df.index)


def donchian(df: pd.DataFrame, length: int = 20) -> pd.DataFrame:
    """
    Donchian Channel: highest high and lowest low over N bars.
    Matches Pine Script ta.highest(length) / ta.lowest(length).
    """
    return pd.DataFrame({
        "dc_upper": df["high"].rolling(length).max(),
        "dc_lower": df["low"].rolling(length).min(),
    }, index=df.index)


# =============================================================================
# ADX + CHOPPINESS INDEX  (sideways filter — mirrors pine.text)
# =============================================================================

def adx(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Average Directional Index — measures trend strength (0–100)."""
    high, low, close = df["high"], df["low"], df["close"]
    prev_high = high.shift(1)
    prev_low  = low.shift(1)
    prev_close = close.shift(1)

    dm_plus  = np.where((high - prev_high) > (prev_low - low),
                        np.maximum(high - prev_high, 0.0), 0.0)
    dm_minus = np.where((prev_low - low) > (high - prev_high),
                        np.maximum(prev_low - low, 0.0), 0.0)

    tr = np.maximum(high - low,
         np.maximum(np.abs(high - prev_close),
                    np.abs(low  - prev_close)))

    tr_s   = pd.Series(tr,       index=df.index).ewm(alpha=1/length, min_periods=length, adjust=False).mean()
    dmp_s  = pd.Series(dm_plus,  index=df.index).ewm(alpha=1/length, min_periods=length, adjust=False).mean()
    dmm_s  = pd.Series(dm_minus, index=df.index).ewm(alpha=1/length, min_periods=length, adjust=False).mean()

    di_plus  = 100 * dmp_s / tr_s.replace(0, np.nan)
    di_minus = 100 * dmm_s / tr_s.replace(0, np.nan)
    dx = 100 * np.abs(di_plus - di_minus) / (di_plus + di_minus).replace(0, np.nan)
    adx_val = dx.ewm(alpha=1/length, min_periods=length, adjust=False).mean()
    return adx_val.fillna(0)


def choppiness(df: pd.DataFrame, length: int = 14) -> pd.Series:
    """Choppiness Index — >61.8 = sideways, <61.8 = trending."""
    atr1     = atr(df, 1)
    chop_sum = atr1.rolling(length).sum()
    hi       = df["high"].rolling(length).max()
    lo       = df["low"].rolling(length).min()
    rng      = hi - lo
    chop_val = 100.0 * np.log10(chop_sum / rng.replace(0, np.nan)) / np.log10(length)
    return chop_val.fillna(50.0)


def is_trending(df: pd.DataFrame) -> pd.Series:
    """True when ADX > threshold AND CHOP < threshold (not sideways)."""
    if not _config.SIDEWAYS_FILTER_ENABLED:
        return pd.Series(True, index=df.index)
    adx_val  = adx(df, ADX_LENGTH)
    chop_val = choppiness(df, CHOP_LENGTH)
    return (adx_val > ADX_THRESHOLD) & (chop_val < CHOP_THRESHOLD)


# =============================================================================
# COMPUTE ALL INDICATORS ON A DATAFRAME
# =============================================================================

def compute_all(df: pd.DataFrame, base_tf: str, setup_type: str) -> pd.DataFrame:
    """
    Add all indicator columns needed for signal generation.

    Parameters
    ----------
    df          : OHLCV DataFrame with DatetimeIndex (base timeframe)
    base_tf     : timeframe string, e.g. "M5"
    setup_type  : "Open/Close" or "Renko"

    Returns
    -------
    df enriched with indicator columns:
        ha_open_htf, ha_close_htf          (for Open/Close mode)
        renko_open_htf, renko_close_htf,
        renko_ema1_htf, renko_ema2_htf     (for Renko mode)
        adx_val, chop_val, is_trending      (ADX+CHOP filter)
        atr_risk                           (ATR(20) for TP/SL)
    """
    tf_mins = timeframe_to_minutes(base_tf)
    out = df.copy()

    if setup_type == "PurpleRain":
        # Oscillator: glide → fast/slow EMA → osc/sig
        glide = glide_smooth(out["close"], LEN_SMOOTH)
        fast  = ema(glide, LEN_FAST)
        slow  = ema(glide, LEN_SLOW)
        out["purplerain_osc"] = fast - slow
        out["purplerain_sig"] = sma(out["purplerain_osc"], LEN_SMOOTH)

        # Weekly VWAP with bands
        vwap = weekly_vwap(out, BAND_MULT)
        out["vwap_value"]  = vwap["vwap_value"]
        out["vwap_upper"]  = vwap["vwap_upper"]
        out["vwap_lower"]  = vwap["vwap_lower"]
        out["vwap_upper2"] = vwap["vwap_upper2"]

        # Donchian Channel for SL/TP
        dc = donchian(out, DC_LENGTH)
        out["dc_upper"] = dc["dc_upper"]
        out["dc_lower"] = dc["dc_lower"]

    elif setup_type == "Open/Close":
        ha_htf = heikin_ashi_htf(out, tf_mins)
        out["ha_open_htf"]       = ha_htf["ha_open_htf"]
        out["ha_close_htf"]      = ha_htf["ha_close_htf"]
        out["ha_open_htf_prev"]  = ha_htf["ha_open_htf_prev"]
        out["ha_close_htf_prev"] = ha_htf["ha_close_htf_prev"]

    elif setup_type == "Renko":
        renko = build_renko_series(out, tf_mins)
        out["renko_open_htf"]  = renko["renko_open_htf"]
        out["renko_close_htf"] = renko["renko_close_htf"]
        out["renko_ema1_htf"]  = renko["renko_ema1_htf"]
        out["renko_ema2_htf"]  = renko["renko_ema2_htf"]

    # ADX + Choppiness filter (always computed — used by PurpleRain and legacy modes)
    out["adx_val"]     = adx(out, ADX_LENGTH)
    out["chop_val"]    = choppiness(out, CHOP_LENGTH)
    out["is_trending"] = is_trending(out)

    # ATR for risk management / lot sizing fallback
    out["atr_risk"] = atr(out, ATR_LENGTH)

    return out
