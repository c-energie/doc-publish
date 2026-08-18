# doc-publish

Turn a LaTeX thesis or paper into a queryable corpus, a Notion wiki, and an
offline-capable website — from one parse and one page plan, so the streams cannot drift.

```
LaTeX document ──ingest──> corpus_public.md ──┬──> Notion wiki      (doc-publish sync)
   (DOC_REPO)           corpus_draft.md    ├──> Quarto site      (doc-publish site)
                           labels/figures ────┼──> publish repo     (doc-publish publish)
                                              └──> chat server      (doc-publish app)
```

Written for one thesis and then generalised. It knows about `subfiles`, `\ExecuteMetaData`
table libraries, `\graphicspath` with bare filenames, glossary macros, and `\cref` — the
conventions a real document accumulates, rather than a clean-room subset.

## Install

```bash
pip install -e .                # ingest + publish
pip install -e ".[app]"         # + the chat server
pip install -e ".[dev]"         # + pytest;  then: pytest tests -q
doc-publish env                 # write a .env here;  then fill in DOC_REPO
```

Only `DOC_REPO` is required. `doc-publish config` prints how every setting resolved
and from where, which is the fastest way to diagnose a path problem.

`doc-publish env` never overwrites an existing `.env` — it may hold the only copy of a
token, and there is no `--force`. Run against one that exists, it audits instead: which
required settings are absent, which keys are unrecognised, and which are spelled with a
pre-rename `THESIS_*` name that still works but should be migrated.

## Use

```bash
doc-publish doctor             # what is set up and what is not; reads only
doc-publish build              # LaTeX -> corpora, labels, figure manifest
doc-publish site               # -> the rendered site (needs Quarto)
doc-publish serve              # view it locally
doc-publish sync               # -> Notion (idempotent; a second run makes 0 writes)
doc-publish publish            # -> copy site + corpus into the publish repo
```

`doc-publish build` exits non-zero on any unresolved macro, missing live figure asset,
or ambiguous figure filename. Treat that as a build failure, not a warning.

**Start with `doc-publish doctor`.** It walks every setup condition at once — the root
`.tex` it found, where the glossary and bibliography resolved to, whether every
`\graphicspath` directory exists, the contract, and the optional streams — and prints a
specific fix for anything not ready. `--json` emits the same findings as structured data,
so an assistant helping someone set this up can read the state instead of guessing.

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
`.doc-publish/`, not in this one.

Two reasons. It is keyed to labels in that repo, so co-locating them means an older
checkout carries matching state instead of drifting. And this repo is public while most
documents are not: the reports quote the source comments they excluded, which makes them
exactly as unpublishable as the draft.

Losing `notion_manifest.json` orphans the wiki — the next sync creates a second copy of
every page. Losing `anchor_map.json` breaks every inbound link into the site. Commit them
in the document repo; they are small.

## Adapting it to your document

Setting up the whole toolchain, or pointing this at a thesis that already exists? The
end-to-end guide is
[writing-template/SETUP.md](https://github.com/c-energie/writing-template/blob/main/SETUP.md).
This section covers the engine's half of it.

Start by scaffolding the contract into your document repo, which writes the files below
and two Claude skills for authoring them:

```bash
export DOC_REPO=/path/to/my-document
doc-publish init      # write .doc-publish/ and .claude/skills/
doc-publish check     # report what is still unfinished
```

`check` is worth re-running after any preamble change: it catches an unfinished prompt and
any macro your document defines that the adapter has never heard of. Unhandled, such a
macro does not error — it survives into the corpus as raw LaTeX and is quoted to a reader
verbatim.

`doc_publish/config.py` is the only place paths are decided. Beyond that:

- `.doc-publish/prompt.md` in your document repo — a `# Document` section describing
  what the text is, and a `# Rules` section for the distinctions that are load-bearing in
  your argument. The generic prompt (citations, the demonstrated/argued/extrapolated
  tiers, figure honesty) is in `app/prompt.py` and needs no editing.
- `publish/quarto/qmd.py` — `SHIM_MACROS`, for maths the renderers do not know.

Most documents need neither of the above and no code changes at all.

### Your document's own macros

Package conventions — `\acrshort`, `\gls`, `\cite`, `\cref`, `\ExecuteMetaData`,
`\graphicspath` — are handled for you. So are zero-argument literals: if your preamble
says `\newcommand{\nmodels}{40}`, the corpus says 40. Nothing to configure.

Macros that take arguments are the exception, and most documents have none. If yours
does, the build tells you rather than guessing:

```
1 unresolved macro/key markers:
   document macro '\ptg' (74x) - no adapter expands it
```

Then drop a `macros.py` in the state directory exporting `expand(text, vocab, unresolved)`
and, optionally, `vocabulary()`. See `ingest/adapter.py` for the full contract — it is
called at one fixed point in the pipeline, must be pure text→text, and reports unresolved
keys the same way the engine does, so the build still fails on anything it cannot handle.

Two things worth knowing. That file is **imported and executed** — the same trust you
already give a `Makefile` in a repo you control, but do not point the engine at a repo
you do not. And a `macros.py` that fails to import is a hard error, never a warning:
silently skipping it would publish raw `\ptg{...}` into the corpus.

## When to add retrieval

Not yet. This is long-context plus prompt caching, not RAG: the whole document sits in a
cached system block, which is what cross-section questions need. Revisit if the corpus
passes ~170k tokens (the build prints its size) or if per-query cost starts to matter.
Then use **hybrid** BM25 + embeddings, never embeddings alone — the lexical tokens in a
technical document are exactly what semantic similarity smears together.

## Licence

MIT. `docs/publishing.md` is worth reading before the first publish to a public repo.
