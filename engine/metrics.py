"""Text measurement for the layout engine.

Everything here exists to answer one question exactly: *given this string
at this font size in a box this wide, how many lines will it wrap to, and
how wide is the widest one?*

Why not estimate? Because the vendored renderer wraps at real glyph
advance widths through skia (`renderer/converter/wrap.py`). If our solver
and the renderer disagree about where a line breaks, every font size we
pick is wrong and the rendered PNG shows clipped or colliding text. So we
import the renderer's own measurement path and measure the same way it
draws.

The import is deliberately optional. `main.py` already tolerates a
missing renderer (it falls back to a box-and-label preview), and a resize
engine that refuses to run without skia would turn a cosmetic
degradation into a total failure. When skia is unavailable we fall back
to per-character advance estimates - coarser, but still sane.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Fraction-of-font-size advance widths used when skia isn't available.
# Rough per-class ratios rather than one flat average, because the
# difference between "iii" and "WWW" is most of what decides wrap points.
_NARROW = set("iljtfrI.,;:'\"()[]|! ")
_WIDE = set("mwMW@%")
_UPPER = set("ABCDEFGHJKLNOPQRSTUVXYZ&")
_ADVANCE = {"narrow": 0.30, "upper": 0.64, "wide": 0.88, "default": 0.52}

# Wrapping tolerance: the renderer breaks a line when the next glyph would
# push past `width`, so a line can land a hair over. Allow that much slack
# before calling a size "doesn't fit".
_WRAP_EPSILON = 1.005

# Bisection precision, in px of font size. Below ~0.1px is invisible.
_BISECT_STEPS = 26

# Wrap safety margin, as a fraction of the box width.
#
# Greedy wrapping is a step function: a hair more width can collapse four
# lines into three. A solver that sizes text to land exactly on that
# boundary is technically correct and practically a coin flip - on the
# 728x90 case, a 0.02px difference in box width flipped the headline
# between 3 and 4 lines, so the emitted box claimed a height the renderer
# didn't honour and the text overlapped the subhead. Solving against a
# slightly narrowed width keeps every choice clear of the cliff.
_WRAP_SAFETY = 0.97


def _approx_advance(ch: str, size: float, serif: bool) -> float:
    if ch in _NARROW:
        ratio = _ADVANCE["narrow"]
    elif ch in _WIDE:
        ratio = _ADVANCE["wide"]
    elif ch in _UPPER:
        ratio = _ADVANCE["upper"]
    else:
        ratio = _ADVANCE["default"]
    # Serif faces sit slightly narrower on lowercase than the geometric
    # sans this default was tuned for.
    if serif and ch not in _UPPER and ch not in _WIDE:
        ratio *= 0.96
    return size * ratio


class TextMeasurer:
    """Measures wrapped text the same way the renderer will draw it.

    One instance is shared across a whole run: the font manager is
    expensive to build (it reads every TTF in the brand kit) and the
    per-string width results are reused by the font-size bisection, which
    asks about the same string at a dozen sizes.
    """

    def __init__(self, fonts_dir: str | Path = "brand_kit/fonts") -> None:
        self._fonts_dir = str(fonts_dir)
        self._loaded = False
        self._font_manager: Any = None
        self._char_widths: Any = None
        self._wrap_text: Any = None
        self._cache: Dict[Tuple[Any, ...], List[float]] = {}

    # -- renderer bridge -------------------------------------------------

    def _ensure_renderer(self) -> bool:
        """Try once to bind to the renderer's measurement path."""
        if self._loaded:
            return self._char_widths is not None
        self._loaded = True
        try:
            from renderer.converter.brand_fonts import load_brand_font_manager
            from renderer.converter.wrap import char_widths, wrap_text

            self._font_manager = load_brand_font_manager(self._fonts_dir)
            self._char_widths = char_widths
            self._wrap_text = wrap_text
        except Exception:
            # Any failure - no skia, no cr_renderer, no fonts on disk -
            # falls through to the estimator. We never want measurement
            # problems to abort a resize.
            self._font_manager = None
            self._char_widths = None
            self._wrap_text = None
        return self._char_widths is not None

    @property
    def is_exact(self) -> bool:
        """True when measuring through skia rather than the estimator."""
        return self._ensure_renderer()

    # -- width tables ----------------------------------------------------

    def advance_widths(
        self,
        content: str,
        font_family: str,
        font_weight: str,
        font_style: str,
        size: float,
    ) -> List[float]:
        """Per-character advance widths, indexed like `content`."""
        key = (content, font_family, font_weight, font_style, round(size, 3))
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        widths: Optional[List[float]] = None
        if self._ensure_renderer():
            try:
                widths = list(
                    self._char_widths(
                        self._font_manager, font_family, font_weight,
                        font_style, size, content,
                    )
                )
            except Exception:
                widths = None

        if widths is None or len(widths) != len(content):
            serif = "serif" in font_family.lower()
            widths = [_approx_advance(ch, size, serif) for ch in content]

        self._cache[key] = widths
        return widths

    # -- wrapping --------------------------------------------------------

    def _wrap(self, content: str, widths: Sequence[float], box_w: float) -> str:
        if self._ensure_renderer():
            try:
                return self._wrap_text(content, list(widths), box_w)
            except Exception:
                pass
        return self._greedy_wrap(content, widths, box_w)

    @staticmethod
    def _greedy_wrap(content: str, widths: Sequence[float], box_w: float) -> str:
        """Index-preserving greedy wrap, matching the renderer's contract.

        Only existing spaces become newlines, in place, so every character
        keeps its original index. That matters beyond the renderer: the
        schema's `style_runs` are 0-based character indices into
        `content`, so a wrap that renumbered characters would shift the
        colours of a two-tone headline onto the wrong words.

        A single word wider than `box_w` is left to overflow rather than
        being hard-broken, same as `wrap.py` does.
        """
        if not content or box_w <= 0:
            return content

        chars = list(content)
        line_w = 0.0
        last_space: Optional[int] = None

        for i, ch in enumerate(chars):
            if ch == "\n":
                line_w = 0.0
                last_space = None
                continue

            w = widths[i] if i < len(widths) else 0.0
            if line_w + w > box_w and last_space is not None:
                chars[last_space] = "\n"
                line_w = sum(
                    widths[j]
                    for j in range(last_space + 1, i + 1)
                    if chars[j] != "\n"
                )
                last_space = None
            else:
                line_w += w

            if ch == " ":
                last_space = i

        return "".join(chars)

    # -- the one query layout actually needs -----------------------------

    def measure(
        self,
        content: str,
        font_family: str,
        font_weight: str,
        font_style: str,
        size: float,
        box_w: float,
        line_height: float = 1.2,
        safe: bool = True,
    ) -> Dict[str, float]:
        """Wrap `content` in a `box_w`-wide box and report what came out.

        Returns `n_lines`, `max_line_width` (so a caller can centre or
        right-align against the real inked width rather than the box),
        `longest_word_width`, and `text_height`.

        `longest_word_width` is the reason this can't be a one-line
        formula: greedy wrap has no fallback for a word wider than the
        box, so it overflows at *every* font size. That's the constraint
        that actually binds first in narrow targets like a 728x90 banner.

        `safe` narrows the width by `_WRAP_SAFETY` before wrapping, so a
        size chosen here still fits when the renderer wraps at the true
        box width. Leave it on for layout decisions; turn it off only to
        reproduce exactly what the renderer will do.
        """
        if not content:
            return {
                "n_lines": 0, "max_line_width": 0.0,
                "longest_word_width": 0.0, "text_height": 0.0,
            }

        widths = self.advance_widths(
            content, font_family, font_weight, font_style, size
        )
        solve_w = box_w * _WRAP_SAFETY if safe else box_w
        wrapped = self._wrap(content, widths, solve_w)

        # Split on the newlines the wrapper inserted, keeping the original
        # character indices so widths stay aligned with `content`.
        n_lines = 1
        max_line = 0.0
        run = 0.0
        longest_word = 0.0
        word = 0.0

        for i, ch in enumerate(wrapped):
            w = widths[i] if i < len(widths) else 0.0
            if ch == "\n":
                n_lines += 1
                max_line = max(max_line, run)
                run = 0.0
                longest_word = max(longest_word, word)
                word = 0.0
                continue
            run += w
            if ch == " ":
                longest_word = max(longest_word, word)
                word = 0.0
            else:
                word += w

        max_line = max(max_line, run)
        longest_word = max(longest_word, word)

        return {
            "n_lines": n_lines,
            "max_line_width": max_line,
            "longest_word_width": longest_word,
            "text_height": n_lines * size * line_height,
        }

    def fits(
        self,
        content: str,
        font_family: str,
        font_weight: str,
        font_style: str,
        size: float,
        box_w: float,
        box_h: float,
        line_height: float = 1.2,
    ) -> bool:
        """Whether text at `size` stays inside `box_w` x `box_h`.

        Height is the soft constraint and width is the hard one. Vertical
        overflow renders as crowding (the renderer doesn't clip); a single
        unbreakable word wider than the box overflows into its
        neighbours, which is the ugliest failure in the set. Both are
        therefore treated as disqualifying a size.
        """
        if box_w <= 0 or box_h <= 0:
            return False

        m = self.measure(
            content, font_family, font_weight, font_style,
            size, box_w, line_height,
        )
        if m["n_lines"] == 0:
            return True

        if m["longest_word_width"] > box_w * _WRAP_EPSILON:
            return False
        if m["text_height"] > box_h:
            return False
        # Lines are built greedily, so any line wider than the box is a
        # word that had nowhere to break.
        if m["max_line_width"] > box_w * _WRAP_EPSILON:
            return False
        return True

    def fit_size(
        self,
        content: str,
        font_family: str,
        font_weight: str,
        font_style: str,
        box_w: float,
        box_h: float,
        line_height: float = 1.2,
        upper: float = 1e9,
        lower: float = 1.0,
    ) -> float:
        """Largest size in `[lower, upper]` whose text fits the box.

        `upper` is the designer's intent (usually the proportionally
        scaled source size); the solver only ever shrinks from there, so
        a text element can't balloon past what the original design
        implied. Bisection is valid here because every term in `fits` is
        monotonically non-increasing in `size`.
        """
        if not content:
            return lower
        if upper < lower:
            upper = lower
        if self.fits(
            content, font_family, font_weight, font_style,
            upper, box_w, box_h, line_height,
        ):
            return upper

        best = lower
        lo, hi = lower, upper
        for _ in range(_BISECT_STEPS):
            mid = (lo + hi) / 2.0
            if self.fits(
                content, font_family, font_weight, font_style,
                mid, box_w, box_h, line_height,
            ):
                best = mid
                lo = mid
            else:
                hi = mid
        return best