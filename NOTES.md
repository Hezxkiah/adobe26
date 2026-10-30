# CHAMELEON resize engine — implementation notes

Working notes for the `engine/` layout reflow: what was built, why it's
structured this way, what was verified, and which parts are known to be
weak. Written for whoever picks this up next.

---

## 1. The task

`engine/resize.py` is the only function the engine must provide. It takes
a design at one canvas size and returns the same design reflowed to fit a
different canvas.

```
input:  a task JSON (source design + target canvas)
              |
              v
        engine/resize.py   <-- the deliverable
              |
              v
output: <task>_output.json  (schema-valid resized design)
        <task>_output.png   (rendered preview)
```

Both artifacts are graded (`RULES.md:15-20`): the JSON is checked
structurally, and the rendered PNG is judged by an AI evaluator. The PNG
is a pure function of the JSON, so the JSON is the only lever.

The brief's framing: *"a naive program stretches the picture; a good one
moves the furniture."* The starter shipped a uniform stretch, which keeps
every coordinate valid and every hard rule satisfied while producing
squashed, unreadable output.

## 2. Original plan

Agreed during the initial design conversation, in this order:

1. **Itemize** the design into independently addressable objects. *(Turned
   out to be unnecessary — the schema already delivers elements as a flat
   list of dicts with stable ids. The real gap was the decision procedure,
   not the data model.)*
2. **Classify** a role for every element from geometry alone. The schema
   has no `role` field and `FAQ.md:13` says so explicitly, but the layout
   needs different decisions per element.
3. **Decompose into groups + free elements.** `group_id` means "these
   travel together," so a badge's pill and label are one unit.
4. **Pick a layout archetype** from the aspect ratio delta, rather than
   applying one transform everywhere.
5. **Solve font sizes** against real glyph metrics, iterating because text
   fit depends on the width you're deciding.
6. **Validate** the hard constraints before emitting.

Two structural corrections to that plan, both made during implementation:

- **Per-element dynamic assignment doesn't work.** Letting each element
  pick any free slot passes every hard rule and produces garbage. The
  invariant that matters is *relative structure* — a CTA drifting above
  the headline reads as a different design. Order is encoded explicitly in
  `_ordered_flow`.
- **Group units must be the unit of assignment.** Individually placing a
  badge's background and its label splits them apart.

## 3. Architecture

Four modules. The split was chosen so the archetype logic can be iterated
without touching the font solver, and so each piece can be tested alone.

| File | Lines | Responsibility |
|---|---|---|
| `resize.py` | 257 | Orchestrator. Backgrounds, decoration, final containment, JSON emission. |
| `classify.py` | 300 | Infers roles from geometry; computes reading order and design margin. |
| `layout.py` | 970 | Archetype selection and slot assignment. |
| `metrics.py` | 359 | Text measurement and font-size solving. |

```
Design(source, measurer)     -> roles, groups, reading order, margin
  Layout(design, w, h)       -> archetype + placements + font sizes
    resize()                 -> backgrounds + decoration + emit
      TextMeasurer           -> shared by every stage above
```

Nothing here touches `main.py`, `requirements.txt`, or the renderer. The
Dockerfile copies `engine/` wholesale, so new modules ship without
changes.

---

## 4. `metrics.py` — text measurement

### Why it exists

The vendored renderer wraps text at real skia glyph advance widths
(`renderer/converter/wrap.py`). If our solver and the renderer disagree
about where a line breaks, every font size we pick is wrong and the PNG
shows clipped or colliding text. So we import the renderer's own
measurement path and measure the same way it draws.

This is safe to do: `layout.py` already imports from the vendored
renderer for fonts (`brand_fonts.load_brand_font_manager`), and
`main.py` renders with it, so the dependency exists regardless. The
Docker build installs `cr-renderer`, so skia is present at scoring time.

### The API

- `measure(...)` — wrap at a given size/width, return `n_lines`,
  `max_line_width`, `longest_word_width`, `text_height`.
- `fits(...)` — does it fit the box? Both dimensions are hard.
- `fit_size(...)` — bisect for the largest size in `[lower, upper]` that
  fits. `upper` is the designer's intent (proportionally scaled source
  size), so text never grows beyond what the original implied.

### Two details that mattered

**`longest_word_width` is why this isn't a formula.** Greedy wrap has no
fallback for a word wider than the box — it overflows at *every* font
size. That's the constraint that binds first in narrow targets.

**`_WRAP_SAFETY = 0.97`.** Greedy wrapping is a step function: a hair more
width can collapse four lines into three. A solver that lands exactly on
that boundary is technically correct and practically a coin flip. On the
728×90 case, a **0.02px** width difference flipped the headline between 3
and 4 lines, so the emitted box claimed a height the renderer didn't honour
and text overlapped the subhead. Solving against a 3%-narrowed width keeps
every choice clear of the cliff.

### Fallback

If skia/`cr_renderer` is unimportable, `_approx_advance` estimates
per-character advances by glyph class. Verified within ~9% of skia. The
import is deliberately optional so a missing renderer degrades quality
rather than aborting the run — `main.py` already tolerates that case.

---

## 5. `classify.py` — role inference

### Why

`FAQ.md:13`: *"There are no semantic labels telling you which element is
'the headline' or 'the logo' — you get `id`, `type`, and geometry."*
Meanwhile the layout needs to treat a logo (fixed readable size), a
background (stretched to fill), and a badge (moves with its label)
differently. Without roles there's no way to tell them apart.

### Roles and the signal for each

| Role | Derived from |
|---|---|
| `FULL_BLEED` | covers ≥92% of canvas area |
| `DECORATIVE` | shape with opacity ≤0.35, or hanging off a canvas edge |
| `LOGO` | wide (≥1.9 aspect), small (≤12% coverage), top-hugging |
| `WATERMARK` | same geometry but bottom-hugging |
| `FOCAL` | largest non-bleed, non-logo image; ties break toward square |
| `HEADLINE` | largest `font_size` |
| `SUBHEAD` | next-largest text |
| `FINEPRINT` | both notably smaller than headline *and* tiny vs canvas |
| `GROUP` | any element sharing a `group_id` |

Plus three whole-design outputs: **groups** (id → members), **reading
order** (`_ordered_flow`), and **implied margin** (`_implied_margin`,
recovered from the gap between content edges and canvas edge, clamped to
2–9%).

### What is assumption, and what isn't

This is the weakest part of the implementation and should be read as such.

**The role *names* are semantic; the role *behaviour* is geometric.**
Nothing in the schema distinguishes "logo" from "footer badge." The
distinction that actually mattered was directional — two edge marks on
opposite edges need opposite anchors, or they collide. Renaming these to
`EDGE_MARK_TOP` / `EDGE_MARK_BOTTOM` would be more honest and behaviourally
identical. Worth considering; see §9.

**Constants were fitted to three samples, not derived.** A sensitivity
sweep (halve/double each, count role changes) found:

| Constant | Fragility |
|---|---|
| `_BLEED_COVERAGE` 0.92 | **fragile** — at 1.84, `bg` fails the test and gets demoted to `FOCAL` |
| `_LOGO_ASPECT` 1.9 | **fragile** — at 3.8, `product` (square) classifies as `LOGO` |
| `_FINEPRINT_RATIO` 0.45 | loads 1 element, stable |
| `_FINEPRINT_CANVAS_RATIO` 0.022 | loads 1 element, stable |
| `_DECORATIVE_OPACITY` 0.35 | inert at ±2× — **false confidence** |
| `_LOGO_MAX_COVERAGE` 0.12 | inert at ±2× — **false confidence** |
| `_WATERMARK_EDGE` 0.06 | inert at ±2× — **false confidence** |

The last three look safe *only* because no sample has an opacity near 0.35
(observed: 0.15, 0.12) or a watermark gap near 6% (observed: 1.5%). A
constant that changes nothing across three samples tells you nothing about
hidden designs.

The two fineprint cuts miss each other by **1.2px** on the Level 2
subhead — not a derived threshold, one that happens to separate two
samples. A hidden design with a 20px legal line under a 40px headline would
classify as `SUBHEAD`.

**`FINEPRINT`'s two-signal rule is load-bearing.** Ratio alone can't
separate a 24px subhead under a 56px headline from a 12px legal line under
a 40px headline. The first attempt used ratio alone and mislabelled both
the Level 1 `cta` and the Level 2 `subhead` as fineprint.

---

## 6. `layout.py` — archetypes

### Why archetypes

One transform can't be right for every target. The aspect ratio delta
picks a strategy:

| Archetype | Condition | Composition |
|---|---|---|
| `NEAR` | \|Δ\| ≤ 0.12 | proportional scale — stretching is genuinely correct here |
| `STACKED` | target taller | column preserved, spread vertically |
| `BANDED` | target wider | columns: image / copy / calls to action |
| `STRIP` | \|Δ\| ≥ 2.2 | one dominant axis |

### Placement rules

**Groups are rigid.** A group's bbox is scaled as one unit, then each
member is mapped back through the same transform using its own source
coordinates. The bbox supplies scale and position; internal geometry is
preserved exactly. First attempt placed group members *and* their labels
separately, so pills rendered stranded with text floating away from them.

**Font-size floor for group labels.** Rigid scaling can push an 18px badge
label below readability on a short canvas, so the label sets a floor
(`_GROUP_TEXT_MIN_PX = 7.0`, capped by `_GROUP_TEXT_MAX_GROW = 2.2`). The
floor is capped by the slot: a pill that outgrows its column looks like a
collision, and overlap is the worse failure.

**Edge marks keep a fixed readable size**, sized off the *source* short
side (max 22%), capped by the *target* short side (max 40%). Without the
target cap a logo lands 237px tall on a 90px banner and swallows it.

**Slot heights are budgets, and must be scaled when over budget.** Text
slots larger than the band hand the solver permission to pick a size that
spills off-canvas.

### Layout constants

Same caveat as `classify.py`: mostly plausible values checked against the
samples, not derived. The background ratio clamp at `[2%, 9%]` came from
measuring (the quartile rule produced 13%, 8.3%, 4.4% — Level 1 hit the
ceiling, so the clamp does real work).

---

## 7. `resize.py` — orchestration

Handles the two roles whose rules are archetype-independent:

- **Backgrounds** fill the target canvas exactly. No position to
  negotiate; getting this wrong is catastrophic (letterboxing).
- **Decoration** is repositioned without dominating: scaled by the *shorter*
  axis so it can't inflate on a wide target, capped at 34% of the short
  side, with the source's **bleed direction preserved**. Snapping
  everything inside would turn intentional edge decoration into a floating
  blob — the Level 2 sample documents exactly this on `deco_circle_1`.

Then final containment. Every element is copied field-by-field, so
anything the engine doesn't reason about (`rotation`, `opacity`,
`z_index`, `group_id`, `style_runs`, `fit`, `stroke_color`) passes through
untouched.

---

## 8. Verification performed

### Structural (gates — these are known, not guessed)

- **78 source × target combinations** across 26 real platform formats
  (Instagram Post/Story, YouTube Thumbnail/Banner, LinkedIn Banner,
  billboards, 728×90, 320×50, print A4, 5000×400 panoramic, …), asserting:
  - id set identical, no duplicates, same order
  - required fields present per element type
  - nothing off-canvas (0.5px tolerance)
  - non-bleed images keep source aspect within 5%
  - full-bleed images cover the target exactly
  - `font_size >= 1`
  - `content`, `font_family`, `asset_id`, `type` never change

  **All hold.** The baseline stretch failed on-canvas for Level 2.

- All three samples pass `main.py`'s self-check with zero warnings.

### Renderer agreement

- **Zero wrap mismatches** across all three outputs: our predicted line
  count equals what the renderer actually draws, and emitted box heights
  equal rendered text height. This caught the wrap-cliff bug in §4.

### Visual

All three samples inspected as rendered PNGs at every iteration. Baseline
comparison:

- **Level 1** (1080×1080 → 1080×1350): passable, no collisions.
- **Level 2** (1080×1080 → 1600×400): baseline squashed text to ~13px and
  badges to unreadable 330×21 pills. Now a clean banner — logo top-left,
  product left, headline/subhead centered, badge + CTA right.
- **Level 3** (1080×1350 → 728×90): baseline illegible smears with the
  ribbon overflowing the corner. Now legible headline in 3 lines with five
  readable badges.

### Robustness

- **Fallback path**: with the renderer import blocked, output remains valid
  (17/17 elements, ids intact, sane font sizes).
- **Collision sweep**: 18 combinations checked for overlapping non-
  decorative pairs. One remaining "overlap" (`holiday_watermark` /
  `legal_text` at 1080×1350) **exists in the source design itself** — the
  `NEAR` identity case is faithfully preserving an input overlap.

### Not verified

**Docker build.** Docker isn't installed in this environment, so the
containerized run is untested. The Dockerfile copies `engine/` wholesale and
`requirements.txt` is unchanged, so the new modules should ship — but that
is inference, not a confirmed build. **Run this before relying on it.**

---

## 9. Known weaknesses

Ordered by how much they'd cost on hidden designs.

1. **Constants are fitted to three samples.** §5. Two are knife edges
   (`_BLEED_COVERAGE`, `_LOGO_ASPECT`). This is the highest-value thing to
   fix, and it needs a generated test set with known roles, not more
   eyeballing.

2. **`FULL_BLEED` assumes any full-canvas element can absorb a crop.**
   Verified safe for the sample background (smooth gradient, 336 colors
   across 1.8M px, so center-cropping to 400px tall loses nothing). But
   detection is by *size alone* — a hero photo occupying 95% of the canvas
   would be center-cropped and lose real content. A low-detail check on the
   asset before treating it as freely croppable would close this.

3. **Layout quality is geometrically inferred, not measured.** Intent is
   preserved structurally — grouping, order, hierarchy, relative scale —
   but the source's actual whitespace and inked text extents are not read.
   A text box in the JSON is a *container*, not a measurement of its glyphs
   (the Level 3 headline occupies 900×220 but inks far less). Rendering the
   source and extracting per-element inked bounds would turn several
   inferences into measurements, at ~0.4s per design. Latency is not
   scored (`RULES.md:11`).

4. **Semantic role names overstate what's computed.** Renaming to
   geometric descriptions (`EDGE_MARK_TOP`, `INCIDENTAL_TEXT`) would be
   behaviourally identical and more honest. Also `classify.py:178` computes
   an unused `short` variable, and `_center` at line 85 is dead code.

5. **Text overlap isn't resolved as a final pass.** Slot budgeting prevents
   it by construction, but there's no last-mile collision resolver if an
   archetype under-allocates on an unusual design.

6. **Contrast isn't checked on output.** Text color is in the schema and
   the background is knowable, so legibility relationships are expressible
   and currently unenforced. A headline landing on a dark patch of a
   stretched gradient would pass every gate and read poorly.

7. **No multi-brand-set handling.** `brand_fit/fonts` is hardcoded to
   `Solstice`. Fine for this challenge; would need parameterising for a
   different brand kit.

8. **`holiday_watermark`'s asset is intentionally missing** (`FAQ.md:4`
   says don't add it), so it renders as a grey placeholder box. Classifying
   and pinning it bottom-right is correct, but that placeholder competes
   with real content during AI evaluation.

---

## 10. If picking this up next

**Highest value:** generate a test set with known roles and targets, then
sweep the `classify.py` constants. Turns five fitted guesses into measured
curves and tells you which margins survive.

**Next:** render the source, extract inked text bounds, replace the
container-based margin inference with measured whitespace.

**Then:** add the visual-quality tier (legibility, contrast, collision,
overflow) as a *separate* harness from `main.py`'s structural gates. Keep
them distinct — the gates are known, the visual tier is guesswork, and
optimising against our own rubric is how you ship something that scores
well on our assumptions and poorly on the real one.