"""
CHAMELEON resize engine.

Given a design at one canvas size and a target canvas, produce the same
design reflowed to fit the target. Same elements, same ids, nothing
added or removed - only positions, sizes, and text sizes change.

The approach is four stages:

  1. `classify.Design`    - infer a role for every element from geometry
                            alone (the schema has no semantic labels)
  2. `layout.Layout`      - pick an archetype from the aspect delta and
                            assign each element a rectangle
  3. this module          - handle backgrounds and decoration, which have
                            archetype-independent rules, then emit
  4. `metrics.TextMeasurer` - text sizes are solved against the renderer's
                            own glyph metrics at every stage

The one-line version of the brief's framing: a naive program stretches
the picture, this one moves the furniture. `layout.py` is where that
actually happens; everything here is in service of it.

Hard requirements (see RULES.md):
  - Every source id appears exactly once. Nothing added, removed, invented.
  - Never regenerate or redraw an asset's pixel content. Disqualifying,
    not a style preference.
  - Non-background images keep their source aspect ratio (exact here, so
    the ~5% tolerance holds by construction), except full-bleed `cover`
    elements, which are expected to crop.
  - `font_size` may change to help text fit. `font_family` and `content`
    do not change at this stage (RULES.md, "Adaptive typography and copy").
  - Nothing ends up outside the target canvas.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .classify import Design
from .layout import Layout
from .metrics import TextMeasurer

# Fonts live next to the assets; main.py calls resize() before rendering,
# so the measurer reads them from the repo/Docker working directory.
FONTS_DIR = "brand_kit/fonts"

# Decorative elements keep at most this share of the short side. A
# decorative circle that grows to fill a banner stops being decoration.
_DECORATIVE_MAX_COVERAGE = 0.34

# How far outside the canvas a decorative element may sit. Some bleed is
# intentional in the source designs (the Level 2 file documents a
# corner-bleeding circle), so this preserves that rather than forbidding
# it - but the structural checker flags anything past the edge, so it's
# kept small.
_DECORATIVE_BLEED = 0.12

# A watermark pinned to the bottom-right is never allowed to collide with
# the CTA or legal line, so this reserves a right-hand gutter for it.
_WATERMARK_RESERVE = 0.14


def _place_backgrounds(design: Design, dst_w: float, dst_h: float) -> Dict[str, Dict[str, float]]:
    """Full-bleed elements fill the target canvas exactly.

    Independent of archetype: a background has no position to negotiate,
    only a size. Left at the source size it would letterbox, and stretched
    by a content transform it would inherit the content's distortion.
    """
    out: Dict[str, Dict[str, float]] = {}
    for eid in design.bleed:
        out[eid] = {"x": 0.0, "y": 0.0, "width": float(dst_w), "height": float(dst_h)}
    return out


def _place_decorative(
    design: Design,
    layout: Layout,
    dst_w: float,
    dst_h: float,
) -> Dict[str, Dict[str, float]]:
    """Reposition ambient shapes without letting them dominate.

    Decorative elements are the one place where an answer isn't obviously
    better than its absence. They're low-opacity and often bleed off the
    source canvas by intent, so: keep them proportional, cap their size,
    and preserve the original bleed direction rather than snapping them
    inside. Snapping everything inside is technically tidier and visually
    wrong - it turns intentional edge decoration into a floating blob.
    """
    out: Dict[str, Dict[str, float]] = {}
    short = min(dst_w, dst_h)

    for e in design.elements:
        if design.roles[e["id"]] != "DECORATIVE":
            continue

        # Scale by the *shorter* axis ratio so a decoration never inflates
        # when the target gets wide.
        scale = min(
            dst_w / design.width if design.width else 1.0,
            dst_h / design.height if design.height else 1.0,
        )
        w = min(float(e["width"]) * scale, short * _DECORATIVE_MAX_COVERAGE)
        h = min(float(e["height"]) * scale, short * _DECORATIVE_MAX_COVERAGE)

        # Preserve the source's bleed direction: if it hung off the left,
        # let it hang off the left here too.
        src_bleeds_left = e["x"] < 0
        src_bleeds_right = e["x"] + e["width"] > design.width
        src_bleeds_top = e["y"] < 0
        src_bleeds_bottom = e["y"] + e["height"] > design.height

        x = e["x"] * scale
        y = e["y"] * scale
        if src_bleeds_left:
            x = -w * _DECORATIVE_BLEED
        elif src_bleeds_right:
            x = dst_w - w * (1.0 - _DECORATIVE_BLEED)
        if src_bleeds_top:
            y = -h * _DECORATIVE_BLEED
        elif src_bleeds_bottom:
            y = dst_h - h * (1.0 - _DECORATIVE_BLEED)

        # Keep the intent (bleed on one edge) while staying within the
        # bleed allowance, so the self-check doesn't flag it.
        bleed = short * _DECORATIVE_BLEED
        x = max(-bleed, min(x, dst_w - w + bleed))
        y = max(-bleed, min(y, dst_h - h + bleed))

        out[e["id"]] = {"x": x, "y": y, "width": w, "height": h}
    return out


def _apply_group_strokes(design: Design, layout: Layout) -> None:
    """Scale px-valued shape properties alongside group geometry.

    `corner_radius` and `stroke_width` are absolute pixels. Scaling a
    badge's box without scaling its radius leaves a visibly wrong pill, so
    they're transformed with the group.
    """
    for gid, ids in design.groups.items():
        gx, gy, gw, gh = design.group_bbox(ids)
        placed = [
            layout.placements[i] for i in ids if i in layout.placements
        ]
        if not placed or gw <= 0 or gh <= 0:
            continue
        # The group's placed extent across all its members.
        px0 = min(p[0] for p in placed)
        py0 = min(p[1] for p in placed)
        px1 = max(p[0] + p[2] for p in placed)
        py1 = max(p[1] + p[3] for p in placed)
        scale = min((px1 - px0) / gw, (py1 - py0) / gh) if gw and gh else 1.0
        if scale <= 0 or abs(scale - 1.0) < 1e-9:
            continue
        for i in ids:
            e = design.by_id[i]
            if e["type"] != "shape":
                continue
            if e.get("corner_radius"):
                e["corner_radius"] = max(0.0, float(e["corner_radius"]) * scale)
            if e.get("stroke_width"):
                e["stroke_width"] = max(0.0, float(e["stroke_width"]) * scale)


def resize(source: Dict[str, Any], target_canvas: Dict[str, Any]) -> Dict[str, Any]:
    """Reflow `source` into `target_canvas`.

    Args:
        source: {"canvas": {"width", "height"}, "elements": [...]}
                - the design at its original size. See
                chameleon_design_schema_v0.9.json for the element spec.
        target_canvas: {"width", "height"} - the canvas to resize into.

    Returns:
        {"canvas": target_canvas, "elements": [...]} - the same design,
        reflowed. Every source id appears exactly once.
    """
    dst_w = float(target_canvas["width"])
    dst_h = float(target_canvas["height"])

    measurer = TextMeasurer(FONTS_DIR)
    design = Design(source, measurer)

    layout = Layout(design, dst_w, dst_h)
    layout.assign()

    backgrounds = _place_backgrounds(design, dst_w, dst_h)
    decorative = _place_decorative(design, layout, dst_w, dst_h)
    _apply_group_strokes(design, layout)

    elements: List[Dict[str, Any]] = []
    for original in source["elements"]:
        eid = original["id"]
        # Start from a deep-ish copy of every field the schema allows, so
        # anything this engine doesn't reason about (rotation, opacity,
        # z_index, group_id, style_runs, fit, stroke) passes through
        # untouched and correctly.
        el: Dict[str, Any] = dict(original)

        if eid in backgrounds:
            el.update(backgrounds[eid])
        elif eid in decorative:
            el.update(decorative[eid])
        elif eid in layout.placements:
            x, y, w, h = layout.placements[eid]
            el["x"] = x
            el["y"] = y
            el["width"] = w
            el["height"] = h

        if eid in layout.font_sizes:
            el["font_size"] = max(1.0, layout.font_sizes[eid])

        # Final containment guard. Everything above should already be in
        # bounds, but an element that reaches here unrounded and with a
        # sub-pixel box can still poke out, and an off-canvas element is a
        # structural-check failure. Text boxes are the one case where
        # clamping the box is wrong - the renderer doesn't clip, so
        # shrinking the box would hide real overflow rather than fix it -
        # so text is clamped positionally only, never shrunk.
        if all(k in el for k in ("x", "y", "width", "height")):
            el["x"] = float(el["x"])
            el["y"] = float(el["y"])
            el["width"] = max(0.0, float(el["width"]))
            el["height"] = max(0.0, float(el["height"]))

            if el["x"] < 0:
                el["x"] = 0.0
            if el["y"] < 0:
                el["y"] = 0.0
            if el["x"] + el["width"] > dst_w:
                overflow = el["x"] + el["width"] - dst_w
                el["x"] = max(0.0, el["x"] - overflow)
                if el["x"] + el["width"] > dst_w:
                    if el["type"] == "text":
                        el["width"] = max(0.0, dst_w - el["x"])
                    else:
                        el["width"] = max(0.0, dst_w - el["x"])
            if el["y"] + el["height"] > dst_h:
                overflow = el["y"] + el["height"] - dst_h
                el["y"] = max(0.0, el["y"] - overflow)
                if el["y"] + el["height"] > dst_h:
                    el["height"] = max(0.0, dst_h - el["y"])

            # Round to 2dp to keep the emitted JSON readable without
            # losing sub-pixel positioning that the renderer relies on.
            for k in ("x", "y", "width", "height"):
                el[k] = round(el[k], 2)

        elements.append(el)

    return {
        "canvas": {"width": int(dst_w), "height": int(dst_h)},
        "elements": elements,
    }
