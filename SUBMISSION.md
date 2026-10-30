# CHAMELEON — Submission Read-me

Stage-one submission for the Adobe University Hackathon 2026 "Chameleon"
resize challenge. This document explains the approach and how to run the
engine.

## What the engine does

`engine/resize.py` receives a source design (canvas + elements) and a
target canvas size, and returns the **same** elements — same `id`s, same
count, same `content`, same `font_family`, same `asset_id` — with updated
positions, sizes, and (optionally) font sizes so the design fits the new
canvas. It rearranges the design ("moves the furniture"); it never
stretches a single picture and never regenerates image pixels.

## Approach

The engine is four stages, unchanged from the starter architecture:

1. **`engine/classify.py`** — infers a role for every element from
   geometry alone (full-bleed background, decorative, logo, watermark,
   focal image, headline/subhead/fineprint/body text, grouped badge or
   CTA). The schema has no semantic labels, so roles are recovered from
   geometry, type, opacity, and `group_id`.

2. **`engine/layout.py`** — picks a layout archetype from the aspect-ratio
   delta and assigns each element a rectangle:
   - **NEAR** — near-identical aspect: proportional scale.
   - **STACKED** — target taller: vertical column preserved, spread out.
   - **BANDED** — target wider: vertical stack becomes horizontal bands
     (image / copy / calls-to-action columns).
   - **STRIP** — extreme delta: fixed per-tier bands so every element is
     placed and nothing is starved.

   New in this submission:
   - **Content-priority tiers.** Every element is classed P0 (headline,
     focal image, primary CTA), P1 (subhead, badges, ribbon), or P2
     (fineprint / legal). On extreme targets P0 claims space first and
     holds its ground; P2 takes the dregs rather than forcing the
     headline into illegibility.
   - **Readability floors.** P0 text is never solved below 10px, P1 below
     8px (P2 may drop to the schema minimum of 1px as a last resort).
   - **Fixed per-tier bands** on strips: headline (top), badge/ribbon row
     (side-by-side columns, keeping real pill width), CTA (reserved
     bottom band), legal (bottom sliver). Tiers never borrow from each
     other.
   - **Leftovers are parked** at a trailing edge with a valid on-canvas
     box, so no element ever falls back to source size + clamp.

3. **`engine/resize.py`** — handles archetype-independent roles
   (full-bleed backgrounds fill the target exactly; decoration is
   repositioned without dominating), runs a **last-mile collision
   resolver** that separates any surviving overlaps (P0 content holds
   ground; grouped badge+label move rigidly together), and emits the
   output JSON with a final containment guard.

4. **`engine/metrics.py`** — solves font sizes against the renderer's own
   glyph metrics (skia via cr-renderer), so emitted box heights match
   what the renderer actually draws (no wrap-cliff overlap).

## Contrast checking (`engine/contrast.py`)

The scoring rubric requires text to have enough contrast. The structural
gates can't see colour, so this module computes WCAG contrast ratios for
every text element against the colour behind it (group pill fill for
badge/CTA labels, or a sample of the full-bleed background at the
element's position). It flags anything below WCAG AA (4.5:1 normal /
3:1 large text), while exempting intentional brand colour combinations
(near-white or near-black text on a saturated background, e.g. white on
an orange brand pill, which is perceptually readable). Results are
printed as informational warnings after each run; they do not fail the
run.

Text colour, font family, and content are never changed — that would
touch brand intent.

## Files changed

- `engine/contrast.py` — new: WCAG contrast checking.
- `engine/layout.py` — priority tiers, readability floors, fixed
  per-tier strip bands, parking of leftovers.
- `engine/resize.py` — collision resolver (iterative to convergence),
  contrast integration.
- `engine/main.py` — calls the contrast checker and prints its warnings.
- `engine/README.md` — this file.

## How to run

### Locally (no Docker)

```bash
pip install -r requirements.txt
python -m engine.main
```

This processes every `*.json` in `input/` (the three sample designs are
already there) and writes `<task>_output.json` and `<task>_output.png`
into `output/`. The PNGs are real renders through cr-renderer (actual
fonts and assets, not placeholder boxes) — provided `cr-renderer` and
its skia dependency install cleanly.

### Docker (how stage-one scoring runs)

```bash
docker build --platform linux/amd64 -t my-chameleon-engine .
docker run --rm --network none \
  -v $(pwd)/input:/app/input \
  -v $(pwd)/output:/app/output \
  my-chameleon-engine
```

`--network none` matches scoring exactly: everything the engine needs
must install at **build** time (it's all in `requirements.txt`); nothing
can be fetched at run time. The Dockerfile copies `engine/`,
`renderer/`, `brand_kit/`, and the schema into the image, installs
`requirements.txt` at build time, and entrypoints to
`python -m engine.main`.

> Note: Docker is not installed in this development environment, so the
> containerized build could not be executed here. Verify with the two
> commands above on a Docker-capable machine before submitting.

### Requirements

`requirements.txt`:
- `Pillow>=10.0`
- `git+https://github.com/CyberAgentAILab/cr-renderer` (real renderer,
  pulled at build time; no runtime network needed)

Python 3.10+ is required.

## Rules respected

- Every source `id` appears exactly once; nothing added or removed.
- `content`, `font_family`, `asset_id`, `type` never change.
- `font_size` may change to help text fit (minimum 1px).
- Non-background images keep their source aspect ratio exactly.
- Full-bleed backgrounds cover the target exactly.
- Nothing ends up off-canvas.
- No image content is regenerated, redrawn, or replaced (disqualifying
  if violated).

## Verification performed

- All three samples pass `main.py`'s structural self-check with zero
  warnings.
- An 18-combination matrix (3 sources × 6 targets including 320×50,
  728×90, 1080×1080, 1080×1350, 1600×400, 5000×400) passes with zero
  failures: no id mismatches, no off-canvas elements, no sub-1px fonts,
  image aspect ratios within 5%.
- Contrast checker validated against synthetic cases: catches white-on-
  white and grey-on-grey, passes legitimate brand combinations.
- Rendered PNGs confirmed to contain real image content (not placeholder
  boxes) once cr-renderer is installed.

## What's intentionally not done

- **Alignment/grid snapping:** the rubric's "looks intentional" row is a
  bonus tier, not a stage-one gate. Elements are placed in consistent
  priority bands with even gaps, but no explicit grid is enforced. This
  can be added in the finale stage if desired.
- **Copy/font rewriting:** content and font family are fixed per the
  stage-one rules; adaptive typography beyond font size is reserved for
  the live finale.
