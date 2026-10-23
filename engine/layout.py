"""Turn a classified design into a new layout for a different canvas.

The premise of the whole challenge is that one transform can't be right
for every target. Stretching a 1080x1080 square into 1600x400 keeps every
coordinate valid and every hard rule satisfied, and produces a squashed
unreadable strip. So this module picks a *strategy* from the aspect
delta and applies it:

  NEAR      small delta - proportional scale. Stretching is genuinely
            correct here, so use it.
  STACKED   target taller - the content column keeps its order and
            spreads vertically.
  BANDED    target wider - content that stacked vertically becomes
            horizontal bands.
  STRIP     extreme delta - a near-single-row or near-single-column
            composition, because the usual bands no longer fit.

Two things are deliberately NOT here. Backgrounds and decoration are
handled by `resize.py` before this runs, because they're the two roles
whose placement rules have nothing to do with the archetype. And text
sizes are solved in `metrics.py` against the slots assigned here, so
that every archetype gets the same font-fitting behaviour for free.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from .classify import Design
from .metrics import TextMeasurer

Rect = Tuple[float, float, float, float]  # x, y, w, h

# Below this aspect ratio delta, treat the target as proportionally
# close enough that a uniform scale is the honest answer.
_NEAR_DELTA = 0.12

# Beyond this, a single row/column is the only composition that fits.
_STRIP_DELTA = 2.2

# Roles that anchor a composition, in the order they should claim space.
# Everything not listed here fills in around them.
_ANCHORS = ("LOGO", "WATERMARK", "FOCAL")

# Share of the usable area reserved for the focal image, by archetype.
# A product shot is usually the reason the ad works, so it gets real
# estate rather than being laid out as if it were any other element.
_FOCAL_SHARE = {"NEAR": 0.40, "STACKED": 0.34, "BANDED": 0.30, "STRIP": 0.24}

# A grouped unit (badge, CTA) may grow this much beyond its source size,
# and no more. Letting a pill stretch to fill an assigned column produces
# a slab that reads as a shape rather than a button.
_GROUP_MAX_GROW = 1.15

# A label inside a group is allowed to exceed the rigid group scale, but
# only up to this factor - enough to stay legible on a short canvas,
# bounded so a small badge can't invert into a giant one.
_GROUP_TEXT_MAX_GROW = 2.2

# Group text below this many pixels stops being readable at any canvas
# size, so it is never scaled below it by rigid group transform alone.
_GROUP_TEXT_MIN_PX = 7.0

# A logo or watermark is sized against the source's short side, not the
# target's - it keeps a readable size instead of inflating to fill a
# banner, and shrinking to nothing on a strip.
_EDGE_MARK_MAX_SHARE = 0.22

# ...but the target's short side is a hard ceiling. Without this, a logo
# sized off a 1080px source lands at 237px on a 90px-tall banner and
# swallows the design.
_EDGE_MARK_TARGET_SHARE = 0.40

# Watermarks are furniture, not furniture-you-need-to-read. They're held
# to a tighter ceiling so a footer mark never competes with the copy.
_WATERMARK_MAX_SHARE = 0.22

# Content priority tiers, used by the STRIP archetype and the collision
# resolver to decide who gives ground when the canvas is too small for
# everything. P0 text must stay legible; P2 text is allowed to shrink to
# a minimal box rather than forcing the headline into illegibility.
_PRIORITY_P0 = 0  # headline, focal image, primary CTA
_PRIORITY_P1 = 1  # subhead, badges, ribbon, body copy
_PRIORITY_P2 = 2  # fineprint / legal text, decoration

# Readability floor, in px. P0/P1 text is never solved below this; P2 text
# may drop to the schema minimum (1px) as a last resort.
_READABILITY_FLOOR_P0 = 10.0
_READABILITY_FLOOR_P1 = 8.0
_READABILITY_FLOOR_P2 = 1.0

# Action-word hints that promote a grouped CTA to P0 so a "Shop ..." button
# never gets shrunk below a badge on a narrow strip.
_CTA_HINTS = ("shop", "buy", "order", "get", "start", "join")


def choose_archetype(src_aspect: float, dst_aspect: float) -> str:
    """Pick a layout strategy from the aspect ratio delta."""
    if src_aspect <= 0 or dst_aspect <= 0:
        return "NEAR"

    delta = dst_aspect / src_aspect
    if abs(delta - 1.0) <= _NEAR_DELTA:
        return "NEAR"
    if abs(delta - 1.0) >= _STRIP_DELTA:
        return "STRIP"
    return "BANDED" if delta > 1.0 else "STACKED"


def _fit_cover(w: float, h: float, box_w: float, box_h: float) -> Tuple[float, float]:
    """Scale (w, h) to cover box_w x box_h, preserving aspect.

    'Cover' means one axis matches and the other overflows - the caller
    is expected to clip, which backgrounds want and placed images don't.
    """
    if w <= 0 or h <= 0:
        return box_w, box_h
    scale = max(box_w / w, box_h / h)
    return w * scale, h * scale


def _fit_contain(w: float, h: float, box_w: float, box_h: float) -> Tuple[float, float]:
    """Scale (w, h) to fit inside box_w x box_h, preserving aspect.

    Used for every non-background image. RULES.md requires non-bleed
    images to keep their source aspect within ~5%, and this is exact, so
    the rule holds by construction rather than by luck.
    """
    if w <= 0 or h <= 0:
        return box_w, box_h
    scale = min(box_w / w, box_h / h)
    return w * scale, h * scale


def _place_centered(size_w: float, size_h: float, slot: Rect) -> Rect:
    """Centre a (w, h) item inside a slot rect."""
    sx, sy, sw, sh = slot
    return (sx + (sw - size_w) / 2.0, sy + (sh - size_h) / 2.0, size_w, size_h)


def _unit_bbox(layout: "Layout", members: List[str]) -> Optional[List[float]]:
    """Union bounding box of `members` current placements, or None."""
    xs, ys, x2s, y2s = [], [], [], []
    for m in members:
        if m not in layout.placements:
            continue
        x, y, w, h = layout.placements[m]
        xs.append(x); ys.append(y); x2s.append(x + w); y2s.append(y + h)
    if not xs:
        return None
    return [min(xs), min(ys), max(x2s) - min(xs), max(y2s) - min(ys)]


def _clamp_to_canvas(rect: Rect, width: float, height: float) -> Rect:
    """Pull a rect fully inside the canvas, shrinking only if unavoidable.

    Positions are clamped rather than scaled when possible: nudging an
    element back inside preserves its size, which for a placed image is
    almost always preferable.
    """
    x, y, w, h = rect
    w = min(w, width)
    h = min(h, height)
    x = max(0.0, min(x, width - w))
    y = max(0.0, min(y, height - h))
    return (x, y, w, h)


class Layout:
    """Builds target-canvas rectangles for every non-background element.

    Usage is one pass: `assign()` decides where everything goes, then
    `resize()` reads the placements back out. Text sizing happens during
    assignment so that a slot's height accounts for the text that will
    actually be drawn into it.
    """

    def __init__(self, design: Design, dst_w: float, dst_h: float) -> None:
        self.design = design
        self.dst_w = float(dst_w)
        self.dst_h = float(dst_h)
        self.dst_aspect = self.dst_w / self.dst_h if self.dst_h else 1.0
        self.archetype = choose_archetype(design.aspect, self.dst_aspect)

        margin = design.margin * min(self.dst_w, self.dst_h)
        self.margin = margin
        self.usable = (
            self.dst_w - 2 * margin,
            self.dst_h - 2 * margin,
        )

        self.placements: Dict[str, Rect] = {}
        self.font_sizes: Dict[str, float] = {}
        self._measurer = design.measurer

    # -- entry point -----------------------------------------------------

    def assign(self) -> None:
        """Decide a rectangle for every element this layout owns."""
        builder = {
            "NEAR": self._build_near,
            "STACKED": self._build_stacked,
            "BANDED": self._build_banded,
            "STRIP": self._build_strip,
        }[self.archetype]
        builder()

        # Safety net: nothing this module emits may leave the canvas.
        for eid, rect in list(self.placements.items()):
            self.placements[eid] = _clamp_to_canvas(
                rect, self.dst_w, self.dst_h
            )

    # -- shared pieces ---------------------------------------------------

    def _font_metrics(self, e: Dict[str, Any]) -> Tuple[str, str, str, float]:
        """Extract the (family, weight, style, line_height) a text needs.

        Mirrors the renderer's own weight/style derivation, so the size we
        solve for is measured against the font that will actually draw.
        """
        bold = str(e.get("font_weight", "regular")).lower() == "bold"
        italic = str(e.get("font_style", "normal")).lower() == "italic"
        style = (
            "bolditalic" if bold and italic
            else "bold" if bold
            else "italic" if italic
            else "regular"
        )
        return (
            str(e.get("font_family", "Solstice Sans")),
            "bold" if bold else "regular",
            style,
            float(e.get("line_height", 1.2)),
        )

    def _fit_text_into(
        self,
        e: Dict[str, Any],
        slot: Rect,
        max_size: float,
        min_size: float = 1.0,
    ) -> None:
        """Solve a font size for `e` and record a snug box for it.

        Both dimensions are hard constraints, for the same reason: the
        renderer does not clip text. Overflow in either direction is drawn
        anyway, so an "approximately fits" box produces overlap rather
        than tight leading. Width is the constraint that binds first -
        greedy wrap has no answer for a word wider than the box - and
        height is what actually stops text from landing on its neighbour
        when elements are stacked.

        When even `min_size` can't fit, the smallest allowed size wins and
        the box reports the true overflow. The schema floors `font_size` at
        1, so there is no further move to make; emitting the honest box is
        better than emitting a lie that renders identically.
        """
        family, weight, style, line_height = self._font_metrics(e)
        sx, sy, sw, sh = slot
        sw = max(sw, 1.0)
        sh = max(sh, 1.0)

        # Upper bound: the proportionally scaled source size, so text never
        # grows beyond what the original design implied.
        upper = max(min_size, max_size)

        size = self._measurer.fit_size(
            e["content"], family, weight, style,
            sw, sh, line_height, upper=upper, lower=min_size,
        )

        # The size was solved against a safety-narrowed width, so the line
        # count here is an upper bound. Re-measure at the real width to
        # report the height the renderer will actually produce - that's
        # what the emitted box has to claim, or the JSON understates how
        # much room the text takes and the next element gets placed on top
        # of it.
        m = self._measurer.measure(
            e["content"], family, weight, style, size, sw, line_height,
            safe=False,
        )

        # Emit a box hugging the drawn text, centred in the slot. A snug box
        # keeps the JSON honest about what was rendered, which matters
        # because the structural checker reads these numbers.
        box_h = max(m["text_height"], 1.0)
        box_y = sy + max(0.0, (sh - box_h) / 2.0)
        self.placements[e["id"]] = (sx, box_y, sw, box_h)
        self.font_sizes[e["id"]] = size

    def _place_image(self, e: Dict[str, Any], slot: Rect) -> None:
        """Place a contained image centred in `slot`, aspect preserved."""
        w, h = _fit_contain(
            float(e["width"]), float(e["height"]), slot[2], slot[3]
        )
        self.placements[e["id"]] = _place_centered(w, h, slot)

    def _place_group(self, ids: Sequence[str], slot: Rect) -> None:
        """Place a grouped unit as one rigid block, preserving its internals.

        A badge is a shape plus its label; scaling them independently
        splits the label off its pill. So the group's bounding box is
        placed as a unit, every member is mapped through that one
        transform, and any text inside is re-solved for the space it
        actually got.
        """
        design = self.design
        gx, gy, gw, gh = design.group_bbox(ids)
        if gw <= 0 or gh <= 0:
            return

        sx, sy, sw, sh = slot
        # Preserve the group's own proportions, then cap how far it may
        # grow. A badge scaled to fill a tall banner column stops reading
        # as a badge - it becomes a slab.
        fit = min(sw / gw, sh / gh) if gw > 0 and gh > 0 else 1.0
        scale = min(fit, _GROUP_MAX_GROW)

        # A rigid scale can push a small label below readability on a short
        # canvas, so the label gets a floor. The floor is capped by the slot,
        # though: a pill that outgrows its slot doesn't look small, it looks
        # like it collided with its neighbours. Overlap is the worse failure,
        # so when space runs out the slot wins and the label gets as small as
        # the slot allows.
        floor = self._legibility_scale(ids, gw, gh)
        if floor > 0:
            scale = max(scale, min(floor, fit))

        nw, nh = gw * scale, gh * scale
        nx, ny = sx + (sw - nw) / 2.0, sy + (sh - nh) / 2.0

        for eid in ids:
            e = design.by_id[eid]
            rel_x = (e["x"] - gx) * scale
            rel_y = (e["y"] - gy) * scale
            rel_w = e["width"] * scale
            rel_h = e["height"] * scale

            if e["type"] == "text":
                # The label may exceed the rigid group scale when the
                # scale would take it below readability, bounded so a small
                # badge can't invert into a giant one. The solver then fits
                # it to the sub-box, which is what stops it from escaping
                # its own pill.
                scaled = self._scaled_font(e["id"], scale)
                src = float(e.get("font_size", 12))
                ceiling = scaled * _GROUP_TEXT_MAX_GROW
                max_size = min(
                    max(scaled, min(_GROUP_TEXT_MIN_PX, ceiling)),
                    max(ceiling, src * scale),
                )
                sub_slot = (nx + rel_x, ny + rel_y, rel_w, rel_h)
                self._fit_text_into(e, sub_slot, max_size=max_size)
            else:
                # Shapes inside a group keep their identity: same box as
                # the transform gave them.
                self.placements[eid] = (
                    nx + rel_x, ny + rel_y, rel_w, rel_h,
                )
                # Corner radius and stroke are in px, so they need the
                # same transform as the geometry or a scaled-up badge ends
                # up with visibly wrong rounding.
                self._rescale_stroke(eid, scale)

    def _legibility_scale(self, ids: Sequence[str], gw: float, gh: float) -> float:
        """Smallest group scale that keeps every label inside it readable.

        Solved against the group's own smallest text. Returns 0 when the
        rigid scale is already fine, so callers can treat it as a floor
        rather than a target.
        """
        design = self.design
        texts = [
            design.by_id[i] for i in ids if design.by_id[i]["type"] == "text"
        ]
        if not texts:
            return 0.0

        smallest = min(float(t.get("font_size", 12)) for t in texts)
        if smallest <= 0:
            return 0.0

        # Scale needed for the smallest label to clear the readability
        # floor, plus enough for the label to fit inside the group width.
        need = _GROUP_TEXT_MIN_PX / smallest

        # Width check: at that size, does the widest label still fit the
        # group's inner width? If not, grow further.
        widest = 0.0
        for t in texts:
            family, weight, style, _lh = self._font_metrics(t)
            m = self._measurer.measure(
                t["content"], family, weight, style,
                smallest * need, max(gw * 0.92, 1.0),
            )
            widest = max(widest, m["max_line_width"])

        if widest > gw * 0.92 and gw > 0:
            need *= (widest / (gw * 0.92))

        return min(need, _GROUP_MAX_GROW * _GROUP_TEXT_MAX_GROW / 1.0)

    def _scaled_font(self, eid: str, scale: float) -> float:
        e = self.design.by_id[eid]
        return max(1.0, float(e.get("font_size", 12)) * scale)

    def _rescale_stroke(self, eid: str, scale: float) -> None:
        e = self.design.by_id[eid]
        if "corner_radius" in e and e["corner_radius"]:
            e["corner_radius"] = max(0.0, e["corner_radius"] * scale)
        if e.get("stroke_width"):
            e["stroke_width"] = max(0.0, e["stroke_width"] * scale)

    def _mark_edges(self) -> Dict[str, Rect]:
        """Reserve the usable area for each edge mark (logo, watermark).

        Returns id -> the full usable rect the mark may position itself
        within. The mark picks its own corner from that, rather than being
        given a pre-sized slot.

        The slot has to be the whole area because `_place_edge_mark` pins a
        watermark by computing `slot[0] + slot[2] - w`. Handing it a slot
        already trimmed to the mark's own size collapses that to zero and
        parks the watermark in the top-left corner, on top of the logo.
        """
        design = self.design
        uw, uh = self.usable
        mx, my = self.margin, self.margin

        slots: Dict[str, Rect] = {}
        for eid in design.by_id:
            if design.roles[eid] in ("LOGO", "WATERMARK"):
                slots[eid] = (mx, my, uw, uh)
        return slots

    def _ordered_flow(self) -> List[Dict[str, Any]]:
        """Non-background content in a sensible reading order.

        Order is the one thing a resize must never lose: a CTA that
        drifts above the headline reads as a different design. Anchors
        first by convention (logo, watermark, focal), then the text
        hierarchy, then groups by their original vertical position.
        """
        design = self.design

        def rank(e: Dict[str, Any]) -> tuple:
            role = design.roles[e["id"]]
            if role in _ANCHORS:
                order = _ANCHORS.index(role)
            elif role in ("HEADLINE", "SUBHEAD", "BODY"):
                order = len(_ANCHORS)
            elif role == "GROUP":
                order = len(_ANCHORS) + 1
            else:
                order = len(_ANCHORS) + 2
            # Within a tier, original position breaks ties.
            return (order, e["y"], e["x"])

        # Elements inside a group are excluded here: they are placed as
        # part of their group's rigid unit, not as free-flowing content.
        # Letting them through the text flow would place a badge's label
        # independently of the pill it belongs to.
        flowable = [
            e for e in design.elements
            if not e.get("group_id")
            and design.roles[e["id"]] in (
                "FOCAL", "HEADLINE", "SUBHEAD", "BODY", "FINEPRINT",
            )
        ]
        return sorted(flowable, key=rank)

    def _priority(self, eid: str) -> int:
        """Content priority tier for `eid` (lower = must stay legible).

        Derived from the classified role, with content hints to promote a
        grouped CTA above a badge. Edge marks are not priority content -
        they keep their own fixed-readable-size rule in `_place_edge_mark`.
        """
        design = self.design
        role = design.roles[eid]
        if role == "HEADLINE" or role == "FOCAL":
            return _PRIORITY_P0
        if role == "GROUP":
            ids = design.groups_by_id.get(eid) if hasattr(design, "groups_by_id") else None
            # groups_by_id doesn't exist; look up by scanning groups below.
            for gid, members in design.groups.items():
                if eid in members:
                    label = " ".join(
                        str(design.by_id[m].get("content", "")) for m in members
                        if design.by_id[m]["type"] == "text"
                    ).lower()
                    if any(h in label for h in _CTA_HINTS):
                        return _PRIORITY_P0
                    return _PRIORITY_P1
            return _PRIORITY_P1
        if role == "SUBHEAD" or role == "BODY" or role == "TEXT":
            return _PRIORITY_P1
        if role == "FINEPRINT":
            return _PRIORITY_P2
        return _PRIORITY_P1

    def _columns(self, area: Rect, weights: Sequence[float], gap: float) -> List[Rect]:
        """Split `area` left-to-right into columns sized by `weights`.

        A shared allocator, because doing this arithmetic inline is how the
        text and group columns ended up stacked on top of each other in
        the first version: each branch was computing its own x-offset from
        a different set of terms.
        """
        ax, ay, aw, ah = area
        total_gap = gap * max(0, len(weights) - 1)
        avail = max(0.0, aw - total_gap)
        wsum = sum(weights)

        rects: List[Rect] = []
        x = ax
        for w in weights:
            cw = avail * (w / wsum) if wsum > 0 else 0.0
            rects.append((x, ay, cw, ah))
            x += cw + gap
        return rects

    def _place_focal(self, slot: Rect) -> None:
        design = self.design
        for e in design.elements:
            if design.roles[e["id"]] == "FOCAL":
                self._place_image(e, slot)

    def _groups_in_order(self) -> List[List[str]]:
        """Group units, ordered top-to-bottom then left-to-right.

        Two badges sitting side by side at the same height must keep that
        order, or "NEW FLAVOR" ends up left of "AWARD WINNING" after a
        reflow that had no reason to reorder them.
        """
        design = self.design
        units = []
        for gid, ids in design.groups.items():
            gx, gy, gw, gh = design.group_bbox(ids)
            units.append((round(gy, 1), round(gx, 1), ids))
        return [ids for _y, _x, ids in sorted(units)]

    def _place_groups(
        self,
        area: Rect,
        same_row: bool,
        gap: Optional[float] = None,
    ) -> None:
        """Place every group unit inside `area`.

        `same_row` means the target is wide enough that the groups read as
        one horizontal band - so they're laid out left to right sharing the
        band's height. Otherwise they stack vertically.
        """
        units = self._groups_in_order()
        if not units:
            return

        ax, ay, aw, ah = area
        if gap is None:
            gap = self._gap()

        if same_row:
            # Share the band by original relative width, so a wide CTA
            # stays wider than a small badge.
            weights = []
            for ids in units:
                _gx, _gy, gw, _gh = self.design.group_bbox(ids)
                weights.append(max(gw, 1.0))
            total = sum(weights)

            gaps = gap * max(0, len(units) - 1)
            avail = max(1.0, aw - gaps)
            # If the natural widths don't fit the row, compress them rather
            # than letting each group claim its proportional share and run
            # past the edge of the column.
            k = min(1.0, avail / max(total, 1e-6))

            cursor = ax
            for ids, w in zip(units, weights):
                slot_w = max(1.0, (w / total) * avail)
                self._place_group(ids, (cursor, ay, slot_w, ah))
                cursor += slot_w * k + gap
        else:
            weights = []
            for ids in units:
                _gx, _gy, _gw, gh = self.design.group_bbox(ids)
                weights.append(max(gh, 1.0))
            total = sum(weights)
            gaps = gap * max(0, len(units) - 1)
            avail = max(1.0, ah - gaps)
            cursor = ay
            for ids, h in zip(units, weights):
                slot_h = max(1.0, (h / total) * avail)
                self._place_group(ids, (ax, cursor, aw, slot_h))
                cursor += slot_h + gap

    # -- archetypes ------------------------------------------------------

    def _build_near(self) -> None:
        """Proportional scale. Correct when the aspect barely changes."""
        design = self.design
        scale = min(self.dst_w / design.width, self.dst_h / design.height)
        offset_x = (self.dst_w - design.width * scale) / 2.0
        offset_y = (self.dst_h - design.height * scale) / 2.0

        for eid, e in design.by_id.items():
            role = design.roles[eid]
            if role in ("FULL_BLEED", "DECORATIVE"):
                continue
            if role in ("LOGO", "WATERMARK"):
                continue  # already edge-anchored at fixed size

            rect = (
                e["x"] * scale + offset_x,
                e["y"] * scale + offset_y,
                e["width"] * scale,
                e["height"] * scale,
            )
            if e["type"] == "text":
                self._fit_text_into(
                    e, rect, max_size=float(e.get("font_size", 12)) * scale
                )
            else:
                self.placements[eid] = rect

        # Edge marks ride along in the scaled coordinate space.
        for eid, e in design.by_id.items():
            role = design.roles[eid]
            if role not in ("LOGO", "WATERMARK"):
                continue
            rect = (
                e["x"] * scale + offset_x,
                e["y"] * scale + offset_y,
                e["width"] * scale,
                e["height"] * scale,
            )
            self.placements[eid] = rect

    def _build_stacked(self) -> None:
        """Target is taller: keep the column, spread it out.

        Vertical order is preserved exactly - logo, image, copy, then the
        grouped calls to action - because in a taller format the
        composition stays vertical and only the spacing changes.
        """
        design = self.design
        marks = self._mark_edges()
        for eid, slot in marks.items():
            self._place_edge_mark(eid, slot)
        logo = next(
            (s for e, s in marks.items() if design.roles[e] == "LOGO"), None
        )
        watermark = next(
            (s for e, s in marks.items() if design.roles[e] == "WATERMARK"), None
        )

        mx, my = self.margin, self.margin
        uw, uh = self.usable
        gap = self._gap()

        # Reserve against what the marks were ACTUALLY placed at, read from
        # self.placements rather than from the slots handed to
        # `_place_edge_mark` - those are the whole usable area, so their
        # height is the full canvas and reserving it collapsed the band to
        # nothing.
        top = my
        bottom = self.dst_h - my
        if logo:
            top = max(top, logo[1] + logo[3] + gap * 0.5)
        if watermark:
            bottom = min(bottom, watermark[1] - gap * 0.5)

        focal = self._focal_of()
        text_flow = [e for e in self._ordered_flow() if e["type"] == "text"]
        groups = self._groups_in_order()

        # Budget the band: the image takes a fixed share, the copy gets the
        # space it needs, and the groups get a share proportional to their
        # source heights.
        band = max(0.0, bottom - top)
        units = int(focal is not None) + int(bool(text_flow)) + int(bool(groups))
        if units == 0 or band <= 0:
            return

        focal_h = band * _FOCAL_SHARE["STACKED"] if focal else 0.0
        group_src_h = sum(
            max(1.0, design.group_bbox(ids)[3]) for ids in groups
        )
        # Reserve a slice for the groups proportional to their share of the
        # design's own group height, so a single CTA doesn't get the same
        # band as a row of three badges.
        group_h = 0.0
        if groups:
            design_group_h = group_src_h
            group_h = min(
                band * 0.34,
                band * (group_src_h / max(design_group_h + band * 0.6, 1e-6)),
            )

        text_h = max(0.0, band - focal_h - group_h)

        cursor = top
        if focal:
            self._place_focal((mx, cursor, uw, focal_h))
            cursor += focal_h + gap
        if text_flow and text_h > 0:
            self._stack_text(text_flow, (mx, cursor, uw, text_h))
            cursor += text_h + gap
        if groups and group_h > 0:
            self._place_groups((mx, cursor, uw, group_h), same_row=False)

    def _build_banded(self) -> None:
        """Target is wider: vertical stack becomes horizontal bands.

        Three columns - image, copy, calls to action. The groups move to
        their own column because a badge row beside a 56px headline needs
        the full height to itself, and because the source's horizontal
        badge row maps most naturally onto a column edge in a banner.
        """
        design = self.design
        marks = self._mark_edges()
        for _eid, _slot in marks.items():
            self._place_edge_mark(_eid, _slot)
        logo = next(
            (self.placements[e] for e in marks
             if design.roles[e] == "LOGO"), None
        )
        watermark = next(
            (self.placements[e] for e in marks
             if design.roles[e] == "WATERMARK"), None
        )

        mx, my = self.margin, self.margin
        uw, uh = self.usable
        gap = self._gap()

        top = my
        bottom = self.dst_h - my
        if logo:
            top = max(top, logo[1] + logo[3] + gap * 0.5)
        if watermark:
            bottom = min(bottom, watermark[1] - gap * 0.5)

        focal = self._focal_of()
        text_flow = [e for e in self._ordered_flow() if e["type"] == "text"]
        groups = self._groups_in_order()

        if not (focal or text_flow or groups):
            return

        band = max(0.0, bottom - top)

        # Column weights as percentages, so the copy always gets whatever
        # the image and groups don't claim.
        img_pct = _FOCAL_SHARE["BANDED"] * 100 if focal else 0.0
        groups_pct = 26.0 if groups else 0.0
        text_pct = max(0.0, 100.0 - img_pct - groups_pct)

        cols = self._columns(
            (mx, top, uw, band),
            [w for w in (img_pct, text_pct, groups_pct) if w > 0],
            gap,
        )
        slots = iter(cols)

        if focal:
            self._place_focal(next(slots))
        if text_flow:
            slot = next(slots) if text_pct > 0 else None
            if slot and slot[2] > 1.0:
                self._stack_text(text_flow, slot)
        if groups:
            slot = next(slots) if groups_pct > 0 else None
            if slot and slot[2] > 1.0:
                # Badges read as a row relative to each other in the
                # source, so they stack here while keeping source order.
                self._place_groups(slot, same_row=False)

    def _build_strip(self) -> None:
        """Extreme delta: one dominant axis.

        A 728x90 target has 90px of height for content composed vertically.
        The previous version let the first P0 item consume every remaining
        pixel, which left subhead, badges, ribbon and legal unplaced - they
        fell back to source size + clamp and overlapped everything.

        Instead we allocate fixed per-tier bands so *every* element is
        placed on-canvas:

          P0 headline  - top band, readability floor enforced
          P1 subhead   - just below the headline
          P1 groups    - laid out side-by-side (columns) across the width,
                         not stacked, so badges keep real width
          P0 CTA       - bottom band, readability floor enforced
          P2 legal     - bottom sliver, minimal but honest

        Tiers never borrow from each other, so the headline/CTA cannot be
        starved into illegibility. Anything that still doesn't fit is parked
        at the trailing edge with a valid box rather than being dropped.
        """
        design = self.design
        marks = self._mark_edges()
        for _eid, _slot in marks.items():
            self._place_edge_mark(_eid, _slot)
        logo = next(
            (self.placements[e] for e in marks
             if design.roles[e] == "LOGO"), None
        )
        watermark = next(
            (self.placements[e] for e in marks
             if design.roles[e] == "WATERMARK"), None
        )

        mx, my = self.margin, self.margin
        uw, uh = self.usable
        gap = self._gap()
        mark_w = (watermark[2] + gap) if watermark else 0.0

        focal = self._focal_of()
        text_flow = [e for e in self._ordered_flow() if e["type"] == "text"]
        groups = self._groups_in_order()
        band = max(0.0, self.dst_h - 2 * my)
        if band <= 0:
            return

        if self.dst_aspect >= 1.0:
            # Horizontal strip: full-height band, tiers stacked top-to-bottom.
            area_x = mx
            area_w = max(0.0, uw - mark_w)
            if logo:
                shift = logo[2] + gap
                if area_w - shift > 0:
                    area_x += shift
                    area_w -= shift

            # Reserve a modest leading slice for the focal image so it is not
            # cropped to a meaningless sliver, but do not let it starve copy.
            focal_w = 0.0
            if focal:
                focal_w = min(area_w * 0.26, uw * 0.30)
                area_x += focal_w + gap
                area_w -= focal_w + gap

            if area_w <= 1.0:
                return

            # Fixed tier heights as fractions of the band.
            headline_h = band * 0.30
            subhead_h = band * 0.14
            groups_h = band * 0.16
            cta_h = band * 0.22
            legal_h = band * 0.10

            # Reserve the bottom bands for the P0 CTA and P2 legal text so
            # they always hold their ground instead of falling through to
            # microscopic parking. Top-down content is capped at cta_y.
            cta_y = my + band - legal_h - cta_h
            legal_y = my + band - legal_h

            cursor = my
            # P0 headline at the top, full remaining width.
            headline = next(
                (e for e in text_flow if design.roles[e["id"]] == "HEADLINE"),
                None,
            )
            if headline is not None and cursor < cta_y:
                room = max(1.0, cta_y - cursor)
                cursor += self._place_text_budget(
                    headline, area_x, cursor, area_w, min(headline_h, room),
                    _READABILITY_FLOOR_P0,
                ) + gap
                text_flow = [e for e in text_flow if e["id"] != headline["id"]]

            # P1 badge/ribbon groups as side-by-side columns across the full
            # width. On a narrow strip a badge row keeps real width (and thus
            # legible text) far better than a stacked column, so badges claim
            # the middle band before the long subhead paragraph.
            p1_groups = [
                ids for ids in groups
                if self._group_priority(ids) == _PRIORITY_P1
            ]
            if p1_groups and cursor + groups_h <= cta_y:
                self._place_p1_row(
                    p1_groups, area_x, cursor, area_w, groups_h, gap
                )
                cursor += groups_h + gap

            # P1 subhead below the badges, only if room remains above the CTA.
            # On an extreme strip there isn't room for headline + badges +
            # a multi-line subhead + CTA + legal all at once, so subhead
            # (P1) yields to the reserved P0 CTA band rather than colliding.
            subhead = next(
                (e for e in text_flow
                 if design.roles[e["id"]] == "SUBHEAD"),
                None,
            )
            if subhead is not None and cursor < cta_y:
                room = max(1.0, cta_y - cursor)
                needed = self._text_height_at(
                    subhead, area_w, _READABILITY_FLOOR_P1
                )
                if needed <= room:
                    cursor += self._place_text_budget(
                        subhead, area_x, cursor, area_w,
                        min(subhead_h, room), _READABILITY_FLOOR_P1,
                    ) + gap
                    text_flow = [
                        e for e in text_flow if e["id"] != subhead["id"]
                    ]
                # else: subhead cannot fit above the CTA; leave it to be
                # parked as a minimal box at the trailing edge.

            # P0 primary CTA at its reserved bottom band - always placed,
            # it holds its ground against anything above it.
            cta_group = next(
                (ids for ids in groups
                 if self._group_priority(ids) == _PRIORITY_P0),
                None,
            )
            cta_bottom = None
            if cta_group is not None:
                self._place_group_budget(
                    cta_group, area_x, cta_y, area_w, cta_h
                )
                groups = [ids for ids in groups if ids != cta_group]
                # Read the CTA's *actual* placed bottom (the group is centred
                # in its slot, so it can differ from the formula) and start
                # legal strictly below it so the two can never overlap.
                cta_bottom = max(
                    self.placements[m][1] + self.placements[m][3]
                    for m in cta_group
                    if m in self.placements
                )

            # P2 legal at the very bottom sliver.
            fineprint = next(
                (e for e in text_flow
                 if design.roles[e["id"]] == "FINEPRINT"),
                None,
            )
            if fineprint is not None:
                # Start legal below the CTA's actual bottom (or at the
                # reserved sliver when there is no CTA), never overlapping it.
                legal_anchor = cta_bottom if cta_bottom is not None else legal_y
                if legal_anchor > cursor:
                    cursor = max(cursor, legal_anchor)
                    cursor += self._place_text_budget(
                        fineprint, area_x, legal_anchor, area_w, legal_h,
                        _READABILITY_FLOOR_P2,
                    )

            # Park any still-unplaced elements at the trailing edge so they
            # never fall back to source size + clamp.
            remaining_text = [
                e for e in text_flow
                if e["id"] not in self.placements
            ]
            remaining_groups = [
                ids for ids in groups
                if not any(m in self.placements for m in ids)
            ]
            self._park_leftovers(
                remaining_text, remaining_groups, area_x, area_w, gap
            )

            # Focal image keeps its reserved leading slice, full band height.
            if focal:
                self._place_focal((mx, my, max(1.0, focal_w - gap), band))
        else:
            # Tall strip: same tier budgeting, transposed left-to-right.
            area_y = my
            area_h = max(0.0, uh - (mark_w if watermark else 0.0))
            if logo:
                shift = logo[3] + gap
                if area_h - shift > 0:
                    area_y += shift
                    area_h -= shift

            focal_h = 0.0
            if focal:
                focal_h = min(area_h * 0.26, uh * 0.30)
                area_y += focal_h + gap
                area_h -= focal_h + gap

            if area_h <= 1.0:
                return

            headline_w = uw * 0.30
            subhead_w = uw * 0.14
            groups_w = uw * 0.16
            cta_w = uw * 0.22
            legal_w = uw * 0.10

            # Reserve the trailing bands for the P0 CTA and P2 legal text so
            # they always hold their ground instead of falling through to
            # microscopic parking. Left-to-right content is capped at cta_x.
            cta_x = mx + uw - legal_w - cta_w
            legal_x = mx + uw - legal_w

            cursor = mx
            headline = next(
                (e for e in text_flow if design.roles[e["id"]] == "HEADLINE"),
                None,
            )
            if headline is not None and cursor < cta_x:
                room = max(1.0, cta_x - cursor)
                cursor += self._place_text_budget_transposed(
                    headline, cursor, area_y, min(headline_w, room), area_h,
                    _READABILITY_FLOOR_P0,
                ) + gap
                text_flow = [e for e in text_flow if e["id"] != headline["id"]]

            subhead = next(
                (e for e in text_flow
                 if design.roles[e["id"]] == "SUBHEAD"),
                None,
            )
            if subhead is not None and cursor < cta_x:
                room = max(1.0, cta_x - cursor)
                cursor += self._place_text_budget_transposed(
                    subhead, cursor, area_y, min(subhead_w, room), area_h,
                    _READABILITY_FLOOR_P1,
                ) + gap
                text_flow = [e for e in text_flow if e["id"] != subhead["id"]]

            p1_groups = [
                ids for ids in groups
                if self._group_priority(ids) == _PRIORITY_P1
            ]
            if p1_groups and cursor + groups_w <= cta_x:
                self._place_p1_column(
                    p1_groups, cursor, area_y, groups_w, area_h, gap
                )
                cursor += groups_w + gap

            cta_group = next(
                (ids for ids in groups
                 if self._group_priority(ids) == _PRIORITY_P0),
                None,
            )
            if cta_group is not None:
                self._place_group_budget_transposed(
                    cta_group, cta_x, area_y, cta_w, area_h
                )
                groups = [ids for ids in groups if ids != cta_group]

            fineprint = next(
                (e for e in text_flow
                 if design.roles[e["id"]] == "FINEPRINT"),
                None,
            )
            if fineprint is not None and legal_x > cursor:
                cursor = max(cursor, legal_x)
                cursor += self._place_text_budget_transposed(
                    fineprint, legal_x, area_y, legal_w, area_h,
                    _READABILITY_FLOOR_P2,
                )

            remaining_text = [
                e for e in text_flow
                if e["id"] not in self.placements
            ]
            remaining_groups = [
                ids for ids in groups
                if not any(m in self.placements for m in ids)
            ]
            self._park_leftovers_transposed(
                remaining_text, remaining_groups, mx + uw, area_y, area_h, gap
            )

            if focal:
                self._place_focal((mx, my, max(1.0, focal_h - gap), band))

    # -- strip-tier placement helpers -------------------------------------

    def _group_priority(self, ids: List[str]) -> int:
        """Priority of a group, driven by its label text (CTA hint -> P0)."""
        design = self.design
        label_ids = [i for i in ids if design.by_id[i]["type"] == "text"]
        if not label_ids:
            return _PRIORITY_P1
        label = " ".join(
            str(design.by_id[i].get("content", "")) for i in label_ids
        ).lower()
        if any(h in label for h in _CTA_HINTS):
            return _PRIORITY_P0
        return _PRIORITY_P1

    def _text_height_at(self, e: Dict[str, Any], box_w: float, size: float) -> float:
        """Rendered text height of `e` at `size` inside `box_w` width."""
        family, weight, style, lh = self._font_metrics(e)
        m = self._measurer.measure(
            e["content"], family, weight, style, size, box_w, lh, safe=False
        )
        return max(m["text_height"], 1.0)

    def _place_text_budget(
        self, e: Dict[str, Any], area_x: float, cursor: float,
        area_w: float, budget_h: float, floor: float,
    ) -> float:
        """Solve a font size for `e` inside a fixed-height slot and return
        the emitted box height."""
        scaled = max(1.0, float(e.get("font_size", 12)))
        slot = (area_x, cursor, area_w, max(budget_h, 1.0))
        self._fit_text_into(e, slot, max_size=scaled, min_size=floor)
        return self.placements[e["id"]][3]

    def _place_text_budget_transposed(
        self, e: Dict[str, Any], cursor: float, area_y: float,
        budget_w: float, area_h: float, floor: float,
    ) -> float:
        """Transpose of `_place_text_budget` for a tall strip."""
        scaled = max(1.0, float(e.get("font_size", 12)))
        slot = (cursor, area_y, max(budget_w, 1.0), area_h)
        self._fit_text_into(e, slot, max_size=scaled, min_size=floor)
        return self.placements[e["id"]][2]

    def _place_group_budget(
        self, ids: List[str], area_x: float, cursor: float,
        area_w: float, budget_h: float,
    ) -> None:
        """Place a group rigidly inside a fixed-height slot."""
        self._place_group(ids, (area_x, cursor, area_w, max(budget_h, 1.0)))

    def _place_group_budget_transposed(
        self, ids: List[str], cursor: float, area_y: float,
        budget_w: float, area_h: float,
    ) -> None:
        """Transpose of `_place_group_budget`."""
        self._place_group(ids, (cursor, area_y, max(budget_w, 1.0), area_h))

    def _place_p1_row(
        self, groups: List[List[str]], area_x: float, cursor: float,
        area_w: float, budget_h: float, gap: float,
    ) -> None:
        """Lay out P1 groups side-by-side (columns) across `area_w`, each
        taking the full `budget_h` height. Used by the horizontal strip so
        badges keep real width instead of being stacked into slivers."""
        design = self.design
        if not groups:
            return
        weights = [
            max(design.group_bbox(ids)[2], 1.0) for ids in groups
        ]
        total = max(sum(weights), 1e-6)
        x = area_x
        for ids, w in zip(groups, weights):
            slot_w = max(1.0, area_w * (w / total))
            self._place_group_budget(ids, x, cursor, slot_w, budget_h)
            x += slot_w + gap

    def _place_p1_column(
        self, groups: List[List[str]], cursor: float, area_y: float,
        budget_w: float, area_h: float, gap: float,
    ) -> None:
        """Transpose of `_place_p1_row` for a tall strip."""
        design = self.design
        if not groups:
            return
        weights = [
            max(design.group_bbox(ids)[3], 1.0) for ids in groups
        ]
        total = max(sum(weights), 1e-6)
        y = area_y
        for ids, h in zip(groups, weights):
            slot_h = max(1.0, area_h * (h / total))
            self._place_group_budget_transposed(
                ids, cursor, area_y, budget_w, slot_h
            )
            y += slot_h + gap

    def _park_leftovers(
        self, text_flow: List[Dict[str, Any]], groups: List[List[str]],
        area_x: float, area_w: float, gap: float,
    ) -> None:
        """Give any still-unplaced element a valid on-canvas box at the
        bottom edge (below any reserved CTA/legal band), so it never
        collides with the main content bands. This is a safety net; the
        tier bands above place nearly everything."""
        design = self.design
        y = self.dst_h
        for e in text_flow:
            if e["id"] in self.placements:
                continue
            slot = (max(0.0, area_x), max(0.0, y - gap), area_w, gap)
            self._fit_text_into(
                e, slot, max_size=max(1.0, float(e.get("font_size", 12))),
                min_size=_READABILITY_FLOOR_P2,
            )
            y = self.placements[e["id"]][1] - gap
        for ids in groups:
            if any(m in self.placements for m in ids):
                continue
            self._place_group_budget(
                ids, max(0.0, area_x), max(0.0, y - gap), area_w, gap
            )
            y -= gap * 2

    def _park_leftovers_transposed(
        self, text_flow: List[Dict[str, Any]], groups: List[List[str]],
        edge_x: float, band_top: float, band_height: float, gap: float,
    ) -> None:
        """Transpose of `_park_leftovers` for a tall strip: park at the
        trailing (right) edge, stacking leftward from edge_x."""
        design = self.design
        x = edge_x
        for e in text_flow:
            if e["id"] in self.placements:
                continue
            slot = (x - gap, band_top, gap, max(band_height, 1.0))
            self._fit_text_into(
                e, slot, max_size=max(1.0, float(e.get("font_size", 12))),
                min_size=_READABILITY_FLOOR_P2,
            )
            x -= self.placements[e["id"]][2] + gap
        for ids in groups:
            if any(m in self.placements for m in ids):
                continue
            self._place_group_budget_transposed(
                ids, x - gap, band_top, gap, max(band_height, 1.0)
            )
            x -= gap * 2

    # -- flow helpers ----------------------------------------------------

    def _gap(self) -> float:
        """Space between stacked items, carrying the source's rhythm."""
        return self.design.margin * min(self.dst_w, self.dst_h) * 0.6

    def _focal_of(self) -> Optional[Dict[str, Any]]:
        for e in self.design.elements:
            if self.design.roles[e["id"]] == "FOCAL":
                return e
        return None

    def _place_edge_mark(self, eid: str, slot: Rect) -> None:
        """Place one logo/watermark at a fixed readable size, aspect intact.

        Sized against the *source* design's short side and capped by the
        target's, then contain-fitted, so a lockup is never stretched:
        clamping width and height independently is what turned a 320x110
        logo into a 238x110 one on the first pass.

        Takes a single id deliberately. This used to loop over every mark,
        so the call that placed the logo also placed the watermark, and the
        call that placed the watermark moved both again - leaving the logo
        pinned to the bottom-right corner on top of the watermark.
        """
        design = self.design
        role = design.roles[eid]
        e = design.by_id[eid]

        ref = min(design.width, design.height)
        target_short = min(self.dst_w, self.dst_h)
        cap = min(
            ref * _EDGE_MARK_MAX_SHARE,
            target_short * _EDGE_MARK_TARGET_SHARE,
        )
        if role == "WATERMARK":
            cap = min(cap, target_short * _WATERMARK_MAX_SHARE)

        w, h = _fit_contain(
            float(e["width"]), float(e["height"]),
            min(slot[2], cap), min(slot[3], cap),
        )

        sx, sy = slot[0], slot[1]
        if role == "WATERMARK":
            # Pinned to the trailing corner of whatever space it's given.
            sx = slot[0] + slot[2] - w
            sy = slot[1] + slot[3] - h
        self.placements[eid] = (sx, sy, w, h)

    def _stack_text(self, flow: Sequence[Dict[str, Any]], area: Rect) -> None:
        """Distribute text elements vertically through `area`.

        Each element's height is its *natural* scaled height, then the
        leftover space is shared out as extra leading. That's what keeps
        a spread-out banner from looking like two unrelated text blobs:
        the elements stay visually related by a consistent gap.
        """
        if not flow:
            return

        ax, ay, aw, ah = area
        gap = self._gap()
        items = list(flow)

        # Natural heights at the proportionally scaled font size, so the
        # distribution reflects the design's own proportions.
        heights: List[float] = []
        for e in items:
            family, weight, style, lh = self._font_metrics(e)
            scaled = max(1.0, float(e.get("font_size", 12)))
            m = self._measurer.measure(
                e["content"], family, weight, style, scaled, aw, lh
            )
            heights.append(max(m["text_height"], 1.0))

        total = sum(heights) + gap * (len(items) - 1)
        if total <= ah:
            # Fits with room to spare: distribute the slack evenly so the
            # block stays centred rather than bunched at the top.
            slack = (ah - total) / len(items)
            heights = [h + slack for h in heights]
        else:
            # Over budget. The heights MUST be scaled down here rather than
            # left at their natural size: these are slot budgets, and a slot
            # larger than the band hands the font solver permission to set a
            # size whose text spills off the canvas and over its neighbours.
            budget = max(ah - gap * (len(items) - 1), 1.0)
            k = budget / max(sum(heights), 1e-6)
            heights = [max(h * k, 1.0) for h in heights]

        cursor = ay
        for e, h in zip(items, heights):
            slot = (ax, cursor, aw, h)
            family, weight, style, lh = self._font_metrics(e)
            scaled = max(1.0, float(e.get("font_size", 12)))
            self._fit_text_into(e, slot, max_size=scaled)
            # Advance by the height we actually emitted, not the estimate,
            # so a re-solved smaller font doesn't leave a gap.
            emitted = self.placements[e["id"]][3]
            cursor += emitted + gap

    def _row_text(self, flow: Sequence[Dict[str, Any]], area: Rect) -> None:
        """Distribute text elements horizontally through `area`.

        The transpose of `_stack_text`, and the reason a banner's copy
        reads left-to-right rather than as a stack of unrelated words.
        """
        if not flow:
            return

        ax, ay, aw, ah = area
        gap = self._gap()
        items = list(flow)

        widths: List[float] = []
        for e in items:
            family, weight, style, lh = self._font_metrics(e)
            scaled = max(1.0, float(e.get("font_size", 12)))
            m = self._measurer.measure(
                e["content"], family, weight, style, scaled, aw, lh
            )
            widths.append(max(m["max_line_width"], 1.0))

        total = sum(widths) + gap * (len(items) - 1)
        if total > aw:
            # Over budget: shrink proportionally and let the font solver
            # take up the slack.
            scale = (aw - gap * (len(items) - 1)) / max(total - gap * (len(items) - 1), 1e-6)
            widths = [w * max(scale, 0.05) for w in widths]
            total = sum(widths) + gap * (len(items) - 1)

        cursor = ax
        for e, w in zip(items, widths):
            slot = (cursor, ay, w, ah)
            scaled = max(1.0, float(e.get("font_size", 12)))
            self._fit_text_into(e, slot, max_size=scaled)
            cursor += w + gap

    # -- accessors -------------------------------------------------------

    def describe(self) -> str:
        """Debug summary of what the archetype decided."""
        return (
            f"archetype={self.archetype} "
            f"(src aspect {self.design.aspect:.3f} -> dst {self.dst_aspect:.3f}), "
            f"margin={self.margin:.1f}px, usable={self.usable[0]:.0f}x{self.usable[1]:.0f}"
        )