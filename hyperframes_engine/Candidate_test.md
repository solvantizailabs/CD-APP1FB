# Design Skills Assessment — Animated Content Templates & Icon Set

**Role context:** We produce short, animated educational video content for school-level learners (K-12), covering subjects like Science, Math, History, and Civics — think: explainer scenes generated from textbook topics for online lessons. We're evaluating designers/developers who are strong in HTML, CSS, and lightweight JS animation to potentially bring onto the team for end-to-end template development. This assessment is the entire evaluation — there is no separate interview. Please treat it as your best representative work.

Design with that audience and subject range in mind: your templates should read clearly to a school-age student and should work as a general-purpose layout regardless of which subject's content is dropped into them (a "comparison" template should work equally well for "Solids vs Liquids" and "Monarchy vs Democracy," for example).

This is an unpaid skills assessment. We aren't giving you access to any live product, codebase, or brand materials — everything here is self-contained and yours to build independently, without needing to know anything about our specific product or company beyond what's in this brief. That said, please note: strong submissions may be tested inside our actual product as part of evaluating them, and any template or icon we like may be used or referenced in our product going forward, even though this assessment itself is unpaid. If that's not something you're comfortable with, please let us know before starting rather than after submitting.

---

## 1. What we're asking you to build

Two things:

**A. A small set of "scene templates."**
A scene template is a single, self-contained HTML page (HTML + CSS + a small amount of JS) that presents one idea visually for about 5–15 seconds of screen time, in a 1280×720 (16:9) frame, and then plays a short entrance/build animation using **CSS animations or transitions only** (no animation libraries, no video, no GIFs).

A good template is not a static poster — it's a *layout pattern* that could be reused for many different pieces of content by swapping out the text/labels/numbers inside it. Think in terms of the *shape of the idea*, not one specific topic. Examples of shapes an idea can take (you don't have to use these exact ones, they're just to calibrate what we mean):

- Comparing two things side by side
- A sequence of steps or a timeline
- One central idea with several things branching off it
- A cycle or loop that repeats
- A hierarchy or tree of categories
- A before/after or cause-and-effect state change
- A simple data table or grid
- A step-by-step worked example (e.g., a formula or process unfolding)

**How many templates should you make?** That's up to you. Build as many distinct layout patterns as you feel show your range — quality and variety of thinking matter far more than quantity. A handful of genuinely distinct, polished layouts is worth more than a large number of near-duplicates.

**B. A matching icon set.**
Alongside your templates, design a set of simple line/flat icons (as inline SVG) in one consistent visual style — same stroke width, same corner rounding, same level of detail — that could plausibly illustrate a range of general subjects (e.g., a book, a lightbulb, a graph, a cycle/loop symbol, a checkmark, a molecule, a globe, a clock, etc.). Again, the exact number and subject coverage is your call — we're looking at consistency, clarity at small sizes, and range of thinking, not a checklist.

---

## 2. Technical requirements (please follow these exactly — this is part of what's being evaluated)

1. **Plain HTML, CSS, and vanilla JS only.** No React/Vue/build tools, no animation libraries (no GSAP, anime.js, etc.) — animation must be done with native CSS `@keyframes`/`transition`.
2. **Self-contained files.** Each template is a single `.html` file with its CSS in a `<style>` block. No external stylesheets, no external images, no CDN fonts if possible (system fonts are fine). Icons should be inline `<svg>` markup, not linked image files.
3. **Fixed canvas.** Design everything to a fixed 1280×720px frame (don't build for responsive/mobile — this is a video frame, not a webpage).
4. **Data-driven structure.** Structure your HTML/JS so the *content* (titles, labels, list items) is clearly separable from the *layout*. A simple pattern we'd like to see: a small JS object at the top of the file (e.g., `const data = { title: "...", items: [...] }`) that your render logic reads from, rather than text hand-typed directly into the HTML tags. This is the single most important technical requirement — it's what tells us whether your layout is a reusable template or a one-off poster.
5. **Consistent naming.** Name each template file descriptively after its layout pattern, e.g., `template-comparison.html`, `template-timeline.html`, `template-cycle.html`. Name your icon file `icons.html` (or `icons.svg` with a symbol/sprite sheet) and list each icon's intended meaning in a comment or caption.
6. **No external asset dependencies.** Nothing should break if the file is opened offline with no internet connection.

---

## 3. What to submit

- A single folder (zipped) containing:
  - One `.html` file per template
  - Your icon file
  - A short `README.md` (a few sentences is enough) noting anything you want us to know — design decisions, what you'd do with more time, etc.
- Please do not include any proprietary work from other employers/clients.

---

## 4. How this will be evaluated

We'll open each file directly in a browser and judge on:

- **Layout clarity** — does the template communicate its idea at a glance?
- **Reusability** — is content genuinely separated from layout, per the data-driven requirement above?
- **Animation quality** — smooth, purposeful motion that supports the content rather than distracting from it.
- **Icon consistency** — does the icon set read as one coherent family?
- **Code cleanliness** — readable structure, sensible naming, no dead code.

There's no fixed pass/fail line on quantity — a small number of excellent, clearly-reusable templates will score higher than many rushed ones.

If you have questions about scope, ask before you start rather than guessing — we'd rather clarify than have you spend time on the wrong thing.

Thank you for your time — we know unpaid assessments are a real ask, and we appreciate you doing this one.
