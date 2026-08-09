# .thesis-agent

Publishing state for a document agent that flattens this LaTeX into a corpus and
publishes it — as a wiki, a static site, or a question-answering index.

**Nothing here is compiled by LaTeX.** It is data, not markup, and the document builds
identically with this directory absent. Delete it if you are not publishing anywhere but
Overleaf.

It lives in *this* repository rather than in the agent's, for two reasons. The agent is
likely to be public while a draft is not, and every file here is keyed to labels in this
document — co-locating them means checking out an older commit gives you the matching
state instead of silently drifting.

## What ships, and what must not

Two kinds of file end up in this directory, and confusing them is expensive.

**Contract** — committed, edited by you:

| File | Purpose |
|---|---|
| `prompt.md` | What the agent must know to answer about this document without misrepresenting it |
| `macros.py` | Teaches the flattener any bespoke LaTeX macro this document defines |
| `database_settings.json` | Schemas for the databases a wiki publisher creates |

**Generated state** — created by the publisher, gitignored here, and *not* to be
committed from a template:

| File | Why it matters |
|---|---|
| `notion_manifest.json` | Maps each section to its live wiki page id |
| `anchor_map.json` | Maps each anchor to its stable URL |
| `*_report.md` | Build reports from the last publish |

The first two are load-bearing once you are publishing: lose `notion_manifest.json` and
the next sync creates a **second copy of the entire wiki**, orphaning the existing pages
and every link into them. Lose `anchor_map.json` and every inbound URL breaks. They are
gitignored in this template because they belong to a project, not to a template — but in
your own repository you may well want to commit them for exactly the reason above. That
is a decision to make deliberately, once, and to write down.

## First steps

1. Rewrite `prompt.md`. The skeleton is structure, not content — an agent given the
   default text will answer confidently and wrongly.
2. Add an entry to `macros.py` for every bespoke macro in `document_settings.sty`.
3. Point your agent at this repository and check its first output against a section you
   know well.
