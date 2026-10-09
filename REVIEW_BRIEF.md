# CHAMELEON — Context Brief for External Review

## Purpose of this document

We are a two-person undergraduate team entering the Adobe University Hackathon 2026. We have a proposed
approach and want it critically reviewed by someone with no prior involvement.

**What we want from you:** find the flaws. Tell us where our reasoning is weak, where we have
misread the rules, where we are solving the wrong problem, and what a stronger approach would be.
Agreement is less useful to us than disagreement.

This document separates:

- **FACT** — stated in official materials, or measured by us directly. Verifiable.
- **CLAIM** — our inference or opinion. Contestable, and the thing we most want challenged.
- **UNVERIFIED** — something we have assumed but not actually checked.

We have tried to present our proposal without arguing for it. Where we found counter-arguments, we
included them. If the framing still reads as advocacy, discount it accordingly.

---

# 1. The problem

**FACT.** Build an offline engine that takes a source design (as JSON) and a target canvas size, and
returns the same elements with updated positions, dimensions, and optionally text sizes. The goal is
to rearrange the design, not stretch or uniformly shrink it.

**FACT.** The implementation point is a single function:

```python
def resize(source: dict, target_canvas: dict) -> dict
```

in `engine/resize.py`. Input loading, output writing, rendering, and Docker packaging are provided.

**FACT.** Adobe describes this as "a live product challenge Adobe still has not fully solved" and
frames it as "designs that adapt, not distort."

**FACT.** Difficulty ranges from small aspect-ratio changes to extreme transformations with many
elements.

## 1.1 Competition structure

| | |
|---|---|
| Build window | October 5–30, 2026 |
| Submission deadline | October 30, 2026 |
| Round 1 | Fully automated scoring against a written rubric. No human judging. |
| Advancement | Top 3 teams, notified November 2026 |
| Finale | January 11–13, 2027, in person at Adobe HQ. Required attendance to place. |
| Teams | 2–3 undergraduates, same university |

**FACT.** Both artifacts are evaluated: the output JSON is checked structurally, and the rendered PNG
is assessed by an AI evaluator against the layout-quality rubric.

## 1.2 The rubric (round one)

**FACT**, as stated in the official materials:

| Row | Stated as |
|---|---|
| No unintended collisions | Avoid unplanned overlap; text should not sit on visually busy imagery |
| Readability | Text legible, sufficiently large, contrasted, fully on-canvas |
| Brand fidelity | Keep colors, fonts, logo, and image proportions intact |
| Intentional composition | Preserve hierarchy, alignment, even spacing, emphasis on key elements |
| Generalization | Work across hidden designs and formats, not hand-tuned examples |
| Speed and reliability | A preference; no hard execution-time limit. Quality comes first. |

**UNVERIFIED.** We do not have the rubric's weights, its exact wording beyond the above, or how the
AI evaluator is prompted. We have a summary of the main brief, not the brief itself. If any of our
reasoning depends on rubric details we have not actually seen, flag it.

## 1.3 Hard requirements and disqualifiers

**FACT:**

- Preserve the same element IDs and count. Nothing added or removed.
- Keep required fields present; all elements on-canvas.
- Preserve image proportions.
- Runs in Docker with networking disabled. All dependencies and model weights must be present at
  build time.
- **Do not hard-code behavior for specific designs or canvas sizes.**
- **Do not regenerate, redraw, or replace any image content.** Disqualifying, including for
  backgrounds and decorative assets.
- For round one, treat text content and font family as fixed. Repositioning and font-size changes
  are allowed; copy rewriting and font-family changes are not rubric targets.
- Target canvas sizes are drawn from a catalogue of real platform formats (e.g. "Instagram Post"),
  but the specific ones used at scoring time are hidden.

---

# 2. The data format

A design is `{"canvas": {...}, "elements": [...]}`. Each element has `id`, `type`
(`text` | `image` | `shape`), and a box: `x`, `y`, `width`, `height` in pixels from the canvas
top-left.

Optional on all elements: `rotation` (degrees clockwise about center, default 0), `opacity`
(0–1, default 1), `z_index` (higher paints on top), `group_id`.

Type-specific fields:

- **text**: `content`, `font_family`, `font_size`, `color` (required); plus `font_weight`,
  `font_style`, `text_align`, `line_height` (default 1.2), `letter_spacing`, `max_lines`,
  `style_runs` (per-character color/weight/style overrides).
- **image**: `asset_id` (required), `fit` (`contain` | `cover` | `fill`, default `contain`).
- **shape**: `shape_type` (`rectangle` | `ellipse` | `line`) and `fill_color` (required); plus
  `stroke_color`, `stroke_width`, `corner_radius`.

**The central difficulty.** No element carries a semantic label. Nothing identifies which element is
the headline, the logo, or the call-to-action. The schema says so explicitly in its description of
`group_id`:

> "Elements sharing a group_id are a designer-intended unit (e.g. a button's background shape + its
> label). The engine is NOT told what the group means — only that these pieces are meant to travel
> together. This is the semantic-gap constraint made concrete."

`group_id` is the only explicit semantic signal in the entire format.

---

# 3. The provided starter repo

```
engine/resize.py        the function we implement (currently a naive baseline)
engine/main.py          orchestrator: loads input/*.json, calls resize(), self-checks, writes output
renderer/converter/     adapter converting our schema to Crello format for cr-renderer
brand_kit/assets/       3 PNG files referenced by asset_id
brand_kit/fonts/        7 TTF files (renamed Lora and Poppins)
input/                  3 sample tasks, one per difficulty level
chameleon_design_schema_v0.9.json
Dockerfile              python:3.11-slim, installs cr-renderer from GitHub
```

## 3.1 Renderer behavior we have verified by reading the source

**FACT.** `renderer/converter/wrap.py` wraps text greedily at spaces only, using real glyph advance
widths from the brand fonts via skia. It converts spaces to newlines in place, preserving character
indices so `style_runs` offsets stay valid. **It wraps to the element's width and never consults its
height.** Vertical overflow is not detected or prevented by the renderer.

**FACT.** A single word wider than the box is left to overflow rather than hard-broken.

**FACT.** `renderer/converter/assets.py` implements `contain` / `cover` / `fill` itself, because
cr-renderer always stretches. `contain` letterboxes the image inside the box with transparent
padding. Missing assets render as a labeled gray placeholder rather than crashing.

**FACT.** `renderer/converter/shapes.py` clamps `corner_radius` to half the box's shorter side.
Stroke properties are ignored for rectangles and ellipses; only `line` uses them.

**FACT.** `engine/main.py`'s `self_check` compares element IDs as **sets**, so duplicate IDs would
pass locally while failing the official count check. It also checks bounds using the unrotated box,
so a rotated element can visually exceed the canvas while passing. It discards the renderer's warning
list and always exits 0.

## 3.2 The three sample tasks

| File | Elements | Resize | Notable features |
|---|---|---|---|
| level1_warmup | 4 | 1080×1080 → 1080×1350 | Background, logo, headline, CTA |
| level2_core | 11 | 1080×1080 → 1600×400 | Groups, semi-transparent decorative circles (one intentionally bleeding off-canvas), two-color headline via `style_runs` |
| level3_advanced | 17 | 1080×1350 → 728×90 | Run-on headline, long paragraph, 3 badges, a −12° rotated group, an intentionally missing asset, tiny legal text |

The sample files describe level 2 as "the main discriminator" and level 3 as what "separates the
elite."

---

# 4. Measured baseline behavior

We set up the environment (Python 3.11, cr-renderer, skia) and ran the provided baseline, which
multiplies all horizontal values by `target_w / source_w` and all vertical values by
`target_h / source_h`.

**FACT**, measured from the actual output JSON:

| | Level 2 (1600×400) | Level 3 (728×90) |
|---|---|---|
| Aspect-ratio drift, non-text elements | 300% on all | 911% on all |
| Text elements below 10px | 3 of 4 (min 6.7px) | 7 of 7 (min 1.0px) |
| Visible collisions | subhead over CTA button | pervasive |

The 5% proportion limit is exceeded by roughly 60× and 180× respectively. The baseline is valid JSON
that would score near zero on quality.

**FACT, and we think it matters.** In the level 2 render, the product image *looks* correctly
proportioned despite 300% box drift, because `fit: contain` letterboxes it. The scorer checks the
box, not the rendered pixels. Visual inspection alone will not catch proportion violations.

---

# 5. Our proposed approach

Everything in this section is **CLAIM**. It is a proposal, not a decision.

## 5.1 Framing

We concluded that this is a re-layout problem, not a scaling problem. The source JSON encodes a
designer's decisions — focal point, reading order, what must be legible, what belongs together —
none of which are written down. Scaling preserves coordinates and discards intent.

We also concluded that sizing and arrangement are mutually dependent: available space depends on the
arrangement, but an arrangement can become untenable once you see what sizes it forces. So any
pipeline needs to be able to revisit the arrangement rather than only patch the output.

## 5.2 Proposed pipeline

```
1. ANALYZE    infer a role for each element          (deterministic rules)
2. GENERATE   produce K candidate layouts            (strategies × parameters)
3. SCORE      rank candidates against a rubric proxy
4. REPAIR     enforce hard constraints on the winner
```

Stage 3 is intended to serve double duty: candidate selection at run time, and progress measurement
during development.

Stage 4 is intended as a guarantee: whatever stages 2 and 3 do, the output is structurally valid.

## 5.3 Stage 1 — role inference

Rules over element properties, not a learned classifier. Proposed signals:

| Signal | Inferred role |
|---|---|
| Image, `fit: cover`, area ≈ canvas area, lowest z-index | Background |
| Text with the largest `font_size` | Headline |
| Shape + text sharing a `group_id`, text inside the shape | Button or badge |
| Shape with `opacity` < 1, often crossing the canvas edge | Decoration |
| Smallest font, near the bottom | Fine print |
| Image, `fit: contain`, wide aspect, near the top | Logo |

Anything unclassified falls back to "generic content" and is scaled proportionally and kept
on-canvas.

We consider branching on `id` to be off-limits, since the hard requirements forbid hard-coding for
specific designs.

## 5.4 Stage 2 — candidate generation

Proposed: 4 layout strategies, each parameterized, enumerated into roughly 120 candidates.

| Strategy | Selected when | Behavior |
|---|---|---|
| `preserve` | Small ratio change | Keep arrangement, rescale, redistribute slack |
| `column` | Tall target | Single centered column, ordered by source y |
| `split` | Wide target | Visual anchor one side, text column the other |
| `strip` | Extreme ratio | Horizontal band, essentials inline, remainder tucked |

Parameters: anchor side, margin fraction, alignment, text column width, whether secondary text gets
its own line.

Strategy selection uses continuous properties (`ratio = width / height`, and the ratio change between
source and target), never a lookup table of known formats, which we read as hard-coding.

## 5.5 Stage 3 — the scorer

A weighted sum over measurable rubric proxies:

- Collisions: pairwise box overlap between content elements, decorations exempt
- Readability: minimum font size; measured text height vs box height; contrast behind text
- Brand fidelity: aspect drift vs source box, hard fail above 5%
- Composition: count of distinct alignment guides, gap variance, margin consistency, size hierarchy
- Canvas use: penalties for large empty regions and crowding

Text measurement reuses the renderer's own `char_widths` and `wrap_text`, so our notion of "fits"
matches what will be drawn.

## 5.6 Stage 4 — repair

Clamp into canvas, restore source aspect ratios on non-`cover` images, scale `corner_radius` with
box height, strip non-schema fields, assert ID set equality and count.

## 5.7 Two specific observations we are acting on

**CLAIM (A): the absence of a time limit makes search viable.** Since latency is explicitly not a
gating constraint, generating and scoring many candidates costs us nothing we care about. This is
the main reason we favor generation-and-selection over a single algorithm.

**CLAIM (B): reading image pixels is permitted and useful.** The rubric says text should not sit on
visually busy imagery, and also requires contrast. We cannot change text color without breaking brand
fidelity, so the only lever for contrast is placement. The ban is on regenerating or replacing image
content; reading pixels to compute local variance and prefer calm regions appears to be allowed.

We would like both of these challenged, particularly (B) — we are confident in the reading but it is
an interpretation of a disqualifying rule, and the cost of being wrong is elimination.

---

# 6. Alternatives we considered

Presented with the arguments on both sides, because we may have dismissed these too quickly.

## 6.1 One algorithm instead of search

**For:** Roughly a third of the code. Easier to reason about. Fewer places for bugs. A well-designed
single algorithm may beat a poorly-scored search.

**Against:** Every improvement means editing one function that must be right for every input. No
fallback when it guesses wrong on an unseen case.

**Status:** Still open. We have deliberately not decided, on the grounds that both routes need the
scorer, so the first week's work is identical. A reviewer may think this is indecision rather than
sequencing.

## 6.2 A learned layout model

**For:** Could generalize to designs our strategies do not anticipate. The Crello dataset
(which cr-renderer targets) contains tens of thousands of real designs as structured element lists
with positions and types — plausible training data.

**Against:** No network at scoring, so weights must be baked in and run on the reference hardware.
Can emit invalid geometry, requiring a fallback anyway. Harder to debug. Fine-tuned weights must be
publicly hosted.

**Status:** We lean toward deferring this to the finale. We are not confident this is right. If the
automated evaluator rewards layout quality more than we expect, a model trained on real designs might
outperform handwritten strategies substantially.

## 6.3 An AI model as the scorer rather than the generator

A small vision model ranking candidates would be closer to what the actual judge does than a
handwritten weighted sum.

**Status:** Considered briefly, not explored. Possibly underrated. We would welcome a view on this.

---

# 7. The hardest unsolved piece

Level 3 requires fitting 17 elements into 728×90, and **no element may be dropped**, since every ID
must be present in the output.

Three badges, a rotated ribbon, legal text, and a 150-character headline must all be placed in a band
90 pixels tall. Readability and completeness are in direct conflict, and the rubric scores both.

We do not have a good answer. Our current thinking is a deliberate "degradation policy": some
elements get real estate, others are reduced to minimum legible size and tucked at an edge.

We have ruled out setting `opacity: 0` on elements we cannot place, on the grounds that it hides an
element while claiming to preserve it. We are not certain this is the right call — it may be a
legitimate design decision rather than evasion. A view on where that line sits would be useful.

---

# 8. Assumptions we have not verified

1. **The rubric's weights.** We assume the six rows are roughly comparable in weight. If, for example,
   collisions dominate, our priorities are wrong.
2. **How the AI evaluator sees the PNG.** We assume it rewards things a human designer would. We have
   not tested what a vision model actually penalizes.
3. **Vertical text placement in cr-renderer.** We know wrapping ignores box height. We have not
   confirmed whether text is top-aligned, centered, or overflows downward within its box. Our
   proposed font-size search depends on this.
4. **Line-height arithmetic.** We assume rendered line height is `font_size × line_height`. Not
   confirmed against a real render.
5. **What "hard-coding for specific canvas sizes" covers.** We interpret aspect-ratio bands as
   acceptable and format lookup tables as not. This is our reading, not a stated rule.
6. **Whether 120 candidates is the right order of magnitude.** Chosen as a plausible number, not
   derived.
7. **That role inference will work on hidden designs.** Our signals are drawn from three samples. We
   have no evidence they generalize.

---

# 9. Known constraints on us

- 22 days remaining at time of writing (October 8, 2026).
- Two people, both undergraduates, in parallel with coursework.
- A viewer add-on (Adobe Express) is also required but is not scored.
- Installing the provided renderer pulls in `datasets`, `pandas`, `pyarrow`, and `huggingface-hub` as
  transitive dependencies, making the Docker image heavy for what is actually used.

---

# 10. What we are asking you

Please be direct. Specifically:

1. **Is the four-stage pipeline the right decomposition?** If not, what is?
2. **Search versus one algorithm** — which would you choose, and on what grounds?
3. **Is our reading of the "no image regeneration" rule correct** when it comes to reading pixels for
   placement decisions? How much risk is there?
4. **Is role inference from geometry viable**, or is it fragile in a way we are not seeing? What
   breaks it?
5. **What would you do about Level 3?**
6. **Are we wrong to defer machine learning to the finale?**
7. **What are we not thinking about at all?** This is the question we most want answered. We have
   been inside this problem for a day and have likely developed blind spots.

If you think our whole framing is wrong, say so plainly. We would rather hear it now than on
October 30.
