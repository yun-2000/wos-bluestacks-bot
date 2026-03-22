import subprocess
import shutil
from pathlib import Path
from dataclasses import dataclass

ADB_PATH = shutil.which("adb") or r"C:\Users\xalch\AppData\Local\Android\Sdk\platform-tools\adb.exe"
GPG_PORT = 6520


@dataclass
class Device:
    serial: str
    state: str
    name: str = ""

    @property
    def display_name(self):
        return self.name or self.serial


class ADB:
    def __init__(self, adb_path: str = ADB_PATH):
        self.adb = adb_path

    def _run(self, args: list[str], device: str | None = None, raw: bool = False) -> bytes | str:
        cmd = [self.adb]
        if device:
            cmd += ["-s", device]
        cmd += args
        r = subprocess.run(cmd, capture_output=True, timeout=30)
        if r.returncode != 0 and not raw:
            raise RuntimeError(f"adb {' '.join(args)} failed: {r.stderr.decode(errors='replace')}")
        return r.stdout if raw else r.stdout.decode(errors="replace")

    def list_devices(self) -> list[Device]:
        out = self._run(["devices", "-l"])
        devices = []
        for line in out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2:
                serial, state = parts[0], parts[1]
                model = ""
                for p in parts[2:]:
                    if p.startswith("model:"):
                        model = p.split(":", 1)[1]
                name = "Google Play Games" if "localhost" in serial or "emulator" in serial else model or serial
                devices.append(Device(serial=serial, state=state, name=name))
        return devices

    def connect_gpg(self) -> str:
        out = self._run(["connect", f"localhost:{GPG_PORT}"])
        return out.strip()

    def screencap(self, device: str) -> bytes:
        return self._run(["exec-out", "screencap", "-p"], device=device, raw=True)

    def tap(self, device: str, x: int, y: int):
        self._run(["shell", "input", "tap", str(x), str(y)], device=device)

    def swipe(self, device: str, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300):
        self._run(["shell", "input", "swipe", str(x1), str(y1), str(x2), str(y2), str(duration_ms)], device=device)

    def press_back(self, device: str):
        self._run(["shell", "input", "keyevent", "KEYCODE_BACK"], device=device)

    def press_home(self, device: str):
        self._run(["shell", "input", "keyevent", "KEYCODE_HOME"], device=device)

    def shell(self, device: str, cmd: str) -> str:
        return self._run(["shell", cmd], device=device)

    def save_screencap(self, device: str, path: str | Path) -> Path:
        data = self.screencap(device)
        p = Path(path)
        p.write_bytes(data)
        return p
