import subprocess
import shutil
import sys
import time
from pathlib import Path
from dataclasses import dataclass
from abc import ABC, abstractmethod

import numpy as np
import cv2

ADB_PATH = shutil.which("adb") or r"C:\Users\xalch\AppData\Local\Android\Sdk\platform-tools\adb.exe"
GPG_PORT = 6520

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

if IS_WINDOWS:
    import ctypes
    import ctypes.wintypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
            ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
            ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
            ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
            ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
            ("biClrImportant", ctypes.c_uint32),
        ]
else:
    user32 = None
    gdi32 = None
    BITMAPINFOHEADER = None


def _mac_bluestacks_window_candidates() -> list[dict]:
    """All plausible BlueStacks game viewports (points), largest first."""
    import Quartz
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
    )
    candidates = []
    seen = set()
    for w in wins:
        owner = w.get("kCGWindowOwnerName") or ""
        if "BlueStacks" not in owner:
            continue
        b = w.get("kCGWindowBounds") or {}
        width = float(b.get("Width", 0))
        height = float(b.get("Height", 0))
        area = width * height
        if width < 200 or height < 300 or area < 80_000:
            continue
        # Skip thin title/tool bars
        if height < 200 or width / max(height, 1) > 8:
            continue
        key = (round(b.get("X", 0)), round(b.get("Y", 0)), round(width), round(height))
        if key in seen:
            continue
        seen.add(key)
        aspect = width / height
        # Prefer portrait phone-like embeds; still keep landscape fullscreen hosts.
        portrait_bonus = 1.0 if 0.40 <= aspect <= 0.75 else 0.0
        # Prefer not-the-entire-desktop chrome when a smaller game pane exists
        desktop_penalty = 0.3 if width >= 1200 and height >= 700 else 1.0
        score = area * portrait_bonus * desktop_penalty + area * 0.01
        candidates.append((score, dict(b)))
    candidates.sort(key=lambda t: t[0], reverse=True)
    return [b for _, b in candidates]


def _mac_bluestacks_bounds() -> dict:
    """Best BlueStacks game window bounds (points).

    BlueStacks Air often omits windows from OnScreenOnly listings, so we scan
    all windows and pick a phone-like portrait frame (not the desktop-sized
    backing surface).
    """
    candidates = _mac_bluestacks_window_candidates()
    if not candidates:
        raise RuntimeError(
            "BlueStacks window not found for macOS click fallback — "
            "bring BlueStacks to the front and un-minimize it"
        )
    return candidates[0]


def _mac_pick_bounds_for_android(android: np.ndarray) -> tuple[dict, dict]:
    """Choose the BlueStacks window that best matches the ADB framebuffer."""
    global _MAC_CAL_CACHE
    candidates = _mac_bluestacks_window_candidates()
    if not candidates:
        raise RuntimeError(
            "BlueStacks window not found for macOS click fallback — "
            "bring BlueStacks to the front and un-minimize it"
        )

    # Prefer portrait phone panes first (fullscreen host is a last resort).
    def _rank(b: dict) -> tuple:
        w, h = float(b["Width"]), float(b["Height"])
        aspect = w / max(h, 1.0)
        portrait = 0 if 0.40 <= aspect <= 0.75 else 1
        return (portrait, -w * h)

    candidates = sorted(candidates, key=_rank)

    # Reuse a still-valid cache hit before probing every window.
    if _MAC_CAL_CACHE and _MAC_CAL_CACHE.get("usable"):
        key = _MAC_CAL_CACHE.get("key")
        if key:
            cached_bounds = {
                "X": float(key[0]), "Y": float(key[1]),
                "Width": float(key[2]), "Height": float(key[3]),
            }
            cal = _mac_calibrate(android, cached_bounds, force=True)
            if cal.get("usable"):
                return cached_bounds, cal

    best_cal = None
    best_bounds = candidates[0]
    for bounds in candidates[:6]:
        cal = _mac_calibrate(android, bounds, force=True)
        if best_cal is None or cal["conf"] > best_cal["conf"]:
            best_cal = cal
            best_bounds = bounds
        if cal.get("usable"):
            _MAC_CAL_CACHE = cal
            return bounds, cal

    if best_cal and best_cal.get("usable"):
        _MAC_CAL_CACHE = best_cal
    else:
        _MAC_CAL_CACHE = None
    return best_bounds, best_cal or {"conf": 0.0, "usable": False, "retina": 2.0,
                                     "embed_x": 0, "embed_y": 0, "embed_scale": 1.0}

def _mac_android_to_screen(ax: int, ay: int, aw: int, ah: int, bounds: dict) -> tuple[float, float]:
    """Naive letterbox mapping (fallback when calibration unavailable)."""
    wx, wy = float(bounds["X"]), float(bounds["Y"])
    ww, wh = float(bounds["Width"]), float(bounds["Height"])
    scale = min(ww / aw, wh / ah)
    gw, gh = aw * scale, ah * scale
    ox, oy = (ww - gw) / 2.0, (wh - gh) / 2.0
    return wx + ox + ax * scale, wy + oy + ay * scale


_MAC_CAL_CACHE: dict | None = None


def _mac_capture_window(bounds: dict) -> tuple[np.ndarray, float]:
    """Capture BlueStacks window pixels and return (BGR image, retina scale)."""
    import Quartz
    wins = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
    )
    win_id = Quartz.kCGNullWindowID
    for w in wins:
        if (w.get("kCGWindowOwnerName") or "") != "BlueStacks":
            continue
        b = w.get("kCGWindowBounds") or {}
        if (
            abs(float(b.get("X", 0)) - bounds["X"]) < 2
            and abs(float(b.get("Width", 0)) - bounds["Width"]) < 2
            and float(b.get("Height", 0)) > 300
        ):
            win_id = int(w.get("kCGWindowNumber"))
            break

    rect = Quartz.CGRectMake(bounds["X"], bounds["Y"], bounds["Width"], bounds["Height"])
    image = Quartz.CGWindowListCreateImage(
        rect,
        Quartz.kCGWindowListOptionIncludingWindow,
        win_id,
        Quartz.kCGWindowImageBoundsIgnoreFraming,
    )
    if image is None:
        image = Quartz.CGWindowListCreateImage(
            rect,
            Quartz.kCGWindowListOptionOnScreenOnly,
            Quartz.kCGNullWindowID,
            Quartz.kCGWindowImageDefault,
        )
    if image is None:
        raise RuntimeError("Failed to capture BlueStacks window")

    w = Quartz.CGImageGetWidth(image)
    h = Quartz.CGImageGetHeight(image)
    bytes_per_row = Quartz.CGImageGetBytesPerRow(image)
    data = Quartz.CGDataProviderCopyData(Quartz.CGImageGetDataProvider(image))
    buf = np.frombuffer(data, dtype=np.uint8)
    arr = buf.reshape((h, bytes_per_row // 4, 4))[:, :w, :]
    bgr = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
    retina = w / float(bounds["Width"])
    return bgr, retina


def _mac_calibrate(android: np.ndarray, bounds: dict, *, force: bool = False) -> dict:
    """Find where the Android framebuffer sits inside the BlueStacks window."""
    global _MAC_CAL_CACHE
    ah, aw = android.shape[:2]
    key = (round(bounds["X"]), round(bounds["Y"]), round(bounds["Width"]), round(bounds["Height"]), aw, ah)
    if not force and _MAC_CAL_CACHE and _MAC_CAL_CACHE.get("key") == key:
        return _MAC_CAL_CACHE

    win_img, retina = _mac_capture_window(bounds)
    wh, ww = win_img.shape[:2]
    best = (0.0, 0, 0, 0.0)
    # BlueStacks Air often embeds the Android framebuffer at ~0.65–0.75 of
    # window pixels; a 0.55 cap previously missed the true scale (~0.68) and
    # fell back to letterbox (embed_y=0), causing taps to land above thin buttons.
    for s in np.linspace(0.25, 0.99, 75):
        tw, th = int(aw * s), int(ah * s)
        if tw < 40 or th < 40 or tw >= ww or th >= wh:
            continue
        small = cv2.resize(android, (tw, th))
        result = cv2.matchTemplate(win_img, small, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(result)
        if mv > best[0]:
            best = (float(mv), int(ml[0]), int(ml[1]), float(s))

    conf, ex, ey, es = best
    usable = conf >= 0.50
    if not usable:
        # Do NOT letterbox-tap: wrong embed_y pans the game map when clicks miss UI.
        # Leave embed values present for logging only; caller must fall back to ADB.
        scale = min(ww / aw, wh / ah)
        es = scale
        ex = int((ww - aw * scale) / 2)
        ey = int((wh - ah * scale) / 2)

    cal = {
        "key": key,
        "embed_x": ex,
        "embed_y": ey,
        "embed_scale": es,
        "retina": retina,
        "conf": conf,
        "usable": usable,
    }
    # Only cache good calibrations — caching letterbox made every later tap miss.
    if usable:
        _MAC_CAL_CACHE = cal
    else:
        _MAC_CAL_CACHE = None
    return cal


def _mac_android_to_screen_calibrated(
    ax: int, ay: int, android: np.ndarray, bounds: dict, *, force: bool = False
) -> tuple[float, float, dict]:
    cal = _mac_calibrate(android, bounds, force=force)
    px = cal["embed_x"] + ax * cal["embed_scale"]
    py = cal["embed_y"] + ay * cal["embed_scale"]
    sx = float(bounds["X"]) + px / cal["retina"]
    sy = float(bounds["Y"]) + py / cal["retina"]
    return sx, sy, cal


def _mac_click(x: float, y: float):
    import Quartz
    from Quartz import (
        CGEventCreateMouseEvent, CGEventPost, CGPointMake,
        kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp,
        kCGMouseButtonLeft, kCGHIDEventTap,
    )
    subprocess.run(
        ["osascript", "-e", 'tell application "BlueStacks" to activate'],
        capture_output=True, timeout=5,
    )
    time.sleep(0.15)
    for etype in (kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseUp):
        ev = CGEventCreateMouseEvent(None, etype, CGPointMake(x, y), kCGMouseButtonLeft)
        CGEventPost(kCGHIDEventTap, ev)
        time.sleep(0.04)


def _mac_drag(x1: float, y1: float, x2: float, y2: float, duration_ms: int = 300):
    import Quartz
    from Quartz import (
        CGEventCreateMouseEvent, CGEventPost, CGPointMake,
        kCGEventMouseMoved, kCGEventLeftMouseDown, kCGEventLeftMouseDragged,
        kCGEventLeftMouseUp, kCGMouseButtonLeft, kCGHIDEventTap,
    )
    subprocess.run(
        ["osascript", "-e", 'tell application "BlueStacks" to activate'],
        capture_output=True, timeout=5,
    )
    time.sleep(0.1)
    CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kCGEventMouseMoved, CGPointMake(x1, y1), kCGMouseButtonLeft))
    CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kCGEventLeftMouseDown, CGPointMake(x1, y1), kCGMouseButtonLeft))
    steps = max(5, duration_ms // 20)
    for i in range(1, steps + 1):
        frac = i / steps
        ix = x1 + (x2 - x1) * frac
        iy = y1 + (y2 - y1) * frac
        CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kCGEventLeftMouseDragged, CGPointMake(ix, iy), kCGMouseButtonLeft))
        time.sleep(duration_ms / 1000 / steps)
    CGEventPost(kCGHIDEventTap, CGEventCreateMouseEvent(None, kCGEventLeftMouseUp, CGPointMake(x2, y2), kCGMouseButtonLeft))


def _mac_press_escape():
    import Quartz
    from Quartz import CGEventCreateKeyboardEvent, CGEventPost, kCGHIDEventTap
    subprocess.run(
        ["osascript", "-e", 'tell application "BlueStacks" to activate'],
        capture_output=True, timeout=5,
    )
    time.sleep(0.1)
    # 53 = Escape
    down = CGEventCreateKeyboardEvent(None, 53, True)
    up = CGEventCreateKeyboardEvent(None, 53, False)
    CGEventPost(kCGHIDEventTap, down)
    CGEventPost(kCGHIDEventTap, up)

@dataclass
class DeviceInfo:
    id: str
    name: str
    type: str
    connected: bool = True


class BaseDevice(ABC):
    @abstractmethod
    def screencap(self) -> np.ndarray:
        ...

    def screencap_png(self) -> bytes:
        img = self.screencap()
        _, buf = cv2.imencode(".png", img)
        return buf.tobytes()

    @abstractmethod
    def tap(self, x: int, y: int):
        ...

    @abstractmethod
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        ...

    @abstractmethod
    def press_back(self):
        ...

    @abstractmethod
    def info(self) -> DeviceInfo:
        ...


class WindowDevice(BaseDevice):
    """Windows-only Google Play Games window capture. Unavailable on macOS/Linux."""

    def __init__(self, title_substring: str = "Whiteout Survival"):
        if not IS_WINDOWS:
            raise RuntimeError("Window capture is only supported on Windows")
        self.title_sub = title_substring
        self._hwnd = None

    def _find_window(self) -> int:
        if self._hwnd and user32.IsWindow(self._hwnd):
            return self._hwnd

        result = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.wintypes.HWND, ctypes.wintypes.LPARAM)
        def enum_cb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextLengthW(hwnd)
            if length == 0:
                return True
            buf = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buf, length + 1)
            if self.title_sub.lower() in buf.value.lower():
                result.append(hwnd)
            return True

        user32.EnumWindows(enum_cb, 0)
        if not result:
            raise RuntimeError(f"Window not found: '{self.title_sub}'")
        self._hwnd = result[0]
        return self._hwnd

    def _get_client_rect(self, hwnd) -> tuple[int, int, int, int]:
        rect = ctypes.wintypes.RECT()
        user32.GetClientRect(hwnd, ctypes.byref(rect))
        pt = ctypes.wintypes.POINT(0, 0)
        ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return pt.x, pt.y, rect.right, rect.bottom

    def screencap(self) -> np.ndarray:
        hwnd = self._find_window()
        cx, cy, w, h = self._get_client_rect(hwnd)

        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        gdi32.SelectObject(hdc_mem, hbmp)
        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, cx, cy, 0x00CC0020)

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        return arr[:, :, :3].copy()

    def tap(self, x: int, y: int):
        hwnd = self._find_window()
        cx, cy, _, _ = self._get_client_rect(hwnd)
        screen_x = cx + x
        screen_y = cy + y

        import pyautogui
        prev_x, prev_y = pyautogui.position()
        pyautogui.click(screen_x, screen_y)
        time.sleep(0.05)
        pyautogui.moveTo(prev_x, prev_y)

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        hwnd = self._find_window()
        cx, cy, _, _ = self._get_client_rect(hwnd)

        import pyautogui
        pyautogui.moveTo(cx + x1, cy + y1)
        pyautogui.mouseDown()
        steps = max(5, duration_ms // 20)
        for i in range(1, steps + 1):
            frac = i / steps
            ix = int(x1 + (x2 - x1) * frac)
            iy = int(y1 + (y2 - y1) * frac)
            pyautogui.moveTo(cx + ix, cy + iy)
            time.sleep(duration_ms / 1000 / steps)
        pyautogui.mouseUp()

    def press_back(self):
        hwnd = self._find_window()
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.1)
        import pyautogui
        pyautogui.press("escape")

    def info(self) -> DeviceInfo:
        try:
            self._find_window()
            return DeviceInfo(id="window", name=f"Window: {self.title_sub}", type="window", connected=True)
        except RuntimeError:
            return DeviceInfo(id="window", name=f"Window: {self.title_sub}", type="window", connected=False)


class ADBDevice(BaseDevice):
    def __init__(self, serial: str, adb_path: str = ADB_PATH):
        self.serial = serial
        self.adb = adb_path
        self._screen_size: tuple[int, int] | None = None  # (w, h)
        self._prefer_mac_input = False

    def _run(self, args: list[str], raw: bool = False, retries: int = 3) -> bytes | str:
        last_err = ""
        for attempt in range(retries):
            cmd = [self.adb, "-s", self.serial] + args
            r = subprocess.run(cmd, capture_output=True, timeout=30)
            if r.returncode == 0:
                return r.stdout if raw else r.stdout.decode(errors="replace")

            last_err = r.stderr.decode(errors="replace").strip() or r.stdout.decode(errors="replace").strip()
            if raw and r.stdout:
                return r.stdout

            closed = "closed" in last_err.lower() or "offline" in last_err.lower() or "not found" in last_err.lower()
            if closed and attempt < retries - 1:
                self._recover_connection()
                time.sleep(0.5 + attempt)
                continue
            if not raw:
                raise RuntimeError(f"adb error: {last_err}")
            return r.stdout
        if not raw:
            raise RuntimeError(f"adb error: {last_err}")
        return b""

    def _recover_connection(self):
        """BlueStacks often drops shell while still listing as 'device'."""
        if ":" in self.serial and not self.serial.startswith("emulator"):
            subprocess.run([self.adb, "disconnect", self.serial], capture_output=True, timeout=10)
            subprocess.run([self.adb, "connect", self.serial], capture_output=True, timeout=10)
        else:
            subprocess.run([self.adb, "reconnect"], capture_output=True, timeout=10)

    def _ensure_screen_size(self):
        if self._screen_size is None:
            img = self.screencap()
            h, w = img.shape[:2]
            self._screen_size = (w, h)

    def _mac_tap(self, x: int, y: int):
        # Prefer a fresh frame for calibration so chrome/sidebar offsets stay accurate
        android = self.screencap()
        h, w = android.shape[:2]
        self._screen_size = (w, h)
        subprocess.run(
            ["osascript", "-e", 'tell application "BlueStacks" to activate'],
            capture_output=True, timeout=5,
        )
        time.sleep(0.35)
        bounds, cal = _mac_pick_bounds_for_android(android)
        sx = float(bounds["X"]) + (cal["embed_x"] + x * cal["embed_scale"]) / cal["retina"]
        sy = float(bounds["Y"]) + (cal["embed_y"] + y * cal["embed_scale"]) / cal["retina"]
        if not cal.get("usable"):
            # Overlay (browser/video fullscreen) may be covering BlueStacks — retry once.
            time.sleep(0.5)
            android = self.screencap()
            bounds, cal = _mac_pick_bounds_for_android(android)
            sx = float(bounds["X"]) + (cal["embed_x"] + x * cal["embed_scale"]) / cal["retina"]
            sy = float(bounds["Y"]) + (cal["embed_y"] + y * cal["embed_scale"]) / cal["retina"]
        if not cal.get("usable"):
            # Bad Mac mapping pans the wilderness map; use ADB instead.
            self._prefer_mac_input = False
            self._run(["shell", "input", "tap", str(x), str(y)], retries=2)
            return
        _mac_click(sx, sy)

    def _mac_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        android = self.screencap()
        h, w = android.shape[:2]
        self._screen_size = (w, h)
        subprocess.run(
            ["osascript", "-e", 'tell application "BlueStacks" to activate'],
            capture_output=True, timeout=5,
        )
        time.sleep(0.35)
        bounds, cal = _mac_pick_bounds_for_android(android)
        if not cal.get("usable"):
            self._prefer_mac_input = False
            self._run([
                "shell", "input", "swipe",
                str(x1), str(y1), str(x2), str(y2), str(duration_ms),
            ], retries=2)
            return
        s1x = float(bounds["X"]) + (cal["embed_x"] + x1 * cal["embed_scale"]) / cal["retina"]
        s1y = float(bounds["Y"]) + (cal["embed_y"] + y1 * cal["embed_scale"]) / cal["retina"]
        s2x = float(bounds["X"]) + (cal["embed_x"] + x2 * cal["embed_scale"]) / cal["retina"]
        s2y = float(bounds["Y"]) + (cal["embed_y"] + y2 * cal["embed_scale"]) / cal["retina"]
        _mac_drag(s1x, s1y, s2x, s2y, duration_ms)

    def screencap(self) -> np.ndarray:
        raw = self._run(["exec-out", "screencap", "-p"], raw=True)
        arr = np.frombuffer(raw, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            raise RuntimeError("Failed to decode ADB screenshot")
        h, w = img.shape[:2]
        self._screen_size = (w, h)
        return img

    def tap(self, x: int, y: int):
        if IS_MAC and self._prefer_mac_input:
            self._mac_tap(x, y)
            return
        try:
            self._run(["shell", "input", "tap", str(x), str(y)], retries=2)
        except RuntimeError as e:
            if IS_MAC and "closed" in str(e).lower():
                self._prefer_mac_input = True
                self._mac_tap(x, y)
            else:
                raise

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        if IS_MAC and self._prefer_mac_input:
            self._mac_swipe(x1, y1, x2, y2, duration_ms)
            return
        try:
            self._run(
                ["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)],
                retries=2,
            )
        except RuntimeError as e:
            if IS_MAC and "closed" in str(e).lower():
                self._prefer_mac_input = True
                self._mac_swipe(x1, y1, x2, y2, duration_ms)
            else:
                raise

    def press_back(self):
        if IS_MAC and self._prefer_mac_input:
            _mac_press_escape()
            return
        try:
            self._run(["shell", "input", "keyevent", "KEYCODE_BACK"], retries=2)
        except RuntimeError as e:
            if IS_MAC and "closed" in str(e).lower():
                self._prefer_mac_input = True
                _mac_press_escape()
            else:
                raise

    def info(self) -> DeviceInfo:
        return DeviceInfo(id=self.serial, name=f"ADB: {self.serial}", type="adb", connected=True)


class DeviceManager:
    def __init__(self, adb_path: str = ADB_PATH):
        self.adb_path = adb_path
        self._current: BaseDevice | None = None

    def list_devices(self) -> list[DeviceInfo]:
        devices = []

        if IS_WINDOWS:
            try:
                win = WindowDevice()
                win._find_window()
                devices.append(win.info())
            except RuntimeError:
                pass

        try:
            cmd = [self.adb_path, "devices", "-l"]
            r = subprocess.run(cmd, capture_output=True, timeout=10)
            for line in r.stdout.decode(errors="replace").strip().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == "device":
                    serial = parts[0]
                    model = ""
                    for p in parts[2:]:
                        if p.startswith("model:"):
                            model = p.split(":", 1)[1]
                    devices.append(DeviceInfo(
                        id=serial,
                        name=model or serial,
                        type="adb",
                    ))
        except Exception:
            pass

        return devices

    def get_device(self, device_id: str) -> BaseDevice:
        if device_id == "window":
            if not IS_WINDOWS:
                raise RuntimeError("Window capture is only supported on Windows; use ADB on macOS")
            return WindowDevice()
        return ADBDevice(device_id, self.adb_path)

    def get_current(self) -> BaseDevice | None:
        if self._current:
            return self._current
        devices = self.list_devices()
        if devices:
            self._current = self.get_device(devices[0].id)
        return self._current

    def set_current(self, device_id: str):
        self._current = self.get_device(device_id)

    def connect_adb(self, host: str = "localhost", port: int = GPG_PORT) -> str:
        cmd = [self.adb_path, "connect", f"{host}:{port}"]
        r = subprocess.run(cmd, capture_output=True, timeout=10)
        return r.stdout.decode(errors="replace").strip()
