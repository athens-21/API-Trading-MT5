# config.example.py — Copy this file to config.py and fill in your credentials
# config.py is excluded from version control (.gitignore)

# ── MT5 Connection ─────────────────────────────────────────────────────────
MT5_LOGIN    = 0              # Your MT5 account number
MT5_PASSWORD = "your_password"
MT5_SERVER   = "your_SERVER"

# ── Symbol & Timeframe ─────────────────────────────────────────────────────
SYMBOL    = "SYMBOLm"
TIMEFRAME = "TF"

# ── Strategy ───────────────────────────────────────────────────────────────
SETUP_TYPE = "your_Type"
TPS_TYPE   = "ATR"

# ── Lot Sizing ─────────────────────────────────────────────────────────────
LOT_SIZE     = 0      # 0 = auto (uses LOT_RISK_PCT)
LOT_RISK_PCT = 1.0   # % of balance to risk per trade

# ── Sunday Block ───────────────────────────────────────────────────────────
SKIP_SUNDAY = True    # Block trading Sat 17:00 UTC → Sun 21:00 UTC

# ── Discord Notifications ──────────────────────────────────────────────────
DISCORD_ENABLED     = False
DISCORD_WEBHOOK_URL = "https://discord.com/api/webhooks/YOUR_ID/YOUR_TOKEN"
DISCORD_MENTION_ID  = ""   # Optional: Discord user ID to @mention
