"""Run a task from the terminal, without the web UI."""

import json
import sys
import time
from pathlib import Path

from device import ADBDevice, DeviceManager
from engine import TASKS_DIR, load_task, run_task
from vision import find_template, probe_template

_DBG_LOG = Path(__file__).parent / ".cursor" / "debug-f028e7.log"


def _dbg(hypothesis_id: str, location: str, message: str, data: dict):
    # #region agent log
    try:
        payload = {
            "sessionId": "f028e7",
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        with open(_DBG_LOG, "a") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass
    # #endregion

DEFAULT_TASK = "hold_rally.yaml"
BLUESTACKS_PORT = 5555
GAME_PACKAGE = "com.gof.global"
PROMO_TEMPLATE = "promo_close.png"
WORLD_TEMPLATE = "world_btn.png"
MATCH_CONFIDENCE = 0.72
LOAD_TIMEOUT = 75
PROMO_TAPS = 8


def log(level: str, msg: str):
    print(f"[{level}] {msg}", flush=True)


def connect_device():
    dm = DeviceManager()
    dev = dm.get_current()
    if dev:
        log("info", f"Device: {dev.info().name}")
        return dev

    target = f"127.0.0.1:{BLUESTACKS_PORT}"
    log("info", f"No device yet, trying adb connect {target}")
    message = dm.connect_adb("127.0.0.1", BLUESTACKS_PORT)
    if message:
        log("info", message)
    dev = dm.get_current()
    if dev:
        log("info", f"Device: {dev.info().name}")
        return dev

    log("error", "No device connected. Start BlueStacks with ADB enabled, or run: adb connect 127.0.0.1:5555")
    return None


def _find(dev, template: str):
    return find_template(dev.screencap(), template, confidence=MATCH_CONFIDENCE)


def _close_promo_once(dev: ADBDevice, attempt: int) -> bool:
    """Tap one promo X. False means it is not on screen."""
    match = _find(dev, PROMO_TEMPLATE)
    if not match:
        return False
    x, y = match.center
    log("info", f"Closing promo ({attempt}/{PROMO_TAPS}) at ({x}, {y})")
    dev.tap(x, y)
    return True


def prepare_game(dev: ADBDevice, restart: bool):
    if restart:
        log("warn", f"Restarting {GAME_PACKAGE}")
        dev.force_stop(GAME_PACKAGE)
        time.sleep(3)
        dev.launch(GAME_PACKAGE)
    elif dev.foreground_package() != GAME_PACKAGE:
        log("info", "Game not in foreground, launching")
        dev.launch(GAME_PACKAGE)

    deadline = time.time() + LOAD_TIMEOUT
    log("info", "Waiting for World button")
    # #region agent log
    _dbg("D", "cli.py:prepare_game", "entered world wait", {
        "restart": restart,
        "foreground": dev.foreground_package(),
        "timeout_s": LOAD_TIMEOUT,
        "confidence": MATCH_CONFIDENCE,
    })
    # #endregion
    world = None
    promo_attempt = 0
    promo_enabled = True
    iteration = 0
    while time.time() < deadline:
        iteration += 1
        # #region agent log
        t0 = time.time()
        img = dev.screencap()
        cap_ms = int((time.time() - t0) * 1000)
        _, world_score = probe_template(img, WORLD_TEMPLATE, MATCH_CONFIDENCE)
        _, promo_score = probe_template(img, PROMO_TEMPLATE, MATCH_CONFIDENCE)
        if iteration <= 2 or iteration % 5 == 0:
            _dbg("A", "cli.py:prepare_game", "world wait poll", {
                "iteration": iteration,
                "cap_ms": cap_ms,
                "screen": [int(img.shape[1]), int(img.shape[0])],
                "world_score": round(float(world_score), 4),
                "promo_score": round(float(promo_score), 4),
                "promo_enabled": promo_enabled,
                "promo_attempt": promo_attempt,
                "threshold": MATCH_CONFIDENCE,
            })
        # #endregion
        if promo_enabled and promo_attempt < PROMO_TAPS:
            try:
                if _close_promo_once(dev, promo_attempt + 1):
                    promo_attempt += 1
                    # #region agent log
                    _dbg("B", "cli.py:prepare_game", "promo tap skipped world check", {
                        "iteration": iteration,
                        "promo_attempt": promo_attempt,
                    })
                    # #endregion
                    time.sleep(0.6)
                    continue
            except Exception as e:
                log("warn", f"Promo close failed, skipping: {e}")
                promo_enabled = False
        world = _find(dev, WORLD_TEMPLATE)
        if world:
            break
        time.sleep(2)
    # #region agent log
    _dbg("E", "cli.py:prepare_game", "world wait finished", {
        "found": world is not None,
        "iterations": iteration,
        "promo_attempt": promo_attempt,
        "promo_enabled": promo_enabled,
    })
    # #endregion
    if promo_enabled and promo_attempt == 0:
        log("info", "Promo close not found, skipping")
    if world:
        x, y = world.center
        log("info", f"Tapping World at ({x}, {y})")
        dev.tap(x, y)
        time.sleep(0.8)
    else:
        log("info", "World button not found, skipping")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    filename = args[0] if args else DEFAULT_TASK
    path = TASKS_DIR / filename
    if not path.exists():
        log("error", f"Task not found: {path}")
        return 1

    dev = connect_device()
    if not dev:
        return 1
    if not isinstance(dev, ADBDevice):
        log("error", "rally needs an ADB device")
        return 1

    task = load_task(path)
    restart = False
    while True:
        try:
            prepare_game(dev, restart)
            ok = run_task(task, dev, log=log)
        except KeyboardInterrupt:
            log("warn", "Stopped")
            return 130
        except Exception as e:
            log("error", str(e))
            ok = False
        if ok:
            return 0
        log("error", "Task failed, restarting game")
        restart = True


if __name__ == "__main__":
    raise SystemExit(main())
