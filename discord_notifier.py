"""
discord_notifier.py — ส่ง trade alerts ไปยัง Discord ผ่าน Webhook

การแจ้งเตือนที่มี:
  notify_start()  — bot เริ่มทำงาน
  notify_stop()   — bot หยุดทำงาน
  notify_trade()  — เปิด/ปิด order (พร้อม TP/SL และ Profit)
  notify_error()  — error / crash

ทุกฟังก์ชัน fire-and-forget (ไม่ block) ผ่าน background thread
ข้ามทั้งหมดเมื่อ DISCORD_WEBHOOK_URL ว่างเปล่าหรือ DISCORD_ENABLED = False
"""

import json
import logging
import threading
from datetime import datetime, timezone

try:
    import requests as _requests
    _REQUESTS_AVAILABLE = True
except ImportError:
    import urllib.request
    import urllib.error
    _REQUESTS_AVAILABLE = False

log = logging.getLogger("discord")

try:
    from config import (
        DISCORD_WEBHOOK_URL, DISCORD_ENABLED, DISCORD_MENTION_ID,
        SYMBOL, TIMEFRAME,
        MT5_LOGIN, MT5_SERVER, SETUP_TYPE, TPS_TYPE,
        SIDEWAYS_FILTER_ENABLED, LOT_SIZE, LOT_RISK_PCT,
    )
except ImportError:
    DISCORD_WEBHOOK_URL    = ""
    DISCORD_ENABLED        = False
    DISCORD_MENTION_ID     = ""
    SYMBOL                 = "?"
    TIMEFRAME              = "?"
    MT5_LOGIN              = "?"
    MT5_SERVER             = "?"
    SETUP_TYPE             = "?"
    TPS_TYPE               = "?"
    SIDEWAYS_FILTER_ENABLED = True
    LOT_SIZE               = 0
    LOT_RISK_PCT           = 0

# Embed colours
_COLOR_LONG  = 0x26a69a   # teal  (buy)
_COLOR_SHORT = 0xef5350   # red   (sell)
_COLOR_CLOSE = 0xffd54f   # yellow (close/profit)
_COLOR_WIN   = 0x3fb950   # green (profit)
_COLOR_LOSS  = 0xf85149   # red   (loss)
_COLOR_ERROR = 0xff5722   # orange (error)
_COLOR_INFO  = 0x42a5f5   # blue  (start/stop)


# =============================================================================
# INTERNAL HELPERS
# =============================================================================

def _post(payload: dict):
    """POST JSON payload ไปยัง Discord webhook (background thread)."""
    if not DISCORD_ENABLED or not DISCORD_WEBHOOK_URL:
        return

    def _send():
        try:
            if _REQUESTS_AVAILABLE:
                resp = _requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
                if resp.status_code not in (200, 204):
                    log.warning("Discord webhook %s: %s", resp.status_code, resp.text[:200])
            else:
                data = json.dumps(payload).encode("utf-8")
                req  = urllib.request.Request(
                    DISCORD_WEBHOOK_URL, data=data,
                    headers={"Content-Type": "application/json"}, method="POST",
                )
                with urllib.request.urlopen(req, timeout=10):
                    pass
        except Exception as e:
            log.warning("Discord webhook error: %s", e)

    threading.Thread(target=_send, daemon=True).start()


def _mention() -> str:
    return f"<@{DISCORD_MENTION_ID}> " if DISCORD_MENTION_ID else ""


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def _pct(price_from: float, price_to: float, direction: int) -> str:
    """คำนวณ % เปลี่ยนแปลงจาก entry ไปยัง TP/SL"""
    if price_from <= 0:
        return ""
    pct = (price_to - price_from) / price_from * 100.0 * direction
    return f"{pct:+.2f}%"


# =============================================================================
# PUBLIC API
# =============================================================================

def notify_trade(
    direction: str,         # "LONG" or "SHORT"
    action: str,            # "OPEN" or "CLOSE"
    price: float,
    lot: float,
    ticket: int   = None,
    atr:   float  = None,
    balance: float = None,
    equity:  float = None,
    pnl:     float = None,  # P&L สำหรับ CLOSE
    reason:  str   = "",    # เหตุผลที่ปิด เช่น "tp1", "sl", "signal"
    tp1: float = None,      # ราคา TP1 (สำหรับ OPEN)
    tp2: float = None,
    tp3: float = None,
    sl:  float = None,
):
    """ส่ง alert การเปิด/ปิด order"""
    is_long = direction.upper() == "LONG"
    is_open = action.upper() == "OPEN"
    d = 1 if is_long else -1

    # ── กำหนดสี ──────────────────────────────────────────────────────────────
    if is_open:
        color = _COLOR_LONG if is_long else _COLOR_SHORT
    else:
        if pnl is not None:
            color = _COLOR_WIN if pnl >= 0 else _COLOR_LOSS
        else:
            color = _COLOR_CLOSE

    dir_label  = "🟢 LONG"  if is_long else "🔴 SHORT"
    act_label  = "📈 เปิด"   if is_open else "📉 ปิด"
    title = f"{act_label}  {dir_label}  @  {price:,.2f}"

    # ── Fields ───────────────────────────────────────────────────────────────
    fields = [
        {"name": "Symbol",    "value": f"`{SYMBOL} {TIMEFRAME}`", "inline": True},
        {"name": "ราคา",      "value": f"`{price:,.2f}`",          "inline": True},
        {"name": "Lots",      "value": f"`{lot:.2f}`" if lot else "`—`", "inline": True},
    ]

    if ticket:
        fields.append({"name": "Ticket", "value": f"`{ticket}`", "inline": True})

    # OPEN — แสดง TP/SL
    if is_open and tp1 is not None:
        fields.append({"name": "\u200b", "value": "\u200b", "inline": False})  # spacer
        fields.append({
            "name": "🎯 TP1",
            "value": f"`{tp1:,.2f}` ({_pct(price, tp1, d)})",
            "inline": True,
        })
        if tp2 is not None:
            fields.append({
                "name": "🎯 TP2",
                "value": f"`{tp2:,.2f}` ({_pct(price, tp2, d)})",
                "inline": True,
            })
        if tp3 is not None:
            fields.append({
                "name": "🎯 TP3",
                "value": f"`{tp3:,.2f}` ({_pct(price, tp3, d)})",
                "inline": True,
            })
        if sl is not None:
            fields.append({
                "name": "🛑 SL",
                "value": f"`{sl:,.2f}` ({_pct(price, sl, d)})",
                "inline": True,
            })

    # CLOSE — แสดง reason และ P&L
    if not is_open:
        if reason:
            reason_labels = {
                "tp1": "✅ TP1 hit",
                "tp2": "✅ TP2 hit",
                "tp3": "✅ TP3 hit",
                "sl":  "❌ SL hit",
                "signal": "🔄 Signal reverse",
            }
            fields.append({
                "name": "เหตุผล",
                "value": reason_labels.get(reason.lower(), reason),
                "inline": True,
            })
        if pnl is not None:
            sign = "+" if pnl >= 0 else ""
            fields.append({
                "name": "💰 P&L",
                "value": f"`{sign}{pnl:,.2f} $`",
                "inline": True,
            })

    # Balance / Equity
    if balance is not None:
        fields.append({"name": "Balance", "value": f"`${balance:,.2f}`", "inline": True})
    if equity is not None:
        fields.append({"name": "Equity",  "value": f"`${equity:,.2f}`",  "inline": True})

    _post({
        "content": _mention() if is_open else "",
        "embeds": [{
            "title":  title,
            "color":  color,
            "fields": fields,
            "footer": {"text": f"ParanoidSignals™ 7.9-X  •  {_now_utc()}"},
        }],
    })


def notify_start(balance: float = None, equity: float = None):
    """Bot เริ่มทำงาน"""
    lot_str = f"{LOT_SIZE} lots (fixed)" if LOT_SIZE > 0 else f"{LOT_RISK_PCT}% risk"
    fields = [
        {"name": "Account",   "value": f"`{MT5_LOGIN}`",                                "inline": True},
        {"name": "Server",    "value": f"`{MT5_SERVER}`",                               "inline": True},
        {"name": "\u200b",    "value": "\u200b",                                        "inline": False},
        {"name": "Symbol",    "value": f"`{SYMBOL} {TIMEFRAME}`",                       "inline": True},
        {"name": "Setup",     "value": f"`{SETUP_TYPE}`",                               "inline": True},
        {"name": "TPS",       "value": f"`{TPS_TYPE}`",                                 "inline": True},
        {"name": "Filter",    "value": f"`ADX+CHOP {'ON' if SIDEWAYS_FILTER_ENABLED else 'OFF'}`", "inline": True},
        {"name": "Lot Size",  "value": f"`{lot_str}`",                                  "inline": True},
    ]
    if balance is not None:
        fields.append({"name": "Balance", "value": f"`${balance:,.2f}`", "inline": True})
    if equity is not None:
        fields.append({"name": "Equity",  "value": f"`${equity:,.2f}`",  "inline": True})

    _post({
        "content": _mention(),
        "embeds": [{
            "title":  "🤖 Bot Started",
            "color":  _COLOR_INFO,
            "fields": fields,
            "footer": {"text": f"ParanoidSignals™ 7.9-X  •  {_now_utc()}"},
        }],
    })


def notify_pid(pid: int):
    """Watchdog แจ้ง PID ของ bot process ที่เพิ่ง start"""
    _post({
        "embeds": [{
            "title":       "🔢 Bot Process Started",
            "description": f"**{SYMBOL} {TIMEFRAME}** — PID `{pid}`",
            "color":       _COLOR_INFO,
            "footer":      {"text": f"ParanoidSignals™ 7.9-X  •  {_now_utc()}"},
        }],
    })


def notify_stop(reason: str = ""):
    """Bot หยุดทำงาน"""
    _post({
        "embeds": [{
            "title":       "🛑 Bot Stopped",
            "description": f"**{SYMBOL} {TIMEFRAME}**  {reason}",
            "color":       _COLOR_CLOSE,
            "footer":      {"text": f"ParanoidSignals™ 7.9-X  •  {_now_utc()}"},
        }],
    })


def notify_error(message: str):
    """Error / crash alert"""
    _post({
        "content": _mention(),
        "embeds": [{
            "title":       "⚠️ Bot Error",
            "description": f"```{message[:1800]}```",
            "color":       _COLOR_ERROR,
            "footer":      {"text": f"ParanoidSignals™ 7.9-X  •  {_now_utc()}"},
        }],
    })


def notify_restore(
    direction: str,       # "LONG" or "SHORT"
    entry_price: float,
    condition: float,     # 1.0 / 1.1 / -1.0 / -1.1
    ticket: int,
    tp1: float = None,
    tp2: float = None,
    sl:  float = None,
    source: str = "file", # "file" หรือ "MT5"
):
    """Alert on restart when an existing open order is found — resumes management."""
    is_long  = direction.upper() == "LONG"
    d        = 1 if is_long else -1
    color    = _COLOR_LONG if is_long else _COLOR_SHORT
    dir_label = "🟢 LONG" if is_long else "🔴 SHORT"

    cond_abs = abs(condition)
    if cond_abs >= 1.1:
        status_str = "TP1 hit — waiting for TP2 (SL = Breakeven)"
    else:
        status_str = "Waiting for TP1"

    source_label = "trade_state.json" if source == "file" else "MT5 (fallback)"

    fields = [
        {"name": "Symbol",    "value": f"`{SYMBOL} {TIMEFRAME}`",      "inline": True},
        {"name": "Direction", "value": dir_label,                       "inline": True},
        {"name": "Ticket",    "value": f"`{ticket}`",                   "inline": True},
        {"name": "​",    "value": "​",                        "inline": False},
        {"name": "Entry",     "value": f"`{entry_price:,.2f}`",         "inline": True},
        {"name": "Status",    "value": status_str,                      "inline": True},
        {"name": "Source",    "value": f"`{source_label}`",             "inline": True},
    ]

    if tp1 is not None:
        fields.append({"name": "🎯 TP1", "value": f"`{tp1:,.2f}` ({_pct(entry_price, tp1, d)})", "inline": True})
    if tp2 is not None:
        fields.append({"name": "🎯 TP2", "value": f"`{tp2:,.2f}` ({_pct(entry_price, tp2, d)})", "inline": True})
    if sl is not None:
        fields.append({"name": "🛑 SL",  "value": f"`{sl:,.2f}` ({_pct(entry_price, sl, d)})",   "inline": True})

    _post({
        "content": _mention(),
        "embeds": [{
            "title":  "🔄 Bot Resumed — Open Order Found",
            "color":  color,
            "fields": fields,
            "footer": {"text": f"ParanoidSignals™ 7.9-X  •  {_now_utc()}"},
        }],
    })


def notify_sunday_block():
    """แจ้งเตือนเมื่อเข้าสู่ช่วง Sunday block (หยุดเทรดวันอาทิตย์ไทย)"""
    _post({
        "embeds": [{
            "title":       "😴 Sunday Block — หยุดเทรดแล้ว",
            "description": "ตลาดพัก — บอทจะไม่เปิด order ใหม่จนกว่าจะถึงวันจันทร์ตี 4 (เวลาไทย)",
            "color":       0x90a4ae,
            "fields": [
                {"name": "Symbol",   "value": f"`{SYMBOL} {TIMEFRAME}`", "inline": True},
                {"name": "Resume",   "value": "`จันทร์ 04:00 ไทย`",       "inline": True},
            ],
            "footer": {"text": f"ParanoidSignals™ 7.9-X  •  {_now_utc()}"},
        }],
    })


def notify_sunday_unblock():
    """แจ้งเตือนเมื่อออกจาก Sunday block (ตลาดเปิด วันจันทร์)"""
    _post({
        "embeds": [{
            "title":       "🟢 Sunday Unblock — กลับมาเทรดแล้ว",
            "description": "ตลาดเปิด — บอทพร้อมรับสัญญาณใหม่",
            "color":       0x43a047,
            "fields": [
                {"name": "Symbol", "value": f"`{SYMBOL} {TIMEFRAME}`", "inline": True},
            ],
            "footer": {"text": f"ParanoidSignals™ 7.9-X  •  {_now_utc()}"},
        }],
    })


def notify_heartbeat(balance: float = None, equity: float = None):
    """Heartbeat ทุก 3 ชั่วโมง — ยืนยันว่า bot ยังทำงานอยู่"""
    fields = [
        {"name": "Symbol", "value": f"`{SYMBOL} {TIMEFRAME}`", "inline": True},
    ]
    if balance is not None:
        fields.append({"name": "Balance", "value": f"`${balance:,.2f}`", "inline": True})
    if equity is not None:
        fields.append({"name": "Equity",  "value": f"`${equity:,.2f}`",  "inline": True})

    _post({
        "embeds": [{
            "title":  "💚 Bot Heartbeat",
            "description": "Bot ทำงานปกติ",
            "color":  0x43a047,
            "fields": fields,
            "footer": {"text": f"ParanoidSignals™ 7.9-X  •  {_now_utc()}"},
        }],
    })
