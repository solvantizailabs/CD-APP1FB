# Internal notes — do not share with candidate

## Why the brief is written this way

- No mention of "Hyperframes," your app, or that submissions may be used in production — the candidate is told this is a standalone skills test with no client work involved. If you intend to actually adapt or reuse any submitted template/icon in the real product, that changes the ethics/legal picture (see below) — don't skip that step.
- The "data object separated from layout" requirement (section 2.4 of the brief) is what makes a submission portable into your actual template system without you needing to ask the candidate to restructure anything.
- CSS-only animation was specified so nobody can reverse-engineer that your engine runs on GSAP.
- No fixed template/icon count, per your instruction — quantity is explicitly de-emphasized in the rubric so you're judging skill, not free labor volume.

## Before you send this to anyone

You said this is unpaid and the candidate won't be told their output may be used. If you plan to actually take a submitted template or icon and put it into the shipping product:
- That's a real legal exposure point (using someone's unpaid work product commercially, without consent, is a common source of disputes) — at minimum, add a line to the brief making clear submissions may be used or referenced by the company, even if unpaid. Silence on this is the riskiest option, not the safest.
- If you'd rather not add that line, treat all submissions purely as evaluation artifacts you look at and get *inspired by* (not literally ship), and rebuild anything you like in-house.
This is worth a deliberate decision rather than defaulting into it — happy to redraft the disclosure line if you tell me which way you want to go.

## How to plug a strong submission into the engine for real testing

1. Drop the candidate's `.html` file into `hyperframes_engine/scratch/` and open it directly in a browser — it's self-contained, so this works with no build step.
2. To test it inside an actual generated lesson: convert their render logic into a `render(scene_no, template_data)` / `animate(...)` module shaped like the existing files in `hyperframes_engine/templates/` (see `TitleSlide.js` or `GeneralScene.js` for the minimal shape), wire it into the `templates` map in `run-storyboard.js`, and add an entry to `hyperframes_engine/shared/template-registry.json` with `"status": "experimental"` so it's flagged as unproven and excluded from normal LLM generation until you've vetted it.
3. For icons: check them against the existing style in `hyperframes_engine/shared/icons.js` / `theme.js` for stroke-width and color-token consistency before merging.

## Quick rubric to fill in per candidate (for your own comparison)

| Criterion | Notes |
|---|---|
| Layout clarity | |
| Reusability (data/layout separation) | |
| Animation quality | |
| Icon consistency | |
| Code cleanliness | |
| Would directly reuse without rework? (Y/N) | |
| Would hire? (Y/N) | |
