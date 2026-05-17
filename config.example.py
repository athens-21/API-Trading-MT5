# config.example.py — Copy this file to config.py and fill in your credentials
# config.py is excluded from version control (.gitignore)

# ── MT5 Connection ─────────────────────────────────────────────────────────
MT5_LOGIN    = 0              # Your MT5 account number
MT5_PASSWORD = "your_password"
MT5_SERVER   = "Exness-MT5Real8"

# ── Symbol & Timeframe ─────────────────────────────────────────────────────
SYMBOL    = "BTCUSDm"
TIMEFRAME = "M15"

# ── Strategy ───────────────────────────────────────────────────────────────
SETUP_TYPE = "PurpleRain"
TPS_TYPE   = "ATR"

# ── Lot Sizing ─────────────────────────────────────────────────────────────
LOT_SIZE     = 0      # 0 = auto (uses LOT_RISK_PCT)
LOT_RISK_PCT = 5.0    # % of balance to risk per trade

# ── ATR TP/SL Multipliers ─────────────────────────────────────────────────
TP1_MULT = 0.8    # TP1 = entry ± slDist × 0.8  (partial close 75%)
TP2_MULT = 2.0    # TP2 = entry ± slDist × 2.0  (full close)

# ── Filters ────────────────────────────────────────────────────────────────
SIDEWAYS_FILTER_ENABLED = True
ADX_SIDEWAYS_THRESHOLD  = 18

# ── VWAP Settings ──────────────────────────────────────────────────────────
BAND_MULT  = 1.0   # VWAP band multiplier
BAND_MULT2 = 2.0   # Upper band 2 (overbought filter)

# ── Case Toggles ───────────────────────────────────────────────────────────
USE_VWAP_CASE1  = False
USE_VWAP_CASE21 = True
USE_VWAP_CASE3  = True
USE_VWAP_CASE51 = True
USE_VWAP_CASE7  = True

# UB2 (overbought) filters per case
UB2_FILTER_ALL    = False
UB2_FILTER_CASE7  = True
UB2_FILTER_CASE21 = False

# FVG settings (Case 21)
FVG_LOOKBACK_21 = 14
FVG_SMOOTH_21   = 9

# ── Sunday Block ───────────────────────────────────────────────────────────
SKIP_SUNDAY = True    # Block trading Sat 17:00 UTC → Sun 21:00 UTC

# ── Discord Notifications ──────────────────────────────────────────────────
DISCORD_ENABLED     = False
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"
DISCORD_MENTION_ID  = ""   # Optional: Discord user ID to @mention
