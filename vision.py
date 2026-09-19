import cv2
import numpy as np
from functools import lru_cache
from pathlib import Path
from dataclasses import dataclass

TEMPLATES_DIR = Path(__file__).parent / "templates"


@dataclass
class MatchResult:
    x: int
    y: int
    w: int
    h: int
    confidence: float

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


def screenshot_to_cv(png_bytes: bytes) -> np.ndarray:
    arr = np.frombuffer(png_bytes, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("Failed to decode screenshot")
    return img


@lru_cache(maxsize=64)
def _load_template(path: str, mtime_ns: int) -> np.ndarray:
    img = cv2.imread(path, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Failed to load template: {path}")
    return img


def _get_template(tpl_path: Path) -> np.ndarray:
    if not tpl_path.exists():
        raise FileNotFoundError(f"Template not found: {tpl_path}")
    mtime = tpl_path.stat().st_mtime_ns
    return _load_template(str(tpl_path), mtime)


def probe_template(
    screenshot: np.ndarray | bytes,
    template_name: str,
    confidence: float = 0.8,
    templates_dir: Path = TEMPLATES_DIR,
    ignore_badge: bool = False,
) -> tuple[MatchResult | None, float]:
    """Return (match_or_none, best_score) for debugging thresholds."""
    if isinstance(screenshot, bytes):
        screenshot = screenshot_to_cv(screenshot)

    template = _get_template(templates_dir / template_name)
    orig_th, orig_tw = template.shape[:2]
    crop_h = crop_w = 0

    if ignore_badge:
        crop_h = int(orig_th * 0.65)
        crop_w = int(orig_tw * 0.65)
        template = template[orig_th - crop_h:orig_th, 0:crop_w].copy()

    th, tw = template.shape[:2]
    sh, sw = screenshot.shape[:2]

    if tw > sw or th > sh:
        scale = min(sw / tw, sh / th) * 0.9
        template = cv2.resize(template, None, fx=scale, fy=scale)
        th, tw = template.shape[:2]

    result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)
    score = float(max_val)

    if score >= confidence:
        if ignore_badge:
            return MatchResult(
                x=max_loc[0], y=max_loc[1] - (orig_th - crop_h),
                w=orig_tw, h=orig_th,
                confidence=round(score, 4),
            ), score
        return MatchResult(
            x=max_loc[0], y=max_loc[1],
            w=tw, h=th,
            confidence=round(score, 4),
        ), score
    return None, score


def find_template(
    screenshot: np.ndarray | bytes,
    template_name: str,
    confidence: float = 0.8,
    templates_dir: Path = TEMPLATES_DIR,
    ignore_badge: bool = False,
) -> MatchResult | None:
    match, _ = probe_template(
        screenshot, template_name, confidence, templates_dir, ignore_badge
    )
    return match


def find_all_templates(
    screenshot: np.ndarray | bytes,
    template_name: str,
    confidence: float = 0.8,
    templates_dir: Path = TEMPLATES_DIR,
    max_results: int = 10,
) -> list[MatchResult]:
    if isinstance(screenshot, bytes):
        screenshot = screenshot_to_cv(screenshot)

    template = _get_template(templates_dir / template_name)
    th, tw = template.shape[:2]
    result = cv2.matchTemplate(screenshot, template, cv2.TM_CCOEFF_NORMED)

    matches = []
    result_copy = result.copy()
    for _ in range(max_results):
        _, max_val, _, max_loc = cv2.minMaxLoc(result_copy)
        if max_val < confidence:
            break
        matches.append(MatchResult(x=max_loc[0], y=max_loc[1], w=tw, h=th, confidence=round(max_val, 4)))
        x, y = max_loc
        cv2.rectangle(result_copy, (x - tw // 2, y - th // 2), (x + tw // 2, y + th // 2), 0, -1)

    return matches


def list_templates(templates_dir: Path = TEMPLATES_DIR) -> list[str]:
    if not templates_dir.exists():
        return []
    return sorted(p.name for p in templates_dir.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg"))


def save_template(name: str, image_bytes: bytes, templates_dir: Path = TEMPLATES_DIR) -> Path:
    templates_dir.mkdir(parents=True, exist_ok=True)
    p = templates_dir / name
    p.write_bytes(image_bytes)
    _load_template.cache_clear()
    return p


def crop_from_screenshot(png_bytes: bytes, x: int, y: int, w: int, h: int) -> bytes:
    img = screenshot_to_cv(png_bytes)
    cropped = img[y:y + h, x:x + w]
    _, buf = cv2.imencode(".png", cropped)
    return buf.tobytes()
