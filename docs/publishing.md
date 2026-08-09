# Publishing, and why it is gated

Three ways out of the corpus, in increasing order of how hard they are to take back.

| Route | Command | Reversible? |
|---|---|---|
| Notion wiki | `doc-publish sync` | yes - pages are updated in place, and the manifest keeps ids stable |
| Zip bundle | `doc-publish bundle` | yes - you chose who received it |
| Publish repo | `doc-publish publish` | **only until you commit** |
| GitHub Pages | `.github/workflows/site.yml` | no - assume anything served is indexed |

## What the engine guarantees

- Every outbound route is built from `corpus_public.md`. The draft corpus, its
  annotations, and the reports that quote excluded source comments never enter one.
- `doc-publish publish` refuses to run under `CORPUS_MODE=draft`, refuses to copy a
  file named like a draft artefact, and scans every text file in the payload for the
  `> [draft note]` marker before copying anything. A hit aborts the whole publish.
- `doc-publish publish` writes files and stops. It never stages, commits or pushes -
  the diff is meant to be read by a person before it becomes permanent.
- The site workflow is gated to `workflow_dispatch` and Pages is off. Do not add a
  push trigger without deciding, deliberately, that the document should be on the web.

## What it cannot guarantee

Whether *your* document is publishable at all. Data-sharing agreements, an unexamined
thesis, an embargo, a co-author who has not agreed - none of that is visible to a
LaTeX flattener. Record the gates for your project in `publishing.md` in the state
directory, next to the document they apply to, and check them before the first commit
into a public repo rather than after.

The asymmetry worth internalising: a wiki page can be edited, a zip can be superseded,
but a public git history cannot be unpublished. Treat the first `git push` of a site as
the irreversible step, because it is.
