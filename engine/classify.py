"""Infer what each element is from geometry alone.

The schema hands us `id`, `type`, and a box. There is no `role` field,
no reading order, no "this is the headline" (FAQ Q1 says so explicitly),
so anything a real designer would know has to be recovered from
geometry, type, and `group_id`.

The vocabulary below is deliberately small. Every role exists because the
layout needs to make a *different* decision for it, not because it's a
useful label in the abstract:

  FULL_BLEED  covers the canvas - must be re-stretched, not placed
  DECORATIVE  low-opacity bleed shapes - repositioned loosely, may
              deliberately hang off an edge
  LOGO        wide, short, corner-anchored image - kept at a fixed
              readable size, not scaled with the canvas
  FOCAL       the dominant non-bleed image - the layout's anchor, gets
              the largest share of the new space
  HEADLINE    largest text by font size - leads the type hierarchy
  SUBHEAD     the next-largest text - the supporting sentence under it
  FINEPRINT   incidental text (disclaimers) - bottom-anchored, deprioritised
  BODY        remaining text
  GROUP       anything sharing a group_id - placed as one rigid unit
"""

from __future__ import annotations

from typing import Any, Dict, List, Sequence, Tuple

from .metrics import TextMeasurer

# An image covering at least this fraction of the source canvas is
# treated as background. Below it, it's a placed element.
_BLEED_COVERAGE = 0.92

# Shapes at or under this opacity are ambient decoration, not content.
# Only elements that actually set `opacity` are affected - the schema
# defaults it to 1.0, so an absent value means fully opaque and correctly
# fails this test. The Level 2 sample sets 0.15 / 0.12 on its two deco
# circles; nothing else in any sample carries the field.
_DECORATIVE_OPACITY = 0.35

# Epsilon for margin measurement, NOT a bleed threshold. Gaps smaller than
# this are treated as zero when inferring the design's margin, so a
# hairline overlap on an edge doesn't register as breathing room.
#
# Edge bleed itself needs no constant - it's an exact geometric test in
# `_role_of` (`x < 0`, `x + width > canvas_width`, etc.). A shape hanging
# off the canvas is decorative by intent; the Level 2 sample documents
# exactly this on `deco_circle_1`.
_MARGIN_EPSILON = 0.001

# LOGO heuristic: wider than this aspect, and smaller than this share of
# the canvas. A 320x100 lockup hits both; a 400x400 product shot
# hits neither.
_LOGO_ASPECT = 1.9
_LOGO_MAX_COVERAGE = 0.12

# A wide, small image hugging the bottom edge is a watermark/footer mark
# rather than a logo - it gets anchored to the opposite edge. Purely
# geometric, and deliberately conservative: without this, a bottom-right
# badge and a top-left lockup both classify as LOGO and the layout pins
# both to the same corner.
_WATERMARK_EDGE = 0.06

# Fine print needs two agreeing signals, not one. Font ratio alone can't
# separate a 24px subhead under a 56px headline from a 12px legal line
# under a 40px headline - both are "small relative to the biggest text",
# and treating the subhead as incidental throws away the one piece of
# supporting copy the design has. So: notably smaller than the headline
# AND tiny in absolute terms against the canvas.
_FINEPRINT_RATIO = 0.45
_FINEPRINT_CANVAS_RATIO = 0.022


def _bbox(elements: Sequence[Dict[str, Any]]) -> Tuple[float, float, float, float]:
    "Makes a box of for the imported element"
    x0 = min(e["x"] for e in elements)
    y0 = min(e["y"] for e in elements)
    x1 = max(e["x"] + e["width"] for e in elements)
    y1 = max(e["y"] + e["height"] for e in elements)
    return x0, y0, x1 - x0, y1 - y0


# def _center(b: Tuple[float, float, float, float]) -> Tuple[float, float]:
#     return b[0] + b[2] / 2.0, b[1] + b[3] / 2.0


class Design:
    """A classified design: elements plus the roles inferred for them.

    Also carries a few whole-design measurements (source aspect, the
    canvas margin implied by the content) that the layout archetypes
    reuse, so those get computed once here rather than per archetype.
    """

    def __init__(self, source: Dict[str, Any], measurer: TextMeasurer) -> None:
        self.source = source
        self.canvas = source["canvas"]
        self.width = float(self.canvas["width"])
        self.height = float(self.canvas["height"])
        self.aspect = self.width / self.height if self.height else 1.0
        self.elements: List[Dict[str, Any]] = list(source["elements"])
        self.measurer = measurer

        self.by_id: Dict[str, Dict[str, Any]] = {
            e["id"]: e for e in self.elements
        }
        self.roles: Dict[str, str] = {}
        self.groups: Dict[str, List[str]] = {}
        self._classify()

        self.bleed: List[str] = [
            e["id"] for e in self.elements if self.roles[e["id"]] == "FULL_BLEED"
        ]
        self.margin = self._implied_margin()

    # -- classification --------------------------------------------------

    def _classify(self) -> None:
        canvas_area = self.width * self.height
        grouped: Dict[str, List[str]] = {}
        for e in self.elements:
            gid = e.get("group_id")
            if gid:
                grouped.setdefault(gid, []).append(e["id"])
        self.groups = grouped

        for e in self.elements:
            self.roles[e["id"]] = self._role_of(e, canvas_area, gid=e.get("group_id"))

        self._classify_text_hierarchy()
        self._classify_focal()

    def _role_of(
        self, e: Dict[str, Any], canvas_area: float, gid: Any
    ) -> str:
        area = float(e["width"]) * float(e["height"])
        coverage = area / canvas_area if canvas_area else 0.0

        if e["type"] == "image":
            if coverage >= _BLEED_COVERAGE:
                return "FULL_BLEED"
            aspect = e["width"] / e["height"] if e["height"] else 0.0
            if aspect >= _LOGO_ASPECT and coverage <= _LOGO_MAX_COVERAGE:
                return self._corner_mark_role(e)
            return "IMAGE"

        if e["type"] == "shape":
            if coverage >= _BLEED_COVERAGE:
                return "FULL_BLEED"
            if gid:
                return "GROUP"
            opacity = float(e.get("opacity", 1.0))
            bleeds = (
                e["x"] < 0 or e["y"] < 0
                or e["x"] + e["width"] > self.width
                or e["y"] + e["height"] > self.height
            )
            if opacity <= _DECORATIVE_OPACITY or bleeds:
                return "DECORATIVE"
            return "SHAPE"

        # Text: anything in a group is placed with its group.
        if gid:
            return "GROUP"
        return "TEXT"

    def _corner_mark_role(self, e: Dict[str, Any]) -> str:
        """LOGO or WATERMARK, decided by which edge the mark hugs.

        Both are wide, small, and image-typed. The distinguishing signal
        is placement: a lockup belongs at the top of the design, a
        watermark or footer mark at the bottom. Both are sticky to their
        own edge under any resize, so classifying them the same way would
        collapse them into one corner.
        """
        short = min(self.width, self.height)
        if short <= 0:
            return "LOGO"
        gap_top = e["y"]
        gap_bottom = self.height - (e["y"] + e["height"])
        # Bottom-hugging, and clearly closer to the bottom than the top.
        if gap_bottom <= self.height * _WATERMARK_EDGE and gap_bottom < gap_top:
            return "WATERMARK"
        return "LOGO"

    def _classify_text_hierarchy(self) -> None:
        """Promote the largest text to HEADLINE, order the rest beneath it.

        Sorted by font size, then top-to-bottom, so the reading order of
        a caption block survives into the new layout.
        """
        texts = [
            e for e in self.elements
            if self.roles[e["id"]] == "TEXT" and e["type"] == "text"
        ]
        if not texts:
            return

        texts.sort(key=lambda e: (-float(e.get("font_size", 0)), e["y"]))

        largest = float(texts[0].get("font_size", 0))
        self.roles[texts[0]["id"]] = "HEADLINE"

        short = min(self.width, self.height)
        for rank, e in enumerate(texts[1:]):
            size = float(e.get("font_size", 0))
            if (
                largest > 0
                and short > 0
                and size <= largest * _FINEPRINT_RATIO
                and size <= short * _FINEPRINT_CANVAS_RATIO
            ):
                self.roles[e["id"]] = "FINEPRINT"
            elif rank == 0:
                self.roles[e["id"]] = "SUBHEAD"

    def _classify_focal(self) -> None:
        """Promote the biggest non-bleed, non-logo image to FOCAL.

        Ties break toward the larger of the two, then toward square,
        since product shots are usually the square thing in the middle.
        """
        candidates = [
            e for e in self.elements
            if self.roles[e["id"]] == "IMAGE"
        ]
        if not candidates:
            return
        focal = max(
            candidates,
            key=lambda e: (
                float(e["width"]) * float(e["height"]),
                -abs(float(e["width"]) - float(e["height"])),
            ),
        )
        self.roles[focal["id"]] = "FOCAL"

    # -- whole-design measurements --------------------------------------

    def _implied_margin(self) -> float:
        """The design's own breathing room, as a fraction of its short side.

        Taken from the gap between the content edges and the canvas edge
        (ignoring decorative bleed and full-bleed elements). Reusing the
        source design's own margin is a cheap way to carry its visual
        rhythm into a canvas whose proportions have nothing to do with
        it - and it's more faithful than hardcoding a percentage.
        """
        short = min(self.width, self.height)
        if short <= 0:
            return 0.0

        edges: List[float] = []
        for e in self.elements:
            if self.roles[e["id"]] in ("FULL_BLEED", "DECORATIVE"):
                continue
            if e.get("group_id"):
                continue
            edges.append(e["x"])
            edges.append(e["y"])
            edges.append(self.width - (e["x"] + e["width"]))
            edges.append(self.height - (e["y"] + e["height"]))

        gaps = sorted(g for g in edges if g > _MARGIN_EPSILON)
        if not gaps:
            return 0.0

        # Lowest quartile: the closest content to each edge is what
        # defines the margin. A median would be dragged around by
        # intentionally off-centre elements.
        quartile = gaps[max(0, int(len(gaps) * 0.25) - 1)]
        # Clamp to a sane band so one stray element can't dominate.
        return max(0.02, min(0.09, quartile / short))

    # -- ordering helpers ------------------------------------------------

    def group_bbox(self, ids: Sequence[str]) -> Tuple[float, float, float, float]:
        return _bbox([self.by_id[i] for i in ids])

    def text_elements(self) -> List[Dict[str, Any]]:
        return [e for e in self.elements if e["type"] == "text"]

    def describe(self) -> str:
        """Human-readable role breakdown - useful while iterating."""
        by_role: Dict[str, List[str]] = {}
        for e in self.elements:
            by_role.setdefault(self.roles[e["id"]], []).append(e["id"])
        lines = [
            f"source {self.width:.0f}x{self.height:.0f} (aspect {self.aspect:.3f}), "
            f"implied margin {self.margin * 100:.1f}%"
        ]
        for role in sorted(by_role):
            ids = by_role[role]
            lines.append(f"  {role:11s} {len(ids):2d}  {', '.join(ids)}")
        if self.groups:
            for gid, ids in self.groups.items():
                lines.append(f"  group {gid!r}: {', '.join(ids)}")
        return "\n".join(lines)