# AGENTS.md

Instructions for any coding agent working in this repository — Claude Code, Cursor,
GitHub Copilot, Codex, Windsurf, Aider, Zed, Gemini, or a chat assistant a human is
pasting files into. Tool-neutral by design, and the only place this guidance is written:

- `CLAUDE.md` imports this file and holds nothing of its own. Claude Code does not read
  `AGENTS.md` by name, so without it none of this reaches a Claude Code session.
- `.claude/skills/` holds two authoring skills for the contract — the answering prompt
  and the macro adapter — unless `init` was run with `--no-skills`.

Scaffolded by `doc-publish init`. Edit it freely — it is yours now, and `init` will not
overwrite it.

## What this repository is

A long LaTeX document. It is an **input** to a publishing engine (`doc-publish`) that
flattens it into a queryable corpus, and possibly a wiki or a website. It may also be the
target of a companion Python repository that generates figures and tables into it.

This repository should contain **no application code**. The companion repositories locate
it through the `DOC_REPO` environment variable; nothing here imports their code.

## Start here

```bash
doc-publish doctor        # what is set up and what is not; reads only, never writes
doc-publish doctor --json # the same findings as structured data
```

If you are helping someone set this up or diagnosing a problem, **run that first and work
from its output** rather than inferring the state from the file tree. It reports per
check: `ok`, `warn`, `--` (an optional feature, not configured, which is fine), or `FAIL`.

```bash
doc-publish build         # LaTeX -> corpus; non-zero on anything unresolved
doc-publish check         # is this document ready to publish?
```

## Rules that matter

These fail *silently* if broken.

1. **Never rename a committed figure.** LaTeX resolves bare filenames via `\graphicspath`
   and the publishing manifest indexes by name. A rename breaks both with no error; two
   files sharing a stem report as `ambiguous`.

2. **Figures are referenced by bare filename**, never a path. `\graphicspath` resolves
   them and **does not recurse** — every directory holding figures needs its own entry.

3. **Labels mirror figure filenames**: `plot.png` ↔ `\label{Fig: plot}`. Break it and
   re-running a notebook appends a duplicate figure block.

4. **A generated `tables.tex` is not hand-edited.** Edits are overwritten on the next
   save. Change the notebook that produced it.

5. **Never describe a figure you have not looked at.** A caption plus a confident
   description is a fabricated result with a real label attached. Check the asset's status
   in `build/figures.json` — `ok`, `pending`, `missing`, `ambiguous` — and say so when it
   is not `ok`.

6. **A build failure is a failure, not a warning.** Unresolvable things become
   `[UNRESOLVED: ...]` markers and fail the build. Never suppress one or edit it out of
   the corpus — fix the cause. The silent version reads plausibly: an unexpanded count
   macro turns "a family of `\nmodels{}` variants" into "a family of variants" and the
   number is simply gone.

## When asked about this document's content

**Read `.doc-publish/prompt.md` first.** It holds this document's own answering rules —
what its comparisons do and do not establish, which numbers are contested, how to pitch
the language. Those rules are not inferable from the text, and ignoring them is how an
assistant misrepresents someone's research in their name.

Then answer from the built corpus in `build/`, not by reading `.tex` files directly. Cite
sections as §N.N and name figures and tables by label. Separate what the document
**demonstrates** (with n and uncertainty) from what it **argues** from what is **your
extrapolation** — never let the third borrow the first's authority. If you cannot locate a
claim in the corpus, say so rather than reconstructing it from general knowledge.

Lines marked `> [draft note]` are LaTeX source comments: provenance, TODOs, open
questions. They are drafting state, not findings. Never present one as a result.

## Two corpora, and only one may leave the machine

- `corpus_public.md` — source comments stripped. Safe for anything outbound.
- `corpus_draft.md` — comments kept. Provenance, TODOs, contested numbers, questions to
  supervisors. **For the author only.**

Never paste draft-corpus content into anything that leaves this machine. `build/` and
`data/` stay gitignored.

## Publishing state

`.doc-publish/` holds state keyed to this document's labels. **Commit it here.** Losing
`notion_manifest.json` makes the next sync create a second copy of the entire wiki;
losing `anchor_map.json` breaks every inbound link. It is data, not markup — a LaTeX
build ignores it entirely.
