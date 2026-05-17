# PurpleRain Signal Logic — ParanoidSignals™ 7.9-X

> **Setup:** PurpleRain | **Symbol:** BTCUSDm | **Timeframe:** M15 | **Filter:** ADX+CHOP ON

---

## ภาพรวม

Bot คำนวณสัญญาณบน **bar ที่ปิดแล้วเท่านั้น** (process_on_close) เพื่อให้ตรงกับ Pine Script
ทุก bar M15 ที่ปิด bot จะวิ่งผ่าน 3 ด่านด้านล่าง — ต้องผ่านครบทุกด่านจึงจะเปิด order

```
buy  = (raw_up  + VWAP case + Noise filter + bull_candle) | Case6 | Case7
sell = (raw_down + VWAP case + Noise filter + bear_candle) | Case5
```

---

## ด่าน 1 — Sani Oscillator Crossover

```python
raw_up   = crossover(osc, sig)  & ~is_sideway   # osc ตัดขึ้นผ่าน sig line
raw_down = crossunder(osc, sig) & ~is_sideway   # osc ตัดลงผ่าน sig line
is_sideway = adx < ADX_THRESHOLD                # True = ตลาด sideways → block
```

### หลักการ
- **Sani Oscillator** = ค่า momentum ที่คำนวณจาก EMA หลายชั้น
- **sig line** = EMA ของ oscillator
- สัญญาณเกิดเฉพาะตอน **ข้าม** เท่านั้น — ถ้า osc อยู่เหนือ sig อยู่แล้วโดยไม่มี crossover ไม่นับ
- `adx_ok=False` ในล็อก = ด่านนี้บล็อก ไม่มีสัญญาณออกมาได้เลย

---

## ด่าน 2 — VWAP Cases

ใช้ **Weekly VWAP** พร้อม upper/lower band เพื่อกำหนดบริบทของราคา

```
VWAP upper band = vwap + (vwap × VWAP_DIST_LONG%)
VWAP lower band = vwap − (vwap × VWAP_DIST_SHORT%)
```

### Long Cases (BUY)

| Case | เงื่อนไข | ความหมาย | เปิดด้วย |
|------|----------|-----------|----------|
| **1** | `recent_touch_lower` (ใน lookback bars) + `raw_up` + `close > lower` | เคยแตะ lower band แล้วเด้งขึ้น | `USE_VWAP_CASE1` |
| **2** | `raw_up` + `low <= lower` + `close > lower` | bar นี้แตะ lower band แต่ปิดเหนือมัน (pin bar / wick) | `USE_VWAP_CASE2` |
| **3** | `close < lower` + far from VWAP + `raw_up` | ราคาหลุดต่ำกว่า lower band มากผิดปกติ | `USE_VWAP_CASE3` |
| **4** | `raw_up` + `close > upper` + near VWAP | ราคาเหนือ upper band แต่ยังใกล้ VWAP | `USE_VWAP_CASE4` |

### Short Cases (SELL)

| Case | เงื่อนไข | ความหมาย | เปิดด้วย |
|------|----------|-----------|----------|
| **1** | `recent_touch_upper` + `raw_down` + `close < upper` | เคยแตะ upper band แล้วร่วงลง | `USE_VWAP_CASE1` |
| **2** | `raw_down` + `high >= upper` + `close < upper` | bar นี้แตะ upper band แต่ปิดใต้มัน | `USE_VWAP_CASE2` |
| **3** | `close > upper` + far from VWAP + `raw_down` | ราคาสูงกว่า upper band มากผิดปกติ | `USE_VWAP_CASE3` |
| **4** | `raw_down` + `close < lower` + near VWAP | ราคาต่ำกว่า lower band แต่ยังใกล้ VWAP | `USE_VWAP_CASE4` |

> ถ้าเปิดทุก case ปิดหมด (`USE_VWAP_CASE1–4 = False`) → ข้ามด่านนี้ทั้งหมด (pass ทันที)

---

## ด่าน 3 — Noise Filters (Rule A–E)

ต้องผ่าน **ทุก rule พร้อมกัน** จึงจะผ่านด่านนี้

| Rule | เงื่อนไข | กรองอะไร |
|------|----------|---------|
| **A** | ถ้า bar แตะ VWAP → ต้องมี lower/upper band touch ใน 6 bars ก่อน | กรองสัญญาณที่แตะ VWAP โดยไม่มี context |
| **B** | bar แตะ VWAP แล้วปิดผิดฝั่ง → block | กรองแท่งที่ momentum ผิดทิศหลัง touch VWAP |
| **C** | bar แตะทั้ง VWAP + opposite band พร้อมกัน → block | กรองแท่ง range กว้างมาก (indecision bar) |
| **D** | ราคาอยู่ระหว่าง VWAP กับ band → ต้องมี VWAP touch ใน 5 bars ก่อน | กรองสัญญาณที่ไม่มี pullback ถึง VWAP |
| **E** | ราคาใกล้ VWAP (< 0.9%) + แท่ง bearish → ต้องแตะ lower band | กรองแท่งเล็กๆ ใกล้ VWAP ที่ไม่มี momentum |

---

## Special Cases (ข้ามด่าน VWAP + Noise ปกติ)

Special cases เปิดสัญญาณเองได้โดยตรง ไม่ต้องผ่าน Case 1–4 และ Noise Filter

### Case 5 — Short พิเศษ (`USE_VWAP_CASE5`)
```python
raw_down & bear_candle & (bear_body > prev_body × 2) & (high >= upper)
```
| เงื่อนไข | รายละเอียด |
|----------|------------|
| `raw_down` | osc ตัดลงผ่าน sig |
| `bear_candle` | close < open (แท่งแดง) |
| `bear_body > prev_body × 2` | ขนาด body ใหญ่กว่า bar ก่อนหน้า 2 เท่า |
| `high >= upper` | bar แตะ VWAP upper band |

**เมื่อใดใช้:** แท่งหมีแรงมาก rejection จาก upper band — momentum ชัดเจนพอที่จะข้ามด่านปกติ

---

### Case 6 — Long พิเศษ (`USE_VWAP_CASE6`)
```python
raw_up & bull_candle & (bull_body > prev_body × 2) & recent_lower_touch4
```
| เงื่อนไข | รายละเอียด |
|----------|------------|
| `raw_up` | osc ตัดขึ้นผ่าน sig |
| `bull_candle` | close >= open (แท่งเขียว) |
| `bull_body > prev_body × 2` | body ใหญ่กว่า bar ก่อน 2 เท่า |
| `recent_lower_touch4` | Donchian lower touch ใน 4 bars ก่อน |

**เมื่อใดใช้:** แท่งเขียวแรงมากหลังแตะ dc_lower — ลักษณะ strong bounce

---

### Case 7 — Long พิเศษ (`USE_VWAP_CASE7`)
```python
raw_up & bull_candle & (bull_body > prev_body × 2) & upper_touch_2bars
```
| เงื่อนไข | รายละเอียด |
|----------|------------|
| `raw_up` | osc ตัดขึ้นผ่าน sig |
| `bull_candle` | close >= open (แท่งเขียว) |
| `bull_body > prev_body × 2` | body ใหญ่กว่า bar ก่อน 2 เท่า |
| `upper_touch_2bars` | VWAP upper band touch ใน 2 bars ปัจจุบัน (รวม bar นี้) |

**เมื่อใดใช้:** breakout แท่งเขียวแรงผ่าน upper band — momentum breakout entry

---

## Flow การตัดสินใจทุก Bar

```
M15 Bar ปิด
    │
    ├─ adx_ok = False? ──────────────────────────────→ หยุด (log: adx_ok=False)
    │
    ├─ osc crossover/crossunder sig?
    │    └─ ไม่ ────────────────────────────────────→ หยุด (log: buy=False sell=False)
    │
    ├─ VWAP case ผ่าน (case1–4)?
    │    └─ ไม่ผ่าน ───────────────────────────────→ หยุด
    │    (ยกเว้น: case5 ไม่ต้องผ่าน VWAP case ปกติ)
    │
    ├─ Noise filter A–E ผ่านหมด?
    │    └─ ไม่ผ่าน ───────────────────────────────→ หยุด
    │    (ยกเว้น: case5/6/7 ข้ามด่านนี้)
    │
    ├─ bull_candle (buy) / bear_candle (sell)?
    │    └─ ไม่ตรง ────────────────────────────────→ หยุด
    │
    └─ buy=True / sell=True
         └─ open Long / Short ทันที พร้อม TP1/TP2/SL
```

---

## TP/SL ที่ตั้งเมื่อเข้า Order

```
slDist = close − dc_lower        (Long)
       = dc_upper − close        (Short)

SL   = entry − slDist            (Long)  | entry + slDist    (Short)
TP1  = entry + slDist × TP1_MULT (Long)  | entry − slDist × TP1_MULT (Short) → ปิด 75%
TP2  = entry + slDist × TP2_MULT (Long)  | entry − slDist × TP2_MULT (Short) → ปิดที่เหลือ

หลัง TP1 hit → SL เลื่อนมาที่ entry (breakeven)
```

---

## ตัวอย่าง Log ที่ควรสังเกต

```
# ปกติ — รอสัญญาณ
[10:15] close=78480  buy=False  sell=False  adx_ok=True

# Sideways filter ทำงาน
[02:15] close=78695  buy=False  sell=False  adx_ok=False

# มีสัญญาณ!
[10:30] close=78650  buy=True   sell=False  adx_ok=True
→ Opening LONG  entry=78650  tp1=79500  tp2=80100  sl=78100

# TP1 hit
→ Partial close 75%  SL moved to breakeven (entry)
```

---

## ความถี่สัญญาณโดยประมาณ (M15)

| สภาพตลาด | สัญญาณต่อวัน |
|-----------|-------------|
| Trending ชัดเจน | 2–4 ครั้ง |
| Sideways / range แคบ | 0–1 ครั้ง |
| หลัง breakout | 1–3 ครั้ง |

> **ไม่มีสัญญาณนานหลายชั่วโมง = ปกติ** — PurpleRain ออกแบบให้เลือกสัญญาณคุณภาพสูงเท่านั้น

---

*ParanoidSignals™ 7.9-X — อัปเดต 2026-05-04*
