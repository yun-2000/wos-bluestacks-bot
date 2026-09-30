import subprocess
import shutil
import sys
import time
from dataclasses import dataclass
from abc import ABC, abstractmethod

import numpy as np
import cv2

ADB_PATH = shutil.which("adb") or r"C:\Users\xalch\AppData\Local\Android\Sdk\platform-tools\adb.exe"
GPG_PORT = 6520

IS_WINDOWS = sys.platform == "win32"

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
    """Pure ADB device — no Mac/Win screen-coordinate click fallback.

    Screenshot and input go through `adb` only, so BlueStacks may be minimized
    or hidden as long as the emulator process stays running and ADB stays up.
    """

    # Common local ADB endpoints (BlueStacks / GPG) tried during reconnect.
    _LOCAL_ADB_PORTS = (5555, 5556, 5565, 5575, 5585, 6520)

    def __init__(self, serial: str, adb_path: str = ADB_PATH):
        self.serial = serial
        self.adb = adb_path
        self._screen_size: tuple[int, int] | None = None  # (w, h)

    def _transient_adb_error(self, err: str) -> bool:
        e = err.lower()
        return any(
            token in e
            for token in (
                "closed",
                "offline",
                "not found",
                "device offline",
                "connection reset",
                "connection refused",
                "no device",
                "error: closed",
                "cannot connect",
            )
        )

    def _run(self, args: list[str], raw: bool = False, retries: int = 5) -> bytes | str:
        last_err = ""
        for attempt in range(retries):
            cmd = [self.adb, "-s", self.serial] + args
            r = subprocess.run(cmd, capture_output=True, timeout=30)
            if r.returncode == 0:
                return r.stdout if raw else r.stdout.decode(errors="replace")

            last_err = r.stderr.decode(errors="replace").strip() or r.stdout.decode(errors="replace").strip()
            if raw and r.stdout:
                return r.stdout

            if self._transient_adb_error(last_err) and attempt < retries - 1:
                self._recover_connection()
                time.sleep(0.5 + attempt * 0.5)
                continue
            if not raw:
                raise RuntimeError(f"adb error: {last_err}")
            return r.stdout
        if not raw:
            raise RuntimeError(f"adb error: {last_err}")
        return b""

    def _device_ready(self, serial: str | None = None) -> bool:
        target = serial or self.serial
        r = subprocess.run(
            [self.adb, "-s", target, "get-state"],
            capture_output=True,
            timeout=10,
        )
        return r.returncode == 0 and r.stdout.decode(errors="replace").strip() == "device"

    def _recover_connection(self):
        """Re-establish ADB after BlueStacks drops the shell (common when backgrounded)."""
        if ":" in self.serial and not self.serial.startswith("emulator"):
            subprocess.run([self.adb, "disconnect", self.serial], capture_output=True, timeout=10)
            subprocess.run([self.adb, "connect", self.serial], capture_output=True, timeout=10)
            if self._device_ready():
                return
            # Original host:port failed — probe common local emulator ports.
            host = self.serial.rsplit(":", 1)[0]
            for port in self._LOCAL_ADB_PORTS:
                candidate = f"{host}:{port}"
                if candidate == self.serial:
                    continue
                subprocess.run([self.adb, "connect", candidate], capture_output=True, timeout=10)
                if self._device_ready(candidate):
                    self.serial = candidate
                    return
        else:
            subprocess.run([self.adb, "reconnect"], capture_output=True, timeout=10)
            if self._device_ready():
                return
            # USB/emulator serial vanished — try TCP reconnect to localhost BlueStacks.
            for port in self._LOCAL_ADB_PORTS:
                candidate = f"127.0.0.1:{port}"
                subprocess.run([self.adb, "connect", candidate], capture_output=True, timeout=10)
                if self._device_ready(candidate):
                    self.serial = candidate
                    return

    def _ensure_screen_size(self):
        if self._screen_size is None:
            img = self.screencap()
            h, w = img.shape[:2]
            self._screen_size = (w, h)

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
        self._run(["shell", "input", "tap", str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        self._run([
            "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(duration_ms),
        ])

    def press_back(self):
        self._run(["shell", "input", "keyevent", "KEYCODE_BACK"])

    def force_stop(self, package: str):
        self._run(["shell", "am", "force-stop", package])

    def launch(self, package: str):
        self._run([
            "shell", "monkey", "-p", package,
            "-c", "android.intent.category.LAUNCHER", "1",
        ])

    def foreground_package(self) -> str:
        out = self._run(["shell", "dumpsys", "activity", "top"])
        for line in out.splitlines()[:40]:
            if "ACTIVITY" not in line:
                continue
            for part in line.split():
                if "/" in part and "." in part:
                    return part.split("/", 1)[0]
        return ""

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
