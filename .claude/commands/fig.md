---
description: Find and display a thesis figure by topic or label
argument-hint: <topic or figure label>
---

Find the figure for: $ARGUMENTS

1. Search `build/figures.json` by caption, label and section.
2. If several match, list them with captions and ask which - do not guess.
3. For the chosen figure:
   - If an asset's status is `ok`, read the PNG from `build/figures/` and show it.
   - If `pending`, say the figure is commented out in the LaTeX awaiting a notebook
     re-run, name the notebook if a draft note gives it, and stop. Do not describe it.
   - If `missing` or `ambiguous`, report that - `ambiguous` means the unique-figure-name
     convention has been broken and two files match.
4. Give the caption, the section it appears in, and what the figure actually shows -
   only if you have seen the image.
