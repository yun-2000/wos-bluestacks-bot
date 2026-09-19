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


def _crop_region_pct(screenshot: np.ndarray, region_pct: list[float]) -> np.ndarray:
    if len(region_pct) != 4:
        raise ValueError("region_pct must be [left, top, right, bottom] in 0–1 fractions")
    left, top, right, bottom = region_pct
    sh, sw = screenshot.shape[:2]
    x1 = max(0, min(sw, int(sw * left)))
    y1 = max(0, min(sh, int(sh * top)))
    x2 = max(0, min(sw, int(sw * right)))
    y2 = max(0, min(sh, int(sh * bottom)))
    if x2 <= x1 or y2 <= y1:
        raise ValueError(f"Invalid OCR region after crop: {(x1, y1, x2, y2)}")
    return screenshot[y1:y2, x1:x2]


def _preprocess_for_ocr(roi: np.ndarray, scale: float = 3.0) -> np.ndarray:
    """Prepare white-on-dark game UI text for Tesseract."""
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY) if roi.ndim == 3 else roi
    up = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    inverted = cv2.bitwise_not(up)
    _, binary = cv2.threshold(inverted, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    return binary


def normalize_ocr_text(raw: str) -> str:
    """Strip noise and fix common digit misreads."""
    s = "".join(raw.split())
    # Game font often turns "1/" into "V" (e.g. "Marching V6")
    s = s.replace("V6", "1/6").replace("v6", "1/6")
    s = s.replace("V/", "1/").replace("v/", "1/")
    trans = str.maketrans({
        "l": "1", "I": "1", "i": "1", "|": "1",
        "O": "0", "o": "0",
        "S": "5", "s": "5",
        "B": "8",
    })
    return s.translate(trans)


def text_matches(raw: str, expected: str) -> bool:
    if not expected:
        return False
    norm_raw = normalize_ocr_text(raw)
    norm_exp = normalize_ocr_text(expected)
    if norm_exp in norm_raw:
        return True
    # Also accept compact forms like "16" for "1/6"
    if "/" in norm_exp:
        compact = norm_exp.replace("/", "")
        if compact and compact in norm_raw.replace("/", ""):
            return True
    return False


def read_text(
    screenshot: np.ndarray | bytes,
    region_pct: list[float],
    *,
    whitelist: str = "0123456789/",
    psm: int = 7,
) -> str:
    """OCR text from a relative screenshot region (0–1 fractions)."""
    import pytesseract

    if isinstance(screenshot, bytes):
        screenshot = screenshot_to_cv(screenshot)

    roi = _crop_region_pct(screenshot, region_pct)
    processed = _preprocess_for_ocr(roi)
    configs = [
        f"--psm {psm} -c tessedit_char_whitelist={whitelist}",
        f"--psm {psm}",
        "--psm 6",
        "--psm 8 -c tessedit_char_whitelist=0123456789/",
    ]
    parts: list[str] = []
    try:
        for cfg in configs:
            t = pytesseract.image_to_string(processed, config=cfg) or ""
            if t.strip():
                parts.append(t)
    except pytesseract.TesseractNotFoundError as e:
        raise RuntimeError(
            "Tesseract not found. Install with: brew install tesseract"
        ) from e
    combined = " ".join(parts)
    # #region agent log
    try:
        import json, time
        from pathlib import Path
        sh, sw = screenshot.shape[:2]
        with Path(__file__).parent.joinpath(".cursor/debug-56bd45.log").open("a") as _f:
            _f.write(json.dumps({
                "sessionId": "56bd45",
                "hypothesisId": "A,B",
                "location": "vision.py:read_text",
                "message": "OCR result",
                "data": {
                    "screen": [sw, sh],
                    "region_pct": region_pct,
                    "parts": parts,
                    "combined": combined,
                    "norm": normalize_ocr_text(combined),
                    "roi_shape": list(roi.shape),
                },
                "timestamp": int(time.time() * 1000),
            }, ensure_ascii=False) + "\n")
    except Exception:
        pass
    # #endregion
    return combined
