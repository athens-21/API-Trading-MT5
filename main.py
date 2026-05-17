"""
main.py — Entry point for the ParanoidSignals™ Python trading bot.

Usage examples
--------------
# Run backtest on MT5 data (MT5 must be open):
python main.py --mode backtest

# Run backtest from a CSV file:
python main.py --mode backtest --csv data/EURUSD_M5.csv

# Export backtest trade list:
python main.py --mode backtest --export trades.csv

# Start live trading:
python main.py --mode live

# Override symbol/timeframe/setup at runtime:
python main.py --mode backtest --symbol GBPUSD --tf M15 --setup Open/Close --tps ATR

# Run backtest with custom date range:
python main.py --mode backtest --from 2023-01-01 --to 2024-01-01
"""

import argparse
import logging
import sys
import os
import atexit
from datetime import datetime

# ── Force UTF-8 output on Windows (prevents UnicodeEncodeError when
#    stdout/stderr are redirected to files via Start-Process) ──────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Single-instance lock (live mode only) ────────────────────────────
_LOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "live_bot.lock")

def _pid_alive(pid: int) -> bool:
    """Return True if a process with this PID is running."""
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    # Fallback: os.kill(pid, 0) works on Windows too
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _acquire_lock(lock_file=None):
    """Create lock file containing current PID. Exit if another instance is running."""
    if lock_file is None:
        lock_file = _LOCK_FILE
    if os.path.exists(lock_file):
        try:
            with open(lock_file) as f:
                old_pid = int(f.read().strip())
            if _pid_alive(old_pid):
                print(f"\n[ERROR] Live bot already running (PID {old_pid}). "
                      f"Run STOP_BOT.bat first or delete {lock_file}\n")
                sys.exit(1)
            else:
                print(f"[INFO] Removing stale lock (PID {old_pid} no longer running).")
        except (ValueError, OSError):
            pass  # Corrupt lock file — overwrite it
    with open(lock_file, "w") as f:
        f.write(str(os.getpid()))
    atexit.register(_release_lock, lock_file)

def _release_lock(lock_file=None):
    if lock_file is None:
        lock_file = _LOCK_FILE
    try:
        os.remove(lock_file)
    except OSError:
        pass

import colorama
colorama.init(autoreset=True)


def setup_logging(level: str, logfile: str = None):
    fmt = "%(asctime)s [%(levelname)s] %(name)s — %(message)s"
    root = logging.getLogger()
    # Remove existing handlers (allows re-configuration for live per-account log)
    for h in root.handlers[:]:
        root.removeHandler(h)
        h.close()
    handlers = [logging.StreamHandler(sys.stdout)]
    if logfile:
        handlers.append(logging.FileHandler(logfile, encoding="utf-8"))
    logging.basicConfig(level=getattr(logging, level.upper(), logging.INFO),
                        format=fmt, handlers=handlers)


def main():
    parser = argparse.ArgumentParser(
        description="ParanoidSignals™ 7.9-X — Python/MT5 Trading Bot",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    parser.add_argument(
        "--mode", choices=["backtest", "live"], default="backtest",
        help="Run mode: 'backtest' (default) or 'live'",
    )
    parser.add_argument("--symbol", default=None, help="Trading symbol, e.g. EURUSD")
    parser.add_argument("--tf",     default=None, help="Timeframe, e.g. M5, H1")
    parser.add_argument("--setup",  default=None,
                        choices=["Open/Close", "Renko", "PurpleRain"],
                        help="Setup type")
    parser.add_argument("--tps",    default=None,
                        choices=["Trailing", "ATR", "Options"],
                        help="TPS mode")
    parser.add_argument("--filter", default=None,
                        dest="sideways_filter",
                        choices=["on", "off"],
                        help="ADX+CHOP filter: 'on' or 'off'")
    parser.add_argument("--htf",   default=None, type=int,
                        choices=[90, 120, 240],
                        help="[Backtest] HTF minutes: 90 (1h30), 120 (2H), 240 (4H)")
    parser.add_argument("--csv",    default=None,
                        help="[Backtest] Load data from CSV instead of MT5")
    parser.add_argument("--bars",   default=5000, type=int,
                        help="[Backtest] Number of bars to fetch from MT5")
    parser.add_argument("--export", default=None,
                        help="[Backtest] Export trade list to this CSV path")
    parser.add_argument("--from",   default=None, dest="from_date",
                        help="[Backtest] Start date YYYY-MM-DD")
    parser.add_argument("--to",     default=None, dest="to_date",
                        help="[Backtest] End date YYYY-MM-DD")
    parser.add_argument("--log",    default="INFO",
                        help="Logging level: DEBUG, INFO, WARNING, ERROR")
    parser.add_argument("--chart",  action="store_true", default=True,
                        help="Show visual chart after backtest (default: on)")
    parser.add_argument("--no-chart", dest="chart", action="store_false",
                        help="Disable chart window")

    args = parser.parse_args()

    # ── Apply overrides to config ─────────────────────────────────────
    import config
    if args.symbol:
        config.SYMBOL = args.symbol
    if args.tf:
        config.TIMEFRAME = args.tf.upper()
    if args.setup:
        config.SETUP_TYPE = args.setup
    if args.tps:
        config.TPS_TYPE = args.tps
    if args.sideways_filter:
        config.SIDEWAYS_FILTER_ENABLED = (args.sideways_filter.lower() == "on")
    if args.htf:
        from indicators import timeframe_to_minutes
        base_mins = timeframe_to_minutes(config.TIMEFRAME)
        if args.htf % base_mins != 0:
            print(f"[ERROR] --htf {args.htf} ไม่หารลงตัวด้วย base TF {base_mins} นาที")
            sys.exit(1)
        config.TF_MULTIPLIER = args.htf // base_mins
        config.HTF_MINUTES   = args.htf
    if args.from_date:
        config.FROM_DATE = datetime.strptime(args.from_date, "%Y-%m-%d")
    if args.to_date:
        config.TO_DATE   = datetime.strptime(args.to_date, "%Y-%m-%d")

    setup_logging(args.log, config.LOG_FILE)
    log = logging.getLogger("main")
    log.info("ParanoidSignals™ 7.9-X Python Bot — mode=%s  symbol=%s  tf=%s",
             args.mode, config.SYMBOL, config.TIMEFRAME)

    # ─────────────────────────────────────────────────────────────────
    # BACKTEST MODE
    # ─────────────────────────────────────────────────────────────────
    if args.mode == "backtest":
        from backtest_engine import BacktestEngine

        engine = BacktestEngine()

        if args.csv:
            engine.load_from_csv(args.csv)
        else:
            from mt5_broker import MT5Broker
            broker = MT5Broker()
            if not broker.connect():
                log.critical("Cannot connect to MT5. "
                             "Ensure MT5 is running or use --csv to load data.")
                sys.exit(1)
            if args.from_date or args.to_date:
                from datetime import timezone as _tz
                from_dt = datetime.strptime(args.from_date, "%Y-%m-%d").replace(tzinfo=_tz.utc) if args.from_date else None
                to_dt   = datetime.strptime(args.to_date,   "%Y-%m-%d").replace(tzinfo=_tz.utc) if args.to_date   else None
                engine.load_from_mt5_range(broker, config.SYMBOL, config.TIMEFRAME, from_dt, to_dt)
            else:
                engine.load_from_mt5(broker, config.SYMBOL, config.TIMEFRAME, args.bars)
            broker.disconnect()

        result = engine.run(
            setup_type               = config.SETUP_TYPE,
            tps_type                 = config.TPS_TYPE,
            sideways_filter_enabled  = config.SIDEWAYS_FILTER_ENABLED,
        )
        result.print_report()

        if args.export:
            result.export_trades_csv(args.export)
            log.info("Trades exported to: %s", args.export)

        if args.chart:
            from visualizer import plot_results
            # Pass price data so the chart shows the actual price line
            try:
                df_price = engine._df
            except Exception:
                df_price = None
            plot_results(result, df_price)

    # ─────────────────────────────────────────────────────────────────
    # LIVE MODE
    # ─────────────────────────────────────────────────────────────────
    elif args.mode == "live":
        _acquire_lock()
        setup_logging(args.log, config.LOG_FILE)
        log = logging.getLogger("main")

        from live_trader import LiveTrader

        print("\n" + "=" * 52)
        print("  !! LIVE TRADING MODE -- Real money at risk !!")
        print("  Account  :", config.MT5_LOGIN)
        print("  Server   :", config.MT5_SERVER)
        print("  Symbol   :", config.SYMBOL)
        print("  Timeframe:", config.TIMEFRAME)
        print("  Setup    :", config.SETUP_TYPE)
        print("  TPS      :", config.TPS_TYPE)
        print("  Filter   : ADX+CHOP", "ON" if config.SIDEWAYS_FILTER_ENABLED else "OFF")
        print("  Lot size :", config.LOT_SIZE if config.LOT_SIZE > 0
              else f"{config.LOT_RISK_PCT}% risk")
        print("=" * 52)
        confirm = input("Type YES to start live trading: ").strip()
        if confirm != "YES":
            print("Aborted.")
            sys.exit(0)

        trader = LiveTrader(
            symbol                   = config.SYMBOL,
            timeframe                = config.TIMEFRAME,
            setup_type               = config.SETUP_TYPE,
            tps_type                 = config.TPS_TYPE,
            sideways_filter_enabled  = config.SIDEWAYS_FILTER_ENABLED,
        )
        trader.run()


if __name__ == "__main__":
    main()
