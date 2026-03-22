import time
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

from device import BaseDevice
from vision import find_template

TASKS_DIR = Path(__file__).parent / "tasks"


@dataclass
class Step:
    action: str
    template: str = ""
    description: str = ""
    optional: bool = False
    confidence: float = 0.8
    seconds: float = 1.0
    x: int = 0
    y: int = 0
    x1: int = 0
    y1: int = 0
    x2: int = 0
    y2: int = 0
    duration_ms: int = 300
    filename: str = ""
    count: int = 1
    times: int = 1
    timeout: float = 10.0
    retries: int = 3
    retry_delay: float = 1.0


@dataclass
class Task:
    name: str
    description: str = ""
    icon: str = "default"
    steps: list[Step] = field(default_factory=list)
    filename: str = ""


LogCallback = Callable[[str, str], None]


def load_task(yaml_path: str | Path) -> Task:
    p = Path(yaml_path)
    with open(p) as f:
        data = yaml.safe_load(f)

    steps = []
    for s in data.get("steps", []):
        steps.append(Step(**{k: v for k, v in s.items() if k != "action"}, action=s["action"]))

    return Task(
        name=data.get("name", p.stem),
        description=data.get("description", ""),
        icon=data.get("icon", "default"),
        steps=steps,
        filename=p.name,
    )


def load_all_tasks(tasks_dir: Path = TASKS_DIR) -> list[Task]:
    if not tasks_dir.exists():
        return []
    tasks = []
    for p in sorted(tasks_dir.glob("*.yaml")):
        try:
            tasks.append(load_task(p))
        except Exception:
            pass
    return tasks


def save_task(task_data: dict, filename: str, tasks_dir: Path = TASKS_DIR) -> Path:
    tasks_dir.mkdir(parents=True, exist_ok=True)
    p = tasks_dir / filename
    with open(p, "w") as f:
        yaml.dump(task_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
    return p


def delete_task(filename: str, tasks_dir: Path = TASKS_DIR):
    p = tasks_dir / filename
    if p.exists():
        p.unlink()


def run_task(task: Task, dev: BaseDevice, log: LogCallback | None = None, stop_flag: Callable[[], bool] | None = None):
    def emit(level: str, msg: str):
        if log:
            log(level, msg)

    emit("info", f"Starting: {task.name}")

    for i, step in enumerate(task.steps):
        if stop_flag and stop_flag():
            emit("warn", "Task stopped by user")
            return

        if step.action == "find_and_tap":
            emit("info", f"Looking for: {step.template}")
            match = _find_with_retry(dev, step, emit)
            if match:
                cx, cy = match
                emit("success", f"Found at ({cx}, {cy})")
                dev.tap(cx, cy)
                emit("info", f"Tapped ({cx}, {cy})")
            elif step.optional:
                emit("warn", f"Not found (optional): {step.template} — skipping")
            else:
                emit("error", f"Not found: {step.template} — aborting task")
                return

        elif step.action == "wait":
            emit("info", f"Wait {step.seconds}s")
            time.sleep(step.seconds)

        elif step.action == "tap":
            emit("info", f"Tap ({step.x}, {step.y})")
            dev.tap(step.x, step.y)

        elif step.action == "swipe":
            emit("info", f"Swipe ({step.x1},{step.y1}) -> ({step.x2},{step.y2})")
            dev.swipe(step.x1, step.y1, step.x2, step.y2, step.duration_ms)

        elif step.action == "tap_back":
            emit("info", "Press back")
            dev.press_back()

        elif step.action == "screenshot":
            fname = step.filename or f"debug_{i}.png"
            p = Path("screenshots") / fname
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(dev.screencap())
            emit("info", f"Screenshot saved: {p}")

        elif step.action == "verify":
            emit("info", f"Verifying: {step.template}")
            found = _find_with_timeout(dev, step)
            if found:
                emit("success", f"Verified: {step.template}")
            else:
                emit("error", f"Verification failed: {step.template} — aborting")
                return

        elif step.action == "repeat":
            emit("info", f"Repeat previous {step.count} steps x{step.times}")
            if i >= step.count:
                repeat_steps = task.steps[i - step.count:i]
                repeat_task = Task(name=f"{task.name}_repeat", steps=repeat_steps)
                for _ in range(step.times):
                    run_task(repeat_task, dev, log, stop_flag)

        else:
            emit("warn", f"Unknown action: {step.action}")

        time.sleep(0.3)

    emit("success", f"Task completed: {task.name}")


def _find_with_retry(dev: BaseDevice, step: Step, emit) -> tuple[int, int] | None:
    for attempt in range(step.retries):
        png = dev.screencap()
        result = find_template(png, step.template, step.confidence)
        if result:
            return result.center
        if attempt < step.retries - 1:
            emit("info", f"Retry {attempt + 1}/{step.retries}...")
            time.sleep(step.retry_delay)
    return None


def _find_with_timeout(dev: BaseDevice, step: Step) -> bool:
    deadline = time.time() + step.timeout
    while time.time() < deadline:
        png = dev.screencap()
        result = find_template(png, step.template, step.confidence)
        if result:
            return True
        time.sleep(1)
    return False
