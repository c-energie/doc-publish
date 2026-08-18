# doc-publish

The engine that turns a LaTeX document into a corpus, a Notion wiki and a site. This repo
is **generic and public**. Everything specific to a particular document — its subject, its
answering rules, its publishing state — lives in that document's repo under
`.doc-publish/`, located by `DOC_REPO`.

Nothing document-specific belongs in this repo. If you find yourself writing a section
number, a dataset name or a result into a file here, it goes in the state directory
instead.

A document set up before the rename keeps its state in `.thesis-agent/`, and `config.py`
still honours it whenever the new name is absent. Never "fix" that by pointing at
`.doc-publish/` and finding nothing: the next sync would then create a **second copy of
the whole wiki**, orphaning every existing page and every link into them. Migrate with
`git mv` in the document repo and commit it. `DOC_*` settings likewise fall back to their
old `THESIS_*` spellings, with a note printed once per name.

## Architecture

```
src/doc_publish/
  config.py     every path and secret, resolved once. Nothing else reads os.environ
                for a path. Real env vars beat .env; missing settings raise ConfigError
                with the variable name, never a silent default.
  ingest/       flatten.py (LaTeX -> text), figures.py (float manifest), build.py,
                manifest.py (interactive exports -> labels), adapter.py (optional
                per-document macros.py, loaded from the state dir)
  publish/      emit.py + structure.py (parse -> page plan, shared by both streams),
                sync.py (Notion), build_site.py + quarto/ (site), to_repo.py (publish
                repo), bundle.py, serve.py
  app/          prompt.py (generic half of the system prompt), server.py, tools.py
```

Ordering in `flatten.flatten` is load-bearing: inline subfiles → resolve
`\ExecuteMetaData` (its tags are *delimited by comments*) → split comments → expand
macros → number sections. Stripping comments before pulling tagged tables silently
deletes every table in the document.

Nothing is dropped silently: unresolvable macros, keys and includes become
`[UNRESOLVED: ...]` markers in the corpus and are reported by the build. That extends to
the document's own `\newcommand`s — any that reach the corpus unexpanded are reported and
fail the build, because the silent version reads plausibly: a count macro resolves to
nothing, so "a family of \nmodels{} variants" becomes "a family of variants" and the
number is simply gone.

Zero-argument literals (`\newcommand{\nmodels}{40}`) are expanded by the engine; they are
a package convention, not a document quirk. Macros taking arguments belong in the
document's own `macros.py` (see `ingest/adapter.py`). Most documents have none.

## Working on it

```powershell
uv sync --extra dev                 # + --extra app for the chat server, --extra agent for the SDK backend
uv run python -m pytest tests -q    # `uv run pytest` fails here: "trampoline failed to canonicalize script path"
uv run python -m pytest tests/test_contract.py::test_check_fails_on_an_unfinished_prompt -q

$env:DOC_REPO = "<path to the document repo>"
doc-publish config      # how every setting resolved, and from where
doc-publish build       # non-zero on unresolved macros, missing assets, ambiguous names
```

There is no linter or formatter here; `pytest` is the whole check.

`doc-publish agent` must be run as that subcommand, never under the uvicorn CLI. Uvicorn
installs `WindowsSelectorEventLoopPolicy` on startup, and a selector loop cannot spawn
subprocesses — which is exactly how the Agent SDK backend works, so the two are mutually
exclusive. `--reload` is unavailable there for the same reason: the reloader's child
re-creates the loop under uvicorn's policy. Restart it by hand. See `cli.py`.

A non-zero build is a failure, not a warning. `build/` and `data/` are gitignored and must
stay that way: the draft corpus carries source comments, provisional numbers and open
questions.

Two corpora, always: `corpus_public.md` (comments stripped) feeds every outbound route;
`corpus_draft.md` (comments kept as `> [draft note]`) is for the author only. The publish
path hard-fails under `CORPUS_MODE=draft` and scans its payload for the draft marker.

## Answering questions about the document

When asked anything about the document itself, answer from `build/corpus_draft.md`, and
**first read `$DOC_REPO/.doc-publish/prompt.md`** — it holds the document's own
answering rules: what its comparisons do and do not establish, which numbers are
contested, how to pitch the language. Those rules are not in this repo and cannot be
inferred from the corpus.

The corpus is ~70k tokens. It fits in context, but a single `Read` truncates it and leaves
you answering from the vocabulary block and §1 while believing you have the whole
thing. Read it in successive `offset`/`limit` passes at the start of any session with more
than one question. For a single lookup, `grep -n "<term>" build/corpus_draft.md` then read
that range plus ~80 lines either side.

Structure: headings are `## §N.N Title`, matching the document exactly. `[FIGURE <label>]`
and `[TABLE <label>]` mark where floats sit. `build/labels.json` maps every `\label` to its
section; `build/annotations.json` is every draft note with its section.

Universal rules, whatever the document:

- Cite the section for every substantive claim, as §N.N. Name figures and tables by label.
- Separate what the document **demonstrates** (with n and uncertainty) from what it
  **argues** from what is **your extrapolation**. Never let the third borrow the first's
  authority.
- If you cannot locate a claim in the corpus, say so. Do not reconstruct it from general
  knowledge of the field.
- `> [draft note]` lines are source comments — drafting state, not findings. Use them to
  say where a number came from or that a point is unresolved. Never present one as a
  result.

## Figures

`build/figures.json` maps labels to files under `build/figures/`. Read the PNG directly to
show one. If an asset's status is `pending`, `missing` or `ambiguous`, say so. Never
describe a figure from its caption alone — a caption plus a confident description is a
fabricated result with a real label attached to it.

A static may be a **PDF**: a document whose figures come from a plotly pipeline exports
vector, because LaTeX embeds PDF natively. Only figures with no interactive export stay
raster. That is not a free choice — a PDF renders in neither a Notion image block nor an
HTML `<img>`, so a figure without an export must be raster or it appears nowhere.

`ambiguous` means two files matched the same stem: the unique-figure-name convention has
been broken, and the figure is dropped from *both* published streams while the LaTeX
carries on compiling from whichever the graphics extension order picked. The producing
pipeline must delete a static it supersedes rather than leaving both. Never rename a
committed figure; LaTeX resolves bare stems via `\graphicspath` and the manifest indexes
by name, so a rename breaks both with no error.

**Order matters:** `doc-publish figures` writes `build/figures_manifest.json`, mapping
labels to their `.html` exports. It is what lets the Notion stream embed the hosted plot
instead of a picture of it; without it every figure falls back to its static image, which
for a PDF static means it does not publish at all. `sync` reports that rather than
emitting silently empty figure cards.

## Publishing

The Notion and Quarto streams are not independent. Both build their page plan from
`structure.build_plan`, with the pinned placements read from the Notion manifest, so the
two keep identical page boundaries and a change to the plan lands on both. The pin is
what makes that safe: a section's first paged/inlined decision is recorded and later runs
never restructure on their own — promoting a section moves its content to a new page ID,
which breaks every inbound link and orphans any comments on it. A run whose fresh
verdict disagrees with the pin *reports* it ("§6.3 now exceeds threshold — promote?") and
keeps the pin. Promotion means deleting the pin from the manifest by hand and re-running.

`doc-publish sync` is idempotent: unchanged pages make zero API writes, and a second
consecutive run must report `0 writes`. `doc-publish publish` writes files into
`DOC_PUBLISH_REPO` and never commits — see `docs/publishing.md` for what is gated and
why the first push of a public site is the irreversible step.
