import time
import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import Callable

from device import BaseDevice
from vision import find_template, probe_template

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
    ignore_badge: bool = False
    task_file: str = ""
    templates: list[str] = field(default_factory=list)
    max_loops: int = 0
    loop_delay: float = 1.0
    tap_y_offset: int = 0
    then_steps: list["Step"] = field(default_factory=list)
    else_steps: list["Step"] = field(default_factory=list)

    def to_dict(self) -> dict:
        from dataclasses import asdict
        d = asdict(self)
        d["then_steps"] = [s.to_dict() for s in self.then_steps]
        d["else_steps"] = [s.to_dict() for s in self.else_steps]
        return d


@dataclass
class Task:
    name: str
    description: str = ""
    icon: str = "default"
    steps: list[Step] = field(default_factory=list)
    filename: str = ""


LogCallback = Callable[[str, str], None]


def _parse_step(s: dict) -> Step:
    then_raw = s.pop("then", None) or []
    else_raw = s.pop("else", None) or []
    step = Step(**{k: v for k, v in s.items() if k != "action"}, action=s["action"])
    step.then_steps = [_parse_step(t) for t in then_raw]
    step.else_steps = [_parse_step(t) for t in else_raw]
    return step


def load_task(yaml_path: str | Path) -> Task:
    p = Path(yaml_path)
    with open(p) as f:
        data = yaml.safe_load(f)

    steps = [_parse_step(dict(s)) for s in data.get("steps", [])]

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
    ok = _run_steps(task.steps, dev, emit, stop_flag)
    if ok:
        emit("success", f"Task completed: {task.name}")
    else:
        emit("error", f"Task failed: {task.name}")


def _run_steps(steps: list[Step], dev: BaseDevice, emit: LogCallback, stop_flag: Callable[[], bool] | None = None) -> bool:
    for i, step in enumerate(steps):
        if stop_flag and stop_flag():
            emit("warn", "Task stopped by user")
            return False

        if step.action == "find_and_tap":
            emit("info", f"Looking for: {step.template}")
            match, best_score = _find_with_retry(dev, step, emit)
            if match:
                cx, cy = match
                if step.tap_y_offset:
                    cy += step.tap_y_offset
                emit("success", f"Found at ({cx}, {cy})" + (
                    f" (y+{step.tap_y_offset})" if step.tap_y_offset else ""
                ))
                dev.tap(cx, cy)
            elif step.optional:
                emit("warn", f"Not found (optional): {step.template} — skipping")
            else:
                emit("error", f"Not found: {step.template} — aborting")
                return False

        elif step.action == "if_found":
            emit("info", f"Checking: {step.template}")
            img = dev.screencap()
            result = find_template(img, step.template, step.confidence, ignore_badge=step.ignore_badge)
            if result:
                emit("success", f"Found {step.template} — THEN")
                if not _run_steps(step.then_steps, dev, emit, stop_flag):
                    return False
            else:
                emit("warn", f"Not found {step.template} — ELSE")
                if not _run_steps(step.else_steps, dev, emit, stop_flag):
                    return False

        elif step.action == "run_task":
            emit("info", f"Running sub-task: {step.task_file}")
            sub = load_task(TASKS_DIR / step.task_file)
            if not _run_steps(sub.steps, dev, emit, stop_flag):
                return False

        elif step.action == "loop":
            label = step.description or "Loop"
            iteration = 0
            while True:
                if stop_flag and stop_flag():
                    return False
                if 0 < step.max_loops <= iteration:
                    break
                iteration += 1
                suffix = f"/{step.max_loops}" if step.max_loops else ""
                emit("info", f"{label} [{iteration}{suffix}]")
                if not _run_steps(step.then_steps, dev, emit, stop_flag):
                    return False
                time.sleep(step.loop_delay)
            emit("info", f"{label} ended after {iteration} iterations")

        elif step.action == "loop_until_found":
            label = step.description or f"Wait for {step.template}"
            iteration = 0
            found = False
            while True:
                if stop_flag and stop_flag():
                    return False
                if 0 < step.max_loops <= iteration:
                    break
                iteration += 1
                img = dev.screencap()
                result = find_template(img, step.template, step.confidence, ignore_badge=step.ignore_badge)
                if result:
                    emit("success", f"{label} — found after {iteration} checks")
                    if step.then_steps:
                        if not _run_steps(step.then_steps, dev, emit, stop_flag):
                            return False
                    found = True
                    break
                suffix = f"/{step.max_loops}" if step.max_loops else ""
                emit("info", f"{label} [{iteration}{suffix}] waiting...")
                time.sleep(step.loop_delay)
            if not found:
                emit("error", f"{label} — not found after {iteration} checks — aborting")
                return False

        elif step.action == "loop_templates":
            label = step.description or "Loop templates"
            iteration = 0
            while True:
                if stop_flag and stop_flag():
                    return False
                if 0 < step.max_loops <= iteration:
                    break
                iteration += 1
                img = dev.screencap()
                found_any = False
                for tpl in step.templates:
                    result = find_template(img, tpl, step.confidence, ignore_badge=step.ignore_badge)
                    if result:
                        cx, cy = result.center
                        emit("success", f"[{iteration}] {tpl} at ({cx},{cy})")
                        dev.tap(cx, cy)
                        found_any = True
                        time.sleep(0.5)
                        img = dev.screencap()
                if not found_any:
                    emit("info", f"[{iteration}] No templates matched")
                time.sleep(step.loop_delay)
            emit("info", f"{label} ended after {iteration} iterations")

        elif step.action == "wait":
            label = step.description or f"Wait {step.seconds}s"
            emit("info", label if step.description else f"Wait {step.seconds}s")
            deadline = time.time() + step.seconds
            while time.time() < deadline:
                if stop_flag and stop_flag():
                    emit("warn", "Task stopped by user")
                    return False
                time.sleep(min(0.5, max(0.0, deadline - time.time())))

        elif step.action == "tap":
            emit("info", f"Tap ({step.x}, {step.y})")
            dev.tap(step.x, step.y)

        elif step.action == "swipe":
            emit("info", f"Swipe ({step.x1},{step.y1}) -> ({step.x2},{step.y2})")
            dev.swipe(step.x1, step.y1, step.x2, step.y2, step.duration_ms)

        elif step.action == "tap_dismiss":
            emit("info", "Dismiss modal — press back")
            dev.press_back()

        elif step.action == "tap_back":
            emit("info", "Press back")
            dev.press_back()

        elif step.action == "screenshot":
            fname = step.filename or f"debug_{i}.png"
            p = Path("screenshots") / fname
            p.parent.mkdir(exist_ok=True)
            p.write_bytes(dev.screencap_png())
            emit("info", f"Screenshot saved: {p}")

        elif step.action == "verify":
            emit("info", f"Verifying: {step.template}")
            found = _find_with_timeout(dev, step)
            if found:
                emit("success", f"Verified: {step.template}")
            else:
                emit("error", f"Verification failed: {step.template} — aborting")
                return False

        elif step.action == "repeat":
            emit("info", f"Repeat previous {step.count} steps x{step.times}")
            if i >= step.count:
                repeat_steps = steps[i - step.count:i]
                for _ in range(step.times):
                    if not _run_steps(repeat_steps, dev, emit, stop_flag):
                        return False

        else:
            emit("warn", f"Unknown action: {step.action}")

        time.sleep(0.1)

    return True


def _find_with_retry(dev: BaseDevice, step: Step, emit) -> tuple[tuple[int, int] | None, float]:
    best_score = 0.0
    for attempt in range(step.retries):
        img = dev.screencap()
        result, score = probe_template(
            img, step.template, step.confidence, ignore_badge=step.ignore_badge
        )
        best_score = max(best_score, score)
        if result:
            return result.center, score
        if attempt < step.retries - 1:
            emit("info", f"Retry {attempt + 1}/{step.retries}...")
            time.sleep(step.retry_delay)
    return None, best_score


def _find_with_timeout(dev: BaseDevice, step: Step) -> bool:
    deadline = time.time() + step.timeout
    while time.time() < deadline:
        img = dev.screencap()
        result = find_template(img, step.template, step.confidence, ignore_badge=step.ignore_badge)
        if result:
            return True
        time.sleep(1)
    return False
