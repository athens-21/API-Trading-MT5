"""
watchdog.py — Auto-restart live bot ถ้า crash

วิธีใช้:
    ดับเบิลคลิก START_WATCHDOG.bat
    (หรือ: python watchdog.py)

สิ่งที่ทำ:
  - เปิด live bot ทันที
  - Auto-restart ถ้า crash (สูงสุด MAX_RESTARTS ครั้ง)
  - ส่ง Discord alert ทุกครั้งที่ crash / restart
  - ถ้า bot หยุดด้วย Ctrl+C หรือ STOP_BOT.bat → watchdog หยุดด้วย
"""

import subprocess
import sys
import os
import time
import logging
import ctypes
import signal
import atexit
from datetime import datetime

# ── Force UTF-8 ────────────────────────────────────────────────────────────
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ── Windows Sleep Prevention ──────────────────────────────────────────────
_ES_CONTINUOUS        = 0x80000000
_ES_SYSTEM_REQUIRED   = 0x00000001
_ES_AWAYMODE_REQUIRED = 0x00000040

def _prevent_sleep():
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(
            _ES_CONTINUOUS | _ES_SYSTEM_REQUIRED | _ES_AWAYMODE_REQUIRED
        )
    except Exception:
        pass

def _allow_sleep():
    try:
        ctypes.windll.kernel32.SetThreadExecutionState(_ES_CONTINUOUS)
    except Exception:
        pass


# ── Config ─────────────────────────────────────────────────────────────────
MAX_RESTARTS      = 10    # หยุดหลัง crash ติดกันกี่ครั้ง
RESET_WINDOW_SEC  = 600   # ถ้าทำงานนานกว่านี้ → reset crash counter
RESTART_DELAY_SEC = 10    # รอกี่วินาทีก่อน restart

# Bot command — อ่านค่าทั้งหมดจาก config.py อัตโนมัติ
BOT_ARGS = [sys.executable, "main.py", "--mode", "live"]

WORK_DIR = os.path.dirname(os.path.abspath(__file__))
YES_FILE = os.path.join(WORK_DIR, "yes_input.txt")

# ── Logging ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(WORK_DIR, "watchdog.log"), encoding="utf-8"),
    ],
)
log = logging.getLogger("watchdog")


# ── Discord helper ─────────────────────────────────────────────────────────

def _discord_alert(msg: str):
    try:
        sys.path.insert(0, WORK_DIR)
        from discord_notifier import notify_error
        notify_error(msg)
    except Exception:
        pass


def _discord_alert_pid(pid: int):
    try:
        sys.path.insert(0, WORK_DIR)
        from discord_notifier import notify_pid
        notify_pid(pid)
    except Exception:
        pass


# ── Active subprocess reference ────────────────────────────────────────────
_active_proc: subprocess.Popen = None


# ── Graceful shutdown ──────────────────────────────────────────────────────

def _handle_stop(sig, frame):
    log.info("Watchdog stop signal received — shutting down …")
    if _active_proc is not None:
        try:
            _active_proc.terminate()
        except Exception:
            pass
    _allow_sleep()
    sys.exit(0)

signal.signal(signal.SIGINT,  _handle_stop)
signal.signal(signal.SIGTERM, _handle_stop)


# ── Helpers ────────────────────────────────────────────────────────────────

def _ensure_yes_file():
    try:
        with open(YES_FILE, "w") as f:
            f.write("YES\n")
    except Exception as e:
        log.warning("Cannot write yes_input.txt: %s — retrying in 5s", e)
        time.sleep(5)
        with open(YES_FILE, "w") as f:
            f.write("YES\n")

def _pid_alive(pid: int) -> bool:
    try:
        import psutil
        return psutil.pid_exists(pid)
    except Exception:
        pass
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _remove_stale_lock():
    """Remove live_bot.lock only if the recorded PID is no longer running."""
    lock = os.path.join(WORK_DIR, "live_bot.lock")
    if not os.path.exists(lock):
        return
    try:
        with open(lock) as f:
            pid = int(f.read().strip())
        if _pid_alive(pid):
            log.error("Bot already running (PID %d) — หยุดก่อนด้วย STOP_BOT.bat", pid)
            _allow_sleep()
            sys.exit(1)
        os.remove(lock)
        log.info("Removed stale lock file (PID %d no longer running).", pid)
    except (ValueError, OSError) as e:
        log.warning("Could not process lock file: %s", e)
        try:
            os.remove(lock)
        except OSError:
            pass


# ── Main ───────────────────────────────────────────────────────────────────

def run():
    global _active_proc

    # บันทึก PID ของ watchdog เพื่อให้ STOP_BOT.bat ปิดได้
    wd_lock = os.path.join(WORK_DIR, "watchdog.lock")
    try:
        with open(wd_lock, "w") as f:
            f.write(str(os.getpid()))
    except Exception as e:
        log.warning("Cannot write watchdog.lock: %s (continuing anyway)", e)
    atexit.register(lambda: os.remove(wd_lock) if os.path.exists(wd_lock) else None)

    _prevent_sleep()
    log.info("Watchdog started — เริ่ม bot ทันที")
    log.info("ปิดทุกอย่าง: ดับเบิลคลิก STOP_BOT.bat หรือกด Ctrl+C")

    crash_count = 0

    while True:
        if crash_count >= MAX_RESTARTS:
            msg = f"Bot crash {crash_count} ครั้งติดกัน — หยุด auto-restart. เปิด START_WATCHDOG.bat ใหม่เพื่อลองอีกครั้ง"
            log.error(msg)
            _discord_alert(msg)
            break

        try:
            _ensure_yes_file()
            _remove_stale_lock()
        except SystemExit:
            raise   # ยอมให้ sys.exit() ผ่านได้ (กรณี bot already running)
        except Exception as e:
            log.error("Pre-start error: %s — retrying in %ds", e, RESTART_DELAY_SEC)
            time.sleep(RESTART_DELAY_SEC)
            continue

        log.info("Starting bot …%s", f" (restart #{crash_count})" if crash_count else "")
        t_start = time.time()

        try:
            yes_fh = open(YES_FILE)
            proc = subprocess.Popen(
                BOT_ARGS,
                cwd=WORK_DIR,
                stdin=yes_fh,
                env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
        except Exception as e:
            log.error("Cannot launch bot: %s — retrying in %ds", e, RESTART_DELAY_SEC)
            time.sleep(RESTART_DELAY_SEC)
            crash_count += 1
            continue

        _active_proc = proc
        log.info("Bot process started — PID %d", proc.pid)
        _discord_alert_pid(proc.pid)
        try:
            proc.wait()
        except Exception as e:
            log.error("proc.wait() failed: %s", e)
        _active_proc = None

        elapsed = time.time() - t_start
        rc      = proc.returncode

        # หยุดด้วย Ctrl+C / STOP_BOT.bat / graceful stop → ไม่ restart
        if rc == 0:
            log.info("Bot stopped cleanly (rc=0). Watchdog stopping.")
            break

        # Crash
        if elapsed > RESET_WINDOW_SEC:
            crash_count = 0

        crash_count += 1
        msg = (f"⚠️ Bot crashed (rc={rc}, ran {elapsed:.0f}s) — "
               f"restarting in {RESTART_DELAY_SEC}s … "
               f"(crash {crash_count}/{MAX_RESTARTS})")
        log.warning(msg)
        _discord_alert(msg)
        time.sleep(RESTART_DELAY_SEC)


if __name__ == "__main__":
    try:
        run()
    except SystemExit:
        pass   # sys.exit() ปกติ
    except Exception as e:
        log.critical("Watchdog itself crashed: %s", e, exc_info=True)
        _discord_alert(f"🔴 WATCHDOG CRASHED: {e}")
    finally:
        _allow_sleep()
