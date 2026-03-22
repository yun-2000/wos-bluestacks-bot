import subprocess
import shutil
import time
import ctypes
import ctypes.wintypes
from pathlib import Path
from dataclasses import dataclass
from abc import ABC, abstractmethod
from io import BytesIO

import numpy as np

ADB_PATH = shutil.which("adb") or r"C:\Users\xalch\AppData\Local\Android\Sdk\platform-tools\adb.exe"
GPG_PORT = 6520

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


@dataclass
class DeviceInfo:
    id: str
    name: str
    type: str
    connected: bool = True


class BaseDevice(ABC):
    @abstractmethod
    def screencap(self) -> bytes:
        ...

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
    def __init__(self, title_substring: str = "Whiteout Survival"):
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

    def screencap(self) -> bytes:
        hwnd = self._find_window()
        cx, cy, w, h = self._get_client_rect(hwnd)

        hdc_screen = user32.GetDC(0)
        hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
        hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
        gdi32.SelectObject(hdc_mem, hbmp)

        gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, cx, cy, 0x00CC0020)

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
                ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
                ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
                ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
                ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
                ("biClrImportant", ctypes.c_uint32),
            ]

        bmi = BITMAPINFOHEADER()
        bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bmi.biWidth = w
        bmi.biHeight = -h
        bmi.biPlanes = 1
        bmi.biBitCount = 32
        bmi.biCompression = 0

        buf_size = w * h * 4
        buf = ctypes.create_string_buffer(buf_size)
        gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi), 0)

        gdi32.DeleteObject(hbmp)
        gdi32.DeleteDC(hdc_mem)
        user32.ReleaseDC(0, hdc_screen)

        import cv2
        arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
        bgr = arr[:, :, :3].copy()
        _, png = cv2.imencode(".png", bgr)
        return png.tobytes()

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

    def _run(self, args: list[str], raw: bool = False) -> bytes | str:
        cmd = [self.adb, "-s", self.serial] + args
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        if r.returncode != 0 and not raw:
            raise RuntimeError(f"adb error: {r.stderr.decode(errors='replace')}")
        return r.stdout if raw else r.stdout.decode(errors="replace")

    def screencap(self) -> bytes:
        return self._run(["exec-out", "screencap", "-p"], raw=True)

    def tap(self, x: int, y: int):
        self._run(["shell", "input", "tap", str(x), str(y)])

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        self._run(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)])

    def press_back(self):
        self._run(["shell", "input", "keyevent", "KEYCODE_BACK"])

    def info(self) -> DeviceInfo:
        return DeviceInfo(id=self.serial, name=f"ADB: {self.serial}", type="adb", connected=True)


class DeviceManager:
    def __init__(self, adb_path: str = ADB_PATH):
        self.adb_path = adb_path
        self._current: BaseDevice | None = None

    def list_devices(self) -> list[DeviceInfo]:
        devices = []

        win = WindowDevice()
        try:
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
