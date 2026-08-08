# thesis-agent

Turn a LaTeX thesis or paper into a queryable corpus, a Notion wiki, and an
offline-capable website — from one parse and one page plan, so the streams cannot drift.

```
LaTeX document ──ingest──> corpus_public.md ──┬──> Notion wiki      (thesis-agent sync)
   (THESIS_REPO)           corpus_draft.md    ├──> Quarto site      (thesis-agent site)
                           labels/figures ────┼──> publish repo     (thesis-agent publish)
                                              └──> chat server      (thesis-agent app)
```

Written for one thesis and then generalised. It knows about `subfiles`, `\ExecuteMetaData`
table libraries, `\graphicspath` with bare filenames, glossary macros, and `\cref` — the
conventions a real document accumulates, rather than a clean-room subset.

## Install

```bash
pip install -e .                # ingest + publish
pip install -e ".[app]"         # + the chat server
cp .env.example .env            # then fill in THESIS_REPO
```

Only `THESIS_REPO` is required. `thesis-agent config` prints how every setting resolved
and from where, which is the fastest way to diagnose a path problem.

## Use

```bash
thesis-agent build              # LaTeX -> corpora, labels, figure manifest
thesis-agent site               # -> the rendered site (needs Quarto)
thesis-agent serve              # view it locally
thesis-agent sync               # -> Notion (idempotent; a second run makes 0 writes)
thesis-agent publish            # -> copy site + corpus into the publish repo
```

`thesis-agent build` exits non-zero on any unresolved macro, missing live figure asset,
or ambiguous figure filename. Treat that as a build failure, not a warning.

## Two corpora — the decision that matters most

LaTeX source comments are not noise. In a working draft they carry the notebook that
produced each headline number, inline TODOs, figure blocks awaiting a re-run, and
questions addressed to supervisors.

- **`corpus_public.md`** — comments stripped. This is what every outbound route uses.
- **`corpus_draft.md`** — comments kept as `> [draft note]`, grouped per section and
  mirrored into `annotations.json`. The viva-prep and drafting build. It will tell you
  which numbers are provisional and where two runs disagree.

Serving the draft build publicly leaks the document's open problems. Running only the
public build wastes the most useful material in the repo. Hence both, selected by
`CORPUS_MODE` — and the publish path refuses to run unless it is `public`.

## Where state lives, and why not here

Publishing state — the Notion page-id manifest, the anchor map, signposts, the
document-specific answering rules — lives in **the document repo**, under
`.thesis-agent/`, not in this one.

Two reasons. It is keyed to labels in that repo, so co-locating them means an older
checkout carries matching state instead of drifting. And this repo is public while most
documents are not: the reports quote the source comments they excluded, which makes them
exactly as unpublishable as the draft.

Losing `notion_manifest.json` orphans the wiki — the next sync creates a second copy of
every page. Losing `anchor_map.json` breaks every inbound link into the site. Commit them
in the document repo; they are small.

## Adapting it to your document

`thesis_agent/config.py` is the only place paths are decided. Beyond that:

- `.thesis-agent/prompt.md` in your document repo — a `# Document` section describing
  what the text is, and a `# Rules` section for the distinctions that are load-bearing in
  your argument. The generic prompt (citations, the demonstrated/argued/extrapolated
  tiers, figure honesty) is in `app/prompt.py` and needs no editing.
- `ingest/flatten.py` — macro expansion. Project-specific macros are the most likely
  thing to need extending; unresolvable ones become `[UNRESOLVED: ...]` markers in the
  corpus and are reported by the build rather than dropped.
- `publish/quarto/qmd.py` — `SHIM_MACROS`, for maths the renderers do not know.

## When to add retrieval

Not yet. This is long-context plus prompt caching, not RAG: the whole document sits in a
cached system block, which is what cross-chapter questions need. Revisit if the corpus
passes ~170k tokens (the build prints its size) or if per-query cost starts to matter.
Then use **hybrid** BM25 + embeddings, never embeddings alone — the lexical tokens in a
technical document are exactly what semantic similarity smears together.

## Licence

MIT. `docs/publishing.md` is worth reading before the first publish to a public repo.
