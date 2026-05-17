# MT5 Algorithmic Trading

Automated trading bot for MetaTrader 5, built in Python.

---

## What's NOT included

| File | Reason |
|------|--------|
| `signals.py` | Proprietary strategy logic — not distributed |
| `indicators.py` | Proprietary indicator implementation — not distributed |
| `config.py` | Credentials and personal settings — never committed |

To run the bot you need to provide your own `signals.py`, `indicators.py`, and create `config.py` from `config.example.py`.

---

## What you can do with this repo

- Use the **live trading framework** (MT5 connection, order management, TP/SL monitoring, watchdog)
- Use the **backtest engine** against historical MT5 data
- Use the **Discord notification system** (fire-and-forget alerts for trade events)
- Plug in your own signal logic by implementing the `signals.py` interface
- Study the **risk management** module (ATR-based TP1/TP2, breakeven SL, partial close)

---

## File Structure

| File | Purpose |
|------|---------|
| `main.py` | Entry point — selects live or backtest mode |
| `live_trader.py` | Live trading loop — bar close → signal → order |
| `signals.py` | Signal computation interface *(not included)* |
| `indicators.py` | Technical indicators — EMA, ATR, ADX, VWAP, Donchian *(not included)* |
| `risk_manager.py` | TP/SL calculator and position state machine |
| `mt5_broker.py` | MT5 connection — fetch bars, place/close/modify orders |
| `backtest_engine.py` | Backtester — simulate trades on historical data |
| `visualizer.py` | Backtest chart output |
| `session_tracker.py` | Per-session trade history saved to `sessions/*.json` |
| `discord_notifier.py` | Discord webhook alerts (start, stop, trade, heartbeat) |
| `watchdog.py` | Auto-restart on crash (max 10 times) |
| `trade_csv_logger.py` | Export trades to CSV |
| `config.example.py` | Configuration template — copy to `config.py` |

---

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Create your config
cp config.example.py config.py
# Edit config.py with your MT5 credentials and settings

# 3. Provide your signals.py (see interface below)

# 4. Run live trading
python main.py --mode live

# 5. Or run a backtest
python main.py --mode backtest --symbol BTCUSDm --tf M15 --from 2025-01-01
```

---

## signals.py Interface

Your `signals.py` must expose at minimum:

```python
def get_current_signal(df) -> dict:
    # df: DataFrame with OHLCV + indicator columns from indicators.compute_all()
    # Returns: {"signal": "BUY" | "SELL" | None, "case": str, "sl_dist": float}
    ...
```

---

## Requirements

- Python 3.10+
- MetaTrader 5 (Windows)
- MT5 account (tested on Exness BTCUSDm)
- See `requirements.txt` for Python packages
