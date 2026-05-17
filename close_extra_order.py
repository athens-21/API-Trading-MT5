"""
close_extra_order.py — ปิด order ที่เกินมา (จาก bot ตัวเก่าที่รันพร้อมกัน)

Logic: ใน MT5 จะมี 2 positions เปิดอยู่ (LONG ทั้งคู่)
       ticket ต่ำกว่า = เปิดก่อน = ของ bot เก่าที่ถูกปิดไปแล้ว
       → ปิด ticket ต่ำกว่า, เหลือ ticket สูงกว่าไว้ให้ bot ปัจจุบันจัดการ
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import MetaTrader5 as mt5
from config import MT5_LOGIN, MT5_PASSWORD, MT5_SERVER, MT5_PATH, SYMBOL, MAGIC

# Connect
kwargs = {}
if MT5_PATH:
    kwargs["path"] = MT5_PATH
if MT5_LOGIN:
    kwargs.update(login=MT5_LOGIN, password=MT5_PASSWORD, server=MT5_SERVER)

if not mt5.initialize(**kwargs):
    print(f"[ERROR] MT5 init failed: {mt5.last_error()}")
    sys.exit(1)

# Get all positions with bot's magic
positions = [p for p in (mt5.positions_get(symbol=SYMBOL) or []) if p.magic == MAGIC]

print(f"\nOpen positions for {SYMBOL} (magic={MAGIC}):")
for p in positions:
    print(f"  ticket={p.ticket}  type={'BUY' if p.type==0 else 'SELL'}  "
          f"lot={p.volume}  open_price={p.price_open}  profit={p.profit:.2f}")

if len(positions) <= 1:
    print("\n[OK] Only 1 (or 0) position — nothing to close.")
    mt5.shutdown()
    sys.exit(0)

# Sort by ticket: lowest = oldest = rogue bot's order
positions.sort(key=lambda p: p.ticket)
to_close = positions[0]
to_keep  = positions[1:]

print(f"\n→ Closing ticket={to_close.ticket} (oldest, from rogue bot)")
print(f"→ Keeping: {[p.ticket for p in to_keep]}")

confirm = input("\nType YES to close: ").strip()
if confirm != "YES":
    print("Aborted.")
    mt5.shutdown()
    sys.exit(0)

# Close it
tick = mt5.symbol_info_tick(SYMBOL)
if to_close.type == 0:  # BUY → close with SELL
    order_type = mt5.ORDER_TYPE_SELL
    price = tick.bid
else:
    order_type = mt5.ORDER_TYPE_BUY
    price = tick.ask

req = {
    "action":    mt5.TRADE_ACTION_DEAL,
    "symbol":    SYMBOL,
    "volume":    to_close.volume,
    "type":      order_type,
    "position":  to_close.ticket,
    "price":     price,
    "deviation": 20,
    "magic":     MAGIC,
    "comment":   "close extra order",
    "type_time": mt5.ORDER_TIME_GTC,
    "type_filling": mt5.ORDER_FILLING_IOC,
}
result = mt5.order_send(req)
if result and result.retcode == mt5.TRADE_RETCODE_DONE:
    print(f"\n[OK] Closed ticket={to_close.ticket}  price={result.price}")
else:
    print(f"\n[ERROR] retcode={result.retcode if result else 'None'}  {result.comment if result else ''}")

mt5.shutdown()
