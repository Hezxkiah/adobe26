# CHAMELEON — Build Plan

*Updated 2026-10-09. 21 days to submission.*

If you only read one section, read **"Start here"** at the bottom.

---

## Where we are

Two branches, and they turn out to be two halves of one plan.

| Branch | What's on it |
|---|---|
| `hez` | A working engine. Classifies elements, picks a layout pattern, solves font sizes, emits valid JSON. ~1,900 lines plus notes. |
| `preplanning` | Planning docs. No code. |

**The engine on `hez` is good work and we are keeping it.** The Level 2 banner it produces is a real
layout — logo top-left, product left, headline and subhead centred, badge and CTA stacked right. The
baseline produced an empty middle with a squashed bottle and overlapping text.

What it is missing is a way to tell whether its own output is any good. That is the other half, and
it is what the rest of this plan is about.

---

## The task

`resize(source, target_canvas) -> dict` in `engine/resize.py`. Take a design, take a new canvas size,
return the same elements rearranged to fit. Nothing added, nothing removed.

The design arrives as JSON: a canvas plus text, image and shape elements, each with a box. Nothing is
labelled — no element says it is the headline or the logo. We work that out ourselves.

| | |
|---|---|
| Deadline | October 30, 2026 |
| Round 1 | Fully automated against a rubric. No human judging. |
| Outcome | Top 3 advance, notified November |
| Finale | January 11–13, 2027, San Jose. In person, required to place. |

---

## How we're judged

Adobe confirmed the weights on 9 October when we asked:

| Weighted **most heavily** | Weighted **lower** |
|---|---|
| Genuine rearrangement — *not just scaling or stretching* | Faithfulness to the original design |
| Clean layout | Consistency across cases |
| Readability | |

**This is the single most important fact we have, and it changes things.** We had treated
rearranging as the *method*. Adobe scores it as the *outcome*. A layout can be clean, readable and
perfectly proportioned and still score badly because it reads as a careful scale rather than a
redesign.

Adobe also pointed at **Section 2.5 of the challenge document** as the most concrete guidance on what
a good result looks like. We have never seen that document. Getting it is the highest-value
outstanding item.

### What we can measure ourselves

| Rubric row | Computable? |
|---|---|
| Rearrangement | Yes — distance from what the naive stretch would have produced |
| Clean layout | Yes — collisions, alignment, spacing, empty space |
| Readability | Yes — min font size, text fitting its box, contrast behind text |
| Brand fidelity | Yes — aspect drift against the source box |
| Faithfulness | Partly — reading order, group integrity |
| Consistency | No — but we can test on canvases we invent |

Five of six. That is what makes the approach below possible.

---

## The approach: a generator and a judge

**Don't try to get the layout right in one go. Make several, grade them, send the best.**

There is no execution-time limit in round one — Adobe calls speed "a preference" and says quality
comes first. So producing 100 candidate layouts costs nothing we care about. It still runs in well
under a second.

```
1. LOOK     what is each element, how do they relate      [built, hez]
2. MAKE     several candidate layouts                     [mostly built, needs knobs]
3. BIN      throw out anything breaking a hard rule       [MISSING]
4. GRADE    score the rest, render the top few, compare   [MISSING]
5. CHECK    validate the JSON, don't touch the layout     [built, hez]
```

**Stage 4 is also the development tool.** Once a layout can be scored, we can tell whether a change
helped. Without it we are squinting at PNGs.

**Stage 3 sits before stage 4 deliberately.** An earlier version of this plan scored candidates then
repaired the winner. That invalidates the score — what you hand over is no longer what you graded.
Reject invalid candidates first.

### Why we know the judge is needed

Run the current engine on Level 1 (1080×1080 → 1080×1350):

```
headline:  x140 y420 800×160  →  x140 y420 800×160   identical
cta:       x140 y620 800×60   →  x140 y620 800×60    identical
```

Nothing moved. The canvas grew 270px taller and all of it became empty space — **half the canvas is
dead**. The logo, centred in the source, was shrunk 26% and pinned to the top-left corner.

The `NEAR` archetype fired, which was a defensible call before we knew the weights. Now it is the
worst possible result on the heaviest-weighted row. **A scorer would see half an empty canvas, score
it badly, and pick a different arrangement.** That is the whole argument, in one sample.

---

## What's already built (`hez`)

Four modules. Read `NOTES.md` on that branch — it is unusually honest about its own weak points.

| File | Does |
|---|---|
| `classify.py` | Infers a role per element from geometry. Also reading order and the design's implied margin. |
| `layout.py` | Picks an archetype from the aspect delta, assigns each element a rectangle. |
| `metrics.py` | Text measurement and font-size solving against real glyph widths. |
| `resize.py` | Orchestrates. Backgrounds, decoration, containment, emission. |

### Five things in there worth knowing about

These were found by building, not by planning, and they are not obvious.

**1. Text wrapping is unstable.** A **0.02px** difference in box width flipped the L3 headline between
3 and 4 lines — so the emitted box claimed a height the renderer didn't honour and text overlapped.
Fix: solve against a box 3% narrower (`_WRAP_SAFETY = 0.97`) so you are never sitting on the cliff.

**2. Groups need a bbox transform.** Placing a button's pill and its label separately leaves them
drifting apart. Scale the box around the *whole group* as one unit, then map each member through that
same transform. Internal spacing is preserved exactly.

**3. Two corner marks fight each other.** A top logo and a bottom watermark look identical to a
classifier — both wide, small images. Label them the same and they get pinned to the same corner and
collide. They are separated by which edge they hug.

**4. The margin is recovered from the source.** Rather than hardcoding "5% margin", measure the gap
between content and canvas edge in the original and reuse it. The design states its own spacing.

**5. The constants were sensitivity-tested.** Doubling and halving each threshold showed three of them
change nothing across the samples — reported as *"false confidence"*, because a constant that does
nothing on three samples tells you nothing about hidden designs. Two are knife edges
(`_BLEED_COVERAGE`, `_LOGO_ASPECT`).

### Verified by running it

- Ids intact, no off-canvas, no aspect drift, fonts ≥ 1, content and fonts untouched — on all three samples
- Predicted line counts match what the renderer actually draws
- L2 is a clear, clean banner

---

## Bugs to fix first

Found by running the branch. The first is a hard-rule violation.

### 1. Aspect ratio breaks on narrow targets

Target **160×600** (a standard skyscraper), not in the 78 tested combinations:

| Element | Source | Output | Drift |
|---|---|---|---|
| `product` | 500×500 | 160×500 | **68%** |
| `cta_bg` | 420×90 | 160×90 | **62%** |
| `badge_bg` | 220×56 | 160×56 | **27%** |

Limit is 5%. The containment guard in `resize.py` clamps `width` to the canvas and never re-derives
`height`. Any narrow target hits this. **Hard requirement, not a quality issue.**

### 2. `resize()` mutates the caller's input

`_apply_group_strokes` writes `corner_radius` back onto `design.by_id[...]`, which holds the *same
dict objects* as `source["elements"]`. After the call, the caller's source has changed:

```
L2: badge_bg corner_radius 28 → 37.03
L3: cta_bg   corner_radius 45 →  2.41
```

Works today, because the emit loop reads the mutated dict. Breaks the moment the harness calls
`resize()` twice or compares source before and after. One line: `copy.deepcopy` at the top.

### 3. Decorative bleed logic is dead code

`_place_decorative` computes the source's bleed direction and sets a negative `x`. Twenty lines later
the containment guard does `if el["x"] < 0: el["x"] = 0.0` and undoes it. Zero off-canvas elements in
any output confirms it. Either delete it or make it work — pending the Adobe answer below.

### 4. Rotation uses untilted boxes

Bounds and collisions are checked against the unrotated box, same blind spot as the starter's
`self_check`. L3 has a group at −12°, whose real corners can cross the canvas edge while the box
passes. Compute the four transformed corners.

### 5. Level 3 small text is not readable

`NOTES.md` says "five readable badges". Measured: badge labels at **5.0px**, legal text at **3.2px**.
The headline is genuinely good. The rest isn't solved — at 728×90 with 17 mandatory elements something
has to lose, but it shouldn't be described as fixed.

---

## The scorer

A weighted sum. Reject on hard rules first, then rank.

| Term | How | Weight |
|---|---|---|
| **Rearrangement** | Distance from what the naive stretch would produce, scaled by how much the canvas shape changed | **Heaviest** |
| **Clean layout** | Collisions between content elements (decorations exempt), alignment guide count, gap variance, empty regions | **Heavy** |
| **Readability** | Min font size, measured text height vs box height, contrast behind text | **Heavy** |
| Brand fidelity | Aspect drift vs source box; hard-fail above 5% | Medium |
| Reading order | Sequence comparison, source vs candidate | Light |
| Group integrity | Did a `group_id` get split up | Heavy (cheap to never break) |

### Rearrangement — the heaviest term

Compute what the naive stretch would have produced and compare:

```python
stretched = naive_scale(source, target)
distance  = mean_normalised_box_distance(candidate, stretched)
```

Scale by how much the canvas shape actually changed:

- **Small shape change** → resembling the stretch is fine. No penalty.
- **Large shape change** → resembling the stretch means we didn't do the job. Penalise.

This is the one term where *difference* is rewarded. Keeping it tied to ratio change stops it
scrambling layouts that didn't need scrambling.

### Reading order — handle with care

The naive version ("headline must stay above the button") is wrong, because a different canvas forces
things to move. Three things make it work:

1. **It compares candidates to each other, not to a fixed ideal.** If stacking is impossible at
   728×90, every candidate breaks it equally and the term cancels out. It only separates candidates
   where one kept the order and another scrambled it for nothing.
2. **Measure the reading sequence, not "above".** Flatten the layout into the order an eye travels —
   top to bottom, left to right — and compare. "Product above headline" becoming "product *left of*
   headline" is the same sequence, so no penalty.
3. **Keep the weight light.** Adobe put faithfulness in the lower band. It must never pull against
   rearrangement.

**Exception: group integrity is near-absolute.** A button's label must stay on its button at any
canvas size.

### Measuring text

Reuse `metrics.py` — it already measures against the renderer's own glyph widths, which is the whole
point. Don't write a second measurement path.

### Tuning the weights

Hand-set to start. Tune by looking at renders — **on principle, not against the three samples.**
Tuning until the samples look right is overfitting, and consistency is its own scored row.

---

## Turning one layout into many

The archetypes in `layout.py` already do the hard work. The change is to stop committing to one.

**Now:** pick an archetype from the aspect delta → run it → emit.

**Instead:** run several archetypes, each at several settings → score them all → emit the winner.

Knobs worth varying: the implied margin (try the measured value, and ±50%), which side the focal
image anchors to, alignment (left vs centred), whether the subhead gets its own line, and whether the
CTA sits beside or below the copy.

Four archetypes × ~25 settings ≈ 100 candidates. Each runs in roughly the time one does today.

**A caution on `NEAR`:** "keep the arrangement and scale it" is close to the exact thing the heaviest
rubric row contrasts against. Level 1 is the proof. Keep it available for genuinely small shape
changes; drop it from the candidate pool once the ratio change is significant.

**And the rule that cannot be broken:** archetype selection must come from continuous properties —
`ratio = width / height`, and the ratio change between source and target. A lookup table of known
formats ("Instagram Post") is hard-coding for specific canvas sizes, which disqualifies.

---

## Who does what

| | Person A | Person B |
|---|---|---|
| Owns | `classify.py`, `layout.py`, `metrics.py` | the scorer, the filter, the test harness |
| Next | Fix bugs 1–4. Add knobs so archetypes produce variations. | Build the scorer and filter. Generate a test set. |
| Tests against | A dummy scorer returning a constant | Current `hez` output, which already exists |

Neither blocks the other. The interface is a design dict — the schema's `{canvas, elements}`.

---

## Timeline

| Dates | Goal |
|---|---|
| Oct 9–12 | Agree this plan. Fix bugs 1–4. Scorer v1 running. Re-ask Adobe. |
| Oct 12–17 | Knobs on archetypes. Filter stage. Candidate selection wired up. |
| **Oct 17** | **Gate: the scorer picks a better Level 1 than `NEAR` does today.** |
| Oct 17–23 | Tune weights. Rotation geometry. Contrast module. Generated test set. |
| Oct 23–27 | Docker with `--network none`. Viewer add-on. README. |
| Oct 27–30 | Freeze, polish, buffer. |
| **Oct 30** | **Submission** |

**Generated test set.** Three samples is not enough evidence, and the constants in `classify.py` are
fitted to them — `NOTES.md` says so directly. Perturb the samples: canvas ratio, element positions
and counts, group membership, font sizes, text lengths, rotations, z-order. We don't need the right
answer for each; we need to find constraint violations, instability, and sensitivity to small input
changes. The 160×600 bug is exactly what this catches.

Priority note: Adobe put consistency in the lower-weighted band, so this is insurance against
catastrophic failure rather than a direct scoring lever. Build it, but behind layout quality.

---

## Hard rules

Two are disqualifiers, not deductions.

- **Never regenerate, redraw or replace image content.** Backgrounds and decorative assets included.
  Reading pixels to inform placement is fine; writing them is not.
- **Never hard-code for specific designs or canvas sizes.** No branching on `id`, no format lookup.
- Same ids, same count, each exactly once.
- All required fields present; everything on canvas (see open question).
- Image proportions within ~5% of the source box, except full-bleed `cover` elements.
- Text content and font family fixed for round one. `font_size` may change.
- Everything installs at Docker **build** time. Round one runs with networking disabled.

**A trap in the starter:** `main.py`'s `self_check` compares ids as **sets**, so duplicates pass
locally while failing the official count check. It also ignores rotation and always exits 0. Our own
validator must compare counts.

---

## Open questions

- [ ] **Still open: what "on-canvas" means.** Asked Oct 8; the reply restated the rubric and never
      addressed containment or rotation. Re-ask as yes/no (draft below). Blocks the filter stage.
- [ ] **Get Section 2.5 of the challenge document.** Adobe named it as the most concrete guidance on
      what good looks like. We have never seen the challenge document. Highest-value item.
- [ ] Degradation policy for Level 3 when everything cannot fit legibly. Currently 5px badges.
- [ ] Does AI go anywhere? No network at scoring, so any model is baked into the image. If we use
      one, the slot that makes sense is **ranking candidates**, not generating them — a bad model
      then cannot produce invalid geometry.
- [ ] Who owns the viewer add-on? Required but unscored, so easy to leave until it is a crisis.
- [ ] Can we both commit to January 11–13 in San Jose? Required in person to place.

Also before submission: installing the renderer pulls in `datasets`, `pandas`, `pyarrow` and
`huggingface-hub`. Heavy image for what we use.

### Re-ask, as yes/no

The first attempt got a rubric summary. A yes/no is harder to deflect.

> Thanks — that's helpful on the rubric. One thing I don't think it covers: the Level 2 sample has
> `deco_circle_1` at `x: -100`, deliberately bleeding off the corner. Does the structural check
> reject an element whose box extends past the canvas edge, yes or no? And for a rotated element, is
> that check against the unrotated box or the rotated corners?

**If no answer arrives:** allow bleed only where the source already bleeds, never introduce new bleed.

---

## Environment

```bash
.venv/bin/python -m engine.main      # runs every input/*.json into output/
```

Python 3.11.4, matching the Dockerfile. `cr-renderer` and `skia-python` installed, real renderer
confirmed working, both brand font families resolving.

**Docker is still untested.** `NOTES.md` flags this. The Dockerfile copies `engine/` wholesale and
`requirements.txt` is unchanged, so the new modules should ship — but that is inference, not a
confirmed build.

---

# Start here

Everything above in the order it should happen.

## Person A — the engine

- [ ] **Fix the 160×600 aspect bug.** Containment guard in `resize.py` clamps width without
      re-deriving height. Hard rule, any narrow target hits it.
- [ ] **`copy.deepcopy(source)` at the top of `resize()`.** Stops `corner_radius` leaking back into
      the caller's input.
- [ ] **Delete the dead decorative-bleed code**, or make it survive the containment guard. Pending
      the Adobe answer.
- [ ] **Oriented boxes for rotated elements.** Four transformed corners, not the untilted box.
- [ ] **Add knobs to the archetypes** so each produces several variations instead of one: margin
      (measured, ±50%), focal anchor side, alignment, subhead placement, CTA placement.
- [ ] **Drop `NEAR` from the pool** once the ratio change is significant. Level 1 is why.

## Person B — the judge

- [ ] **Scorer v1.** Collisions, min font size, text overflow, aspect drift, empty space. Run it
      against current `hez` output to get a baseline number.
- [ ] **Add the rearrangement term.** Distance from the naive stretch, scaled by ratio change. This
      is the heaviest-weighted row.
- [ ] **Filter stage.** Hard rules only, rejects before scoring. Would have caught 160×600.
- [ ] **Wire selection up:** generate → filter → score → emit winner, untouched.
- [ ] **Render the top 5 and compare.** We have the renderer locally and no time limit.
- [ ] **Generated test set.** Perturb the samples, sweep the `classify.py` constants.

## Both

- [ ] Send the yes/no re-ask to Adobe.
- [ ] Find Section 2.5 of the challenge document.
- [ ] Confirm January 11–13 availability.
- [ ] Verify the Docker build actually works.

## The gate

**By October 17: the scorer picks a better Level 1 layout than `NEAR` produces today.** If it can't,
the approach needs rethinking while there is still time to do it.
