# CHAMELEON — Build Plan

*Last updated: 2026-10-08. Mirrors the shared doc at
https://claude.ai/code/artifact/2404093c-1bbd-4b9d-923d-3cee770ff218*

---

## What we're building

An offline engine that takes a design and a new canvas size and rearranges the elements to fit,
rather than stretching them. One function: `resize(source, target_canvas) -> dict` in
`engine/resize.py`. Everything else in the repo is plumbing.

The design arrives as JSON: a canvas plus a list of text, image and shape elements, each with a box.
Nothing is labelled — no element says it is the headline or the logo. We work that out ourselves.

| | |
|---|---|
| Submission deadline | October 30, 2026 |
| Round 1 | Fully automated against a written rubric. No human judging. |
| Outcome | Top 3 advance, notified November 2026 |
| Finale | January 11–13, 2027, San Jose. In person, required to place. |

Round 1 has no human in the loop. Robustness beats flair — there is no judge to be impressed by an
ambitious approach that half-works.

---

## The insight worth building around

There is no execution-time limit in round one. The rules call performance "a preference" and say
quality comes first. So we can generate many candidate layouts, score them all, and return the best,
and still finish in well under a second.

Second half: four of the six rubric rows are things we can measure ourselves.

| Rubric row | Can we compute it? |
|---|---|
| No unintended collisions | Yes — box overlap between content elements |
| Readability | Yes — min font size, text fitting its box, contrast behind text |
| Brand fidelity | Yes — aspect drift against the source box |
| Intentional composition | Mostly — alignment, gap variance, size hierarchy, reading order |
| Generalization | No — but we can test on canvases we invent |
| Speed and reliability | Not a real constraint |

If we can score ourselves, we can optimise against the score instead of guessing.

**Caveat:** more candidates is not free improvement. 10,000 bad candidates lose to 100 good ones.
Optimise candidate quality and diversity, not raw count.

### Rubric weights (confirmed by Adobe, Oct 9)

Asked directly. The answer:

| Weighted **most heavily** | Weighted **lower** |
|---|---|
| Genuine rearrangement to fit the new shape — *not just scaling or stretching* | Faithfulness to the original design |
| Clean layout | Consistency across cases |
| Readability | |

**This changes our priorities.** We had treated rearranging as the *method* for getting a good
layout. Adobe is scoring it as an *outcome*, and weighting it top. A layout can be clean, readable
and perfectly proportioned and still score badly because it reads as a careful scale rather than a
redesign.

Two direct consequences, applied below:

1. `preserve` is only safe for small shape changes — see Decision 2.
2. Add a scorer term measuring **distance from the naive stretch** — see Decision 3.

And one that validates an earlier call: faithfulness sits in the *lower* band, so the reading-order
term stays light. Preserving the source must never fight against rearranging, because rearranging
wins.

Adobe also pointed at **Section 2.5 of the challenge document** as "the most concrete guidance
available on what good actually looks like." We do not have that document. Getting it is the highest
-value outstanding item.

---

## Architecture

```
SOURCE JSON
    │
    ▼
┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────┐
│ 1. ANALYZE  │──▶│ 2. GENERATE │──▶│  3. FILTER  │──▶│  4. SCORE   │──▶│ 5. VALIDATE │
│             │   │             │   │             │   │             │   │             │
│ relations,  │   │ strategies  │   │ drop        │   │ rank, then  │   │ schema,ids, │
│ soft roles  │   │ × knobs     │   │ anything    │   │ render      │   │ no geometry │
│             │   │             │   │ invalid     │   │ top N       │   │             │
└─────────────┘   └──────▲──────┘   └──────┬──────┘   └─────────────┘   └─────────────┘
                         │                 │
                         └─────────────────┘
                     nothing survived — widen the search

     Person A ──────────────────┤├────────────────────── Person B
```

**Stage 4 does double duty:** it picks the winner at run time, and it is how we measure whether a
change improved anything during development.

**The filter sits before the scorer deliberately.** An earlier version of this plan scored
candidates and then repaired the winner by clamping boxes and restoring aspect ratios. That
invalidates the score — what we hand over is no longer the layout that won. Invalid candidates now
get dropped before scoring. If a geometric fix ever proves unavoidable, we re-score afterwards
rather than quietly editing the winner.

**Stage 4 also renders the top handful** and measures the actual image, not just geometry. We have
the renderer locally and no time limit. See the `fit: contain` trap under Measured Baseline.

**Stage 5 only checks schema, ids and fields.** It never moves geometry.

---

## Decision 1 — Analyze: relationships and soft roles

Carry two things side by side.

### Relationships (drive placement)

Roles are a lossy compression and the compression can be wrong. So also extract what each element is
*doing*:

- visual prominence (relative font size, relative area)
- group membership (`group_id`)
- alignment with neighbours (shared left edge, shared centreline, shared baseline)
- proximity and clustering
- reading-order position
- overlap relationships
- z-order

These survive on a hidden design even when the role is ambiguous.

### Soft roles (drive treatment)

Roles still earn their place, because the handling rules depend on them. "Keep this in proportion",
"this may crop", "this may shrink" are facts about backgrounds, products and decorations — no
relationship encodes them.

| Signal | Suggests |
|---|---|
| Image, `fit: cover`, area ≈ canvas, lowest z-index, touches all four edges | Background |
| Largest `font_size` | Headline |
| Shape + text sharing a `group_id`, text inside the shape | Button or badge |
| Shape with `opacity` < 1, often crossing the canvas edge | Decoration |
| Smallest font, near the bottom | Fine print |
| Image, `fit: contain`, wide aspect, near the top | Logo |

Score these rather than hard-classifying. **Where a role is genuinely ambiguous, emit candidates
under both readings and let the scorer settle it** rather than committing to a guess.

### Two rules we must not break

1. **Never branch on `id`.** The samples use friendly names like `headline`; hidden designs will
   not. Hard-coding for specific designs is a stated disqualifier.
2. **Everything unclassified falls back to generic content**: scaled proportionally, kept on canvas,
   kept in the layout. Never clever, never broken.

---

## Decision 2 — Generate: two routes, both open

**Option A — search.** 4 layout strategies, each with knobs, enumerated into candidates and scored.
More moving parts, but each strategy can be crude because the scorer sorts out which one worked.

**Option B — one algorithm.** Compute the layout directly. Roughly a third of the code, but every
improvement means editing the single function that has to be right for every input, with no safety
net when it guesses wrong.

**Both need the scorer, so the first week's work is identical. Decide once the scorer runs and we
can actually compare them.**

### The strategies

| Strategy | When | What it does |
|---|---|---|
| `preserve` | **Small ratio change only** | Keep arrangement, rescale, redistribute slack |
| `column` | Tall target | Single centred column, ordered by source y |
| `split` | Wide target | Visual anchor one side, text column the other |
| `strip` | Extreme ratio | Horizontal band, essentials inline, rest tucked |

**`preserve` is the dangerous one.** "Keep the arrangement and rescale" is close to the exact thing
the heaviest rubric row contrasts against. It is fine when the canvas barely changes shape. Once the
ratio change is significant it should stop being offered as a candidate at all — otherwise we risk
shipping a tidy, well-scored stretch that scores zero on the row that matters most.

**Knobs:** anchor side, margin fraction, alignment, text column width, whether secondary text gets
its own line.

**Selection must use continuous properties** — `ratio = width / height`, and the ratio change
between source and target. A lookup of known formats ("Instagram Post") is hard-coding for specific
canvas sizes, which disqualifies.

Use ratio to decide **how much freedom the layout gets**, not which template is mandatory:

```
small ratio change     → preserve source relationships strongly
moderate               → permit local rearrangement
large                  → permit stacking and stronger movement
extreme                → solve it as constrained packing
```

### Deferred (not now)

Beam search and composable transformations were proposed in external review. Both are solutions to a
problem we do not have yet — 120 candidates can simply be enumerated. Revisit only if the simple
version saturates.

---

## Decision 3 — Score

A weighted sum over measurable rubric proxies.

| Term | How we compute it |
|---|---|
| Collisions | Pairwise overlap between content elements; decorations exempt |
| Readability | Minimum font size; measured text height vs box height; contrast behind text |
| Brand fidelity | Aspect drift vs source box, per element; hard fail above 5% |
| Composition | Distinct alignment guides, gap variance, margin consistency, size hierarchy |
| **Rearrangement** | **Distance from the naive stretch — see below. Heaviest weight.** |
| Reading order | See below. Light weight. |
| Canvas use | Penalty for large empty regions and for crowding |

### Rearrangement — the heaviest term

Adobe weights "genuine rearrangement, not just scaling or stretching" most heavily. So measure it
directly: compute what the naive stretch would have produced, and compare our candidate to it.

```python
stretched = naive_scale(source, target)        # the baseline we are beating
distance  = mean_normalised_box_distance(candidate, stretched)
```

Scale the reward by how much the canvas shape actually changed:

- **Small shape change** → looking like the stretch is fine and expected. No penalty.
- **Large shape change** → similarity to the stretch is evidence we did not do the job. Penalise.

This is the one term where *difference* is rewarded. Keep it tied to ratio change so it never
pushes us to scramble a layout that did not need scrambling.

### Reading order — the one that needs care

Naive version ("headline must stay above the button") is **wrong**, because a different canvas forces
things to move. Three things make it work:

1. **It is a comparison between candidates, not against a fixed ideal.** If stacking is impossible
   at 728×90, every candidate breaks it equally and the term cancels out. It only separates
   candidates where one kept the order and another scrambled it for no gain.
2. **Measure reading order, not "above".** Flatten the layout into the sequence an eye travels —
   top to bottom, left to right — and compare. "Product above headline" becoming "product left of
   headline" is the *same sequence*, so no penalty. Only actual swaps count.
3. **Small weight.** A backwards layout is still a layout; 2px text is useless. Collisions and
   readability should cost far more. Adobe confirmed faithfulness sits in the lower-weighted band,
   so this stays light by design — it must never pull against the rearrangement term.

**Exception: group integrity is near-absolute.** A button's label must stay on its button at any
canvas size. Breaking a `group_id` should cost a lot.

### Oriented boxes, not axis-aligned

Level 3 contains a group rotated −12°, and the starter's own `self_check` tests bounds against the
**unrotated** box. An element can pass that check and still visibly cross the canvas edge or cut
through a label. Compute the four transformed corners. Both collision and bounds depend on it.

### Measuring text

Reuse the renderer's own machinery so "does it fit" matches what gets drawn:

```python
from renderer.converter.wrap import char_widths, wrap_text
from renderer.converter.brand_fonts import load_brand_font_manager

FM = load_brand_font_manager("brand_kit/fonts")   # module level — loads once

widths = char_widths(FM, el["font_family"], weight, style, size, el["content"])
lines  = wrap_text(el["content"], widths, box_w).count("\n") + 1
height = lines * size * el.get("line_height", 1.2)
overflow = max(0, height - box_h)
```

`wrap.py` wraps to **width only** — it never checks height. Vertical overflow is invisible to the
renderer and entirely ours to catch.

### Weights

Start hand-set. Tune by looking at renders — but **on principle, not against the three samples**.
Tuning until the samples look right is overfitting, and generalization is its own scored row.

---

## Decision 4 — Level 3 is a different class of problem

17 elements into 728×90, and **no element may be dropped** — every id must be present.

Three badges, a rotated ribbon, legal text, and a 150-character headline into a band 90px tall.
Readability and completeness are in direct conflict and the rubric scores both. This is closer to
**constrained packing with priorities** than to picking a template.

Rough shape of an approach:

1. Cluster elements (headline cluster, hero, CTA, badges, legal, decoration)
2. Assign continuous importance from prominence, area, centrality, group membership
3. Compute minimum viable sizes — min font, min width, min useful image dimensions
4. Generate packing structures as *structural candidates*, not hard-coded layouts
5. Score the result for overall quality, not just "did the headline survive"

**`opacity: 0` is off the table.** It hides an element while claiming to preserve it. But also do not
shrink secondary content indefinitely — tiny content makes the whole composition look broken even
though it is technically present. The objective is *maximise overall design quality subject to
completeness*.

---

## Decision 5 — Reading image pixels for contrast

The rubric says text should not sit on visually busy imagery, and separately requires contrast. We
cannot change text colour without breaking brand fidelity, so **placement is the only lever we
have**. A white headline over a light part of the gradient is illegible; the same headline over the
dark part is fine. Both are valid JSON.

The ban is on **regenerating, redrawing or replacing** image content. Reading pixels to compute local
variance appears clearly different:

```python
img = Image.open(assets_dir / bg["asset_id"]).convert("L")   # READ — fine
region = img.crop(map_to_source_coords(text_box))
busy = ImageStat.Stat(region).stddev[0]       # high = detailed = bad for text
# generating or saving new pixels → DISQUALIFYING
```

**Keep this as a removable module.** The core engine must work without it. Because this is an
interpretation of a disqualifying rule and the cost of being wrong is elimination, nothing should
depend on it structurally until confirmed.

---

## Who does what

| | Person A — "what and where" | Person B — "is it good" |
|---|---|---|
| Owns | `analyze()`, `generate()` | `filter()`, `score()`, `validate()` |
| Builds | Relationship graph, soft roles, layout strategies, parameter space | Oriented-box geometry, hard-constraint filter, rubric scorer, final validation, test harness |
| Tests against | A dummy scorer returning a constant | Baseline output, which already exists |

Neither blocks the other. Agree the shape of the design dict passed between `generate()` and
`score()` before writing code — it is the schema's `{canvas, elements}` plus whatever role and
relationship annotations we carry alongside.

---

## Timeline

| Dates | Goal |
|---|---|
| Oct 8–11 | Agree this plan. Send the Adobe question. |
| Oct 11–17 | B: scorer + oriented-box geometry. A: relationships + soft roles. |
| **Oct 17** | **Gate: beat the baseline measurably on all three samples.** If not, rethink while there is time. |
| Oct 17–23 | All four strategies. Pixel-contrast module. |
| Oct 23–27 | Docker with `--network none`. Synthetic stress suite. Viewer add-on. README. |
| Oct 27–30 | Freeze, polish, buffer. |
| **Oct 30** | **Submission** |

### Synthetic stress testing

Three samples is nowhere near enough evidence, and there is a real risk of building a sophisticated
solver for three examples. Generate hundreds of variations by perturbing: canvas aspect ratio,
element positions, element counts, group membership, font sizes, text lengths, rotations, spacing,
z-order.

We do not need to know the perfect answer for each. We use them to find constraint violations,
pathological layouts, instability, and sensitivity to small input changes.

**Priority note.** Adobe put "consistency across cases" in the lower-weighted band, so this is
insurance against catastrophic failure on a weird input rather than a direct scoring lever. Build
it, but behind layout quality — not ahead of it.

---

## Environment

```bash
.venv/bin/python -m engine.main          # runs all of input/*.json into output/
```

Python 3.11.4 venv, matching the Dockerfile's `python:3.11-slim`. `cr-renderer` and `skia-python`
installed, real renderer confirmed working (not the box-and-label fallback), both brand font
families resolving.

---

## Measured baseline

The provided baseline multiplies all horizontal values by `target_w / source_w` and all vertical
values by `target_h / source_h`.

| | Level 2 (1600×400) | Level 3 (728×90) |
|---|---|---|
| Aspect drift, non-text elements | 300% on all | 911% on all |
| Text below 10px | 3 of 4 (min 6.7px) | 7 of 7 (min 1.0px) |
| Collisions | subhead over CTA button | pervasive |

The 5% limit is exceeded by roughly 60× and 180×.

**The `fit: contain` trap.** In the L2 render, the product image *looks* correctly proportioned
despite 300% box drift, because `contain` letterboxes it inside the stretched box. **The scorer
checks the box, not the rendered pixels.** Visual inspection alone will not catch proportion
violations — this is why we need both geometric and rendered checks.

---

## Hard rules

Two of these are disqualifiers, not point deductions.

- **Never regenerate, redraw or replace image content**, backgrounds and decorative assets included.
  Reading pixels to inform placement is fine; writing them is not.
- **Never hard-code behaviour for specific designs or canvas sizes.** No branching on `id`, no
  lookup table of known formats.
- Same element ids, same count. Each id exactly once.
- All required fields present; everything on canvas (but see the open question).
- Image proportions within ~5% of the source box, except full-bleed `cover` elements.
- Text content and font family fixed for round one. `font_size` may change.
- Everything installs at Docker **build** time. Round one runs with networking disabled.

**A trap in the starter repo:** `engine/main.py`'s `self_check` compares element ids as **sets**, so
duplicate ids pass locally while failing the official count check. Our own validator must compare
counts. It also ignores rotation and always exits 0.

---

## Open questions

- [ ] **Still open: what "on-canvas" means.** Asked Oct 8; the reply restated the rubric and did not
      address containment or rotation. Re-ask as a yes/no — draft below. This blocks the filter stage.
- [ ] **Get Section 2.5 of the challenge document.** Adobe named it as the most concrete guidance on
      what a good result looks like. We have never seen the challenge document — everything so far
      comes from `RULES.md`, the FAQ, the schema and a summary. Highest-value outstanding item.
- [ ] Search or one algorithm? Decide once the scorer runs and we can measure.
- [ ] Degradation policy for Level 3 when everything cannot fit legibly at once.
- [ ] Where does AI go, if anywhere? No network at scoring, so any model is baked into the image and
      runs locally on the reference hardware.
- [ ] If we use a model, the slot that makes sense is **ranking candidates**, not generating them —
      so a bad model cannot produce invalid geometry. Agree?
- [ ] Do we deliberately keep round one rules-based and save the model for the finale, where
      adaptive copy and font swaps have a live jury that can judge them?
- [ ] Who owns the viewer add-on? Required but unscored, so easy to leave until it is a crisis.
- [ ] Can we both commit to January 11–13 in San Jose? Finalists must attend in person to place,
      regardless of score.

Also worth revisiting before submission: installing the renderer pulls in `datasets`, `pandas`,
`pyarrow` and `huggingface-hub` as transitive dependencies. Heavy Docker image for what we use.

### Re-ask, as yes/no

The first attempt was answered with a rubric summary. A yes/no question is harder to deflect.

> Thanks — that's helpful on the rubric. One thing I don't think it covers: the Level 2 sample has
> `deco_circle_1` at `x: -100`, deliberately bleeding off the corner. Does the structural check
> reject an element whose box extends past the canvas edge, yes or no? And for a rotated element, is
> that check against the unrotated box or the rotated corners?

**If no answer arrives**, default to allowing bleed only for elements that already bleed in the
source, and never introducing new ones. That matches the starter README calling the Level 2 warning
expected, while keeping us conservative everywhere else.

### Why it matters

If we enforce full containment, our code drags a deliberate corner bleed back inside and damages the
composition. If we do not, we may fail a structural check on every task. It is a coin flip right now
and it changes what we build.
