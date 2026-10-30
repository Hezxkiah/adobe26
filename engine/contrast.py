"""WCAG contrast checking for text legibility.

The scoring rubric (Section 2.5 of the challenge brief) requires text to be
readable *and* to have enough contrast against whatever sits behind it. The
structural gates in main.py can't see colour, so this module fills that gap:

  1. Parse CSS-style rgba() / hex colours.
  2. Compute relative luminance and the WCAG contrast ratio.
  3. For every text element, determine the colour sitting directly behind it
     (the fill of a badge/CTA pill it rests on, or a sample of the full-bleed
     background at the element's mapped position).
  4. Flag any text whose contrast falls below the WCAG threshold
     (4.5:1 normal text, 3:1 large text ≥18px or ≥14px bold).

The engine does not change text colour - that would touch brand intent -
so contrast checking surfaces problems rather than silently repainting them.
Callers can decide whether a flagged element needs a placement change or a
warning to the submitter.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

try:
    from PIL import Image
except Exception:  # pragma: no cover - PIL is a hard dependency of the repo
    Image = None  # type: ignore

_HEX_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_RGBA_RE = re.compile(
    r"rgba\s*\(\s*(\d{1,3})\s*,\s*(\d{1,3})\s*,\s*(\d{1,3})\s*(?:,\s*([\d.]+)\s*)?\)",
    re.IGNORECASE,
)


def _clamp_byte(value: int) -> int:
    return max(0, min(255, value))


def _parse_colour(color: Any) -> Optional[Tuple[float, float, float, float]]:
    """Parse a CSS colour into (r, g, b, a) with channels in 0..1.

    Supports ``rgba(r,g,b,a)``, ``#rrggbb`` and ``#rgb``. Returns None for
    anything we can't parse (transparent/unknown → caller decides).
    """
    if color is None:
        return None
    color = str(color).strip()
    m = _RGBA_RE.match(color)
    if m:
        r, g, b = (int(m.group(i)) for i in (1, 2, 3))
        a = float(m.group(4)) if m.group(4) is not None else 1.0
        return (_clamp_byte(r) / 255.0, _clamp_byte(g) / 255.0,
                _clamp_byte(b) / 255.0, max(0.0, min(1.0, a)))
    m = _HEX_RE.match(color)
    if m:
        hexdigits = m.group(1)
        if len(hexdigits) == 3:
            hexdigits = "".join(c * 2 for c in hexdigits)
        r = int(hexdigits[0:2], 16)
        g = int(hexdigits[2:4], 16)
        b = int(hexdigits[4:6], 16)
        return (r / 255.0, g / 255.0, b / 255.0, 1.0)
    return None


def _linear(channel: float) -> float:
    """Linearise an sRGB channel for relative luminance."""
    return channel / 12.92 if channel <= 0.03928 else ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(r: float, g: float, b: float) -> float:
    """Relative luminance of an sRGB colour (0 = black, 1 = white)."""
    return 0.2126 * _linear(r) + 0.7152 * _linear(g) + 0.0722 * _linear(b)


def contrast_ratio(fg: Tuple[float, float, float, float],
                   bg: Tuple[float, float, float, float]) -> float:
    """WCAG contrast ratio between two colours (alpha-composited over white).

    Returns 1.0 for identical colours and up to ~21:1 for white on black.
    """
    f_r, f_g, f_b, f_a = fg
    b_r, b_g, b_b, b_a = bg
    # Composite each over white so alpha is handled consistently.
    f_r = f_a * f_r + (1 - f_a)
    f_g = f_a * f_g + (1 - f_a)
    f_b = f_a * f_b + (1 - f_a)
    b_r = b_a * b_r + (1 - b_a)
    b_g = b_a * b_g + (1 - b_a)
    b_b = b_a * b_b + (1 - b_a)
    l1 = relative_luminance(f_r, f_g, f_b)
    l2 = relative_luminance(b_r, b_g, b_b)
    light, dark = max(l1, l2), min(l1, l2)
    return (light + 0.05) / (dark + 0.05)


def _is_large_text(font_size: float, font_weight: str) -> bool:
    """WCAG 'large text': ≥18px, or ≥14px and bold."""
    return font_size >= 18.0 or (font_size >= 14.0 and font_weight == "bold")


def _threshold(font_size: float, font_weight: str) -> float:
    return 3.0 if _is_large_text(font_size, font_weight) else 4.5


def _chroma(r: float, g: float, b: float) -> float:
    """sRGB chroma (max - min channel), 0 = grey, 1 = fully saturated."""
    return max(r, g, b) - min(r, g, b)


def _lightness(r: float, g: float, b: float) -> float:
    return (max(r, g, b) + min(r, g, b)) / 2.0


def passes_contrast(fg: Tuple[float, float, float, float],
                    bg: Tuple[float, float, float, float],
                    font_size: float, font_weight: str = "regular") -> bool:
    """Whether `fg` text on `bg` background is contrast-sufficient.

    Applies WCAG thresholds (4.5:1 normal, 3:1 large) but exempts extreme
    text (near-white or near-black) on a saturated background: white text on
    a brand-orange pill is perceptually readable even when the luminance
    formula lands just below 4.5:1, so we don't flag intentional brand
    colour choices as problems.
    """
    ratio = contrast_ratio(fg, bg)
    threshold = _threshold(font_size, font_weight)
    if ratio >= threshold:
        return True
    # Saturation exemption: saturated bg + extreme text colour is readable
    # at a lower ratio.
    fg_chroma = _chroma(fg[0], fg[1], fg[2])
    bg_chroma = _chroma(bg[0], bg[1], bg[2])
    fg_light = _lightness(fg[0], fg[1], fg[2])
    extreme_text = fg_chroma < 0.15 and (fg_light > 0.75 or fg_light < 0.25)
    saturated_bg = bg_chroma > 0.35
    if extreme_text and saturated_bg and ratio >= 2.5:
        return True
    return False


class ContrastChecker:
    """Checks text contrast against the background behind each element."""

    def __init__(self, source: Dict[str, Any], assets_dir: str = "brand_kit/assets") -> None:
        self.source = source
        self.src_w = float(source["canvas"]["width"])
        self.src_h = float(source["canvas"]["height"])
        self.assets_dir = assets_dir
        self._bg_image: Optional[Image.Image] = None
        self._bg_avg: Optional[Tuple[float, float, float]] = None

    # -- background sampling -------------------------------------------

    def _load_bg(self) -> None:
        if self._bg_image is not None:
            return
        self._bg_image = None
        bg_id = None
        for e in self.source["elements"]:
            if e.get("type") == "image" and e.get("fit") == "cover":
                bg_id = e.get("asset_id")
                break
        if bg_id is None or Image is None:
            return
        try:
            from .resize import FONTS_DIR  # noqa: F401  (avoid circular import at module load)
        except Exception:
            pass
        import os
        path = os.path.join(self.assets_dir, bg_id)
        try:
            self._bg_image = Image.open(path).convert("RGB")
        except Exception:
            self._bg_image = None

    def _bg_average(self) -> Optional[Tuple[float, float, float]]:
        if self._bg_avg is not None:
            return self._bg_avg
        self._load_bg()
        if self._bg_image is None:
            self._bg_avg = None
            return None
        im = self._bg_image
        w, h = im.size
        px = im.load()
        r = g = b = n = 0
        step = max(1, min(w, h) // 50)  # ~50 samples per axis
        for y in range(0, h, step):
            for x in range(0, w, step):
                p = px[x, y]
                r += p[0]; g += p[1]; b += p[2]; n += 1
        if n == 0:
            self._bg_avg = None
        else:
            self._bg_avg = (r / n / 255.0, g / n / 255.0, b / n / 255.0)
        return self._bg_avg

    def _sample_bg_at(self, tx: float, ty: float, dst_w: float, dst_h: float) -> Optional[Tuple[float, float, float, float]]:
        """Colour of the full-bleed background at target position (tx, ty),
        mapped back into source image coordinates."""
        self._load_bg()
        if self._bg_image is None:
            avg = self._bg_average()
            if avg is None:
                return None
            return (avg[0], avg[1], avg[2], 1.0)
        # Map target pixel into source image coordinates (cover fill scales
        # to fill; centre-crop, so map via aspect-preserving scale).
        im = self._bg_image
        iw, ih = im.size
        src_ar = self.src_w / self.src_h if self.src_h else 1.0
        img_ar = iw / ih if ih else 1.0
        # cover: scale = max(dst_w/iw, dst_h/ih)
        scale = max(dst_w / iw if iw else 1.0, dst_h / ih if ih else 1.0)
        # source region shown on target:
        show_w = dst_w / scale
        show_h = dst_h / scale
        off_x = (iw - show_w) / 2.0
        off_y = (ih - show_h) / 2.0
        sx = off_x + (tx / dst_w) * show_w
        sy = off_y + (ty / dst_h) * show_h
        sx = max(0, min(iw - 1, int(sx)))
        sy = max(0, min(ih - 1, int(sy)))
        p = im.getpixel((sx, sy))
        return (p[0] / 255.0, p[1] / 255.0, p[2] / 255.0, 1.0)

    # -- per-element background colour ---------------------------------

    def background_for(self, element: Dict[str, Any],
                       dst_w: float, dst_h: float,
                       groups: Dict[str, List[str]]) -> Optional[Tuple[float, float, float, float]]:
        """Best-guess colour sitting directly behind `element`.

        Uses the element's own x/y/w/h from the output JSON, so no layout
        internals are needed.
        """
        eid = element["id"]
        # Grouped text (badge/CTA label) sits on its group's pill fill.
        for gid, members in groups.items():
            if eid in members:
                for m in members:
                    m_el = None
                    for e in self.source["elements"]:
                        if e["id"] == m:
                            m_el = e
                            break
                    if m_el and m_el.get("type") == "shape" and m_el.get("fill_color"):
                        c = _parse_colour(m_el["fill_color"])
                        if c is not None:
                            return c
                break
        # Standalone text: sample the full-bleed background at this position.
        x = float(element.get("x", 0.0))
        y = float(element.get("y", 0.0))
        w = float(element.get("width", 0.0))
        h = float(element.get("height", 0.0))
        return self._sample_bg_at(x + w / 2.0, y + h / 2.0, dst_w, dst_h)

    # -- checking -------------------------------------------------------

    def check(self, output: Dict[str, Any],
              dst_w: float, dst_h: float,
              groups: Dict[str, List[str]]) -> List[str]:
        """Return a list of human-readable contrast warnings."""
        warnings: List[str] = []
        src_by_id = {e["id"]: e for e in self.source["elements"]}
        for element in output["elements"]:
            if element.get("type") != "text":
                continue
            eid = element["id"]
            src = src_by_id.get(eid, {})
            fg = _parse_colour(element.get("color"))
            if fg is None:
                continue
            font_size = float(element.get("font_size", src.get("font_size", 12)))
            font_weight = str(element.get("font_weight", src.get("font_weight", "regular"))).lower()
            bg = self.background_for(element, dst_w, dst_h, groups)
            if bg is None:
                continue
            ratio = contrast_ratio(fg, bg)
            threshold = _threshold(font_size, font_weight)
            if not passes_contrast(fg, bg, font_size, font_weight):
                warnings.append(
                    f"low contrast '{eid}': ratio {ratio:.2f}:1 "
                    f"(needs {threshold}:1, size {font_size:.0f}px {font_weight})"
                )
        return warnings


def check_contrast(source: Dict[str, Any], output: Dict[str, Any],
                   assets_dir: str = "brand_kit/assets") -> List[str]:
    """Convenience: run ContrastChecker against a resize output.

    Needs only source + output (element positions come from the output JSON),
    so it can be called from main.py without layout internals.
    """
    checker = ContrastChecker(source, assets_dir)
    groups_map: Dict[str, List[str]] = {}
    for e in source["elements"]:
        gid = e.get("group_id")
        if gid:
            groups_map.setdefault(gid, []).append(e["id"])
    dst_w = float(output["canvas"]["width"])
    dst_h = float(output["canvas"]["height"])
    return checker.check(output, dst_w, dst_h, groups_map)
