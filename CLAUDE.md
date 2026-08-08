# thesis-agent

The engine that turns a LaTeX document into a corpus, a Notion wiki and a site. This repo
is **generic and public**. Everything specific to a particular document — its subject, its
answering rules, its publishing state — lives in that document's repo under
`.thesis-agent/`, located by `THESIS_REPO`.

Nothing thesis-specific belongs in this repo. If you find yourself writing a chapter
number, a dataset name or a result into a file here, it goes in the state directory
instead.

## Architecture

```
src/thesis_agent/
  config.py     every path and secret, resolved once. Nothing else reads os.environ
                for a path. Real env vars beat .env; missing settings raise ConfigError
                with the variable name, never a silent default.
  ingest/       flatten.py (LaTeX -> text), figures.py (float manifest), build.py,
                manifest.py (interactive exports -> labels)
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
`[UNRESOLVED: ...]` markers in the corpus and are reported by the build.

## Working on it

```powershell
$env:THESIS_REPO = "<path to the document repo>"
thesis-agent config      # how every setting resolved, and from where
thesis-agent build       # non-zero on unresolved macros, missing assets, ambiguous names
```

A non-zero build is a failure, not a warning. `build/` and `data/` are gitignored and must
stay that way: the draft corpus carries source comments, provisional numbers and open
questions.

Two corpora, always: `corpus_public.md` (comments stripped) feeds every outbound route;
`corpus_draft.md` (comments kept as `> [draft note]`) is for the author only. The publish
path hard-fails under `CORPUS_MODE=draft` and scans its payload for the draft marker.

## Answering questions about the document

When asked anything about the document itself, answer from `build/corpus_draft.md`, and
**first read `$THESIS_REPO/.thesis-agent/prompt.md`** — it holds the document's own
answering rules: what its comparisons do and do not establish, which numbers are
contested, how to pitch the language. Those rules are not in this repo and cannot be
inferred from the corpus.

The corpus is ~70k tokens. It fits in context, but a single `Read` truncates it and leaves
you answering from the vocabulary block and Chapter 1 while believing you have the whole
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

`ambiguous` means two files matched: the unique-figure-name convention has been broken.
Never rename a committed figure PNG; LaTeX resolves bare filenames via `\graphicspath` and
the manifest indexes by name, so a rename breaks both with no error.

## Publishing

`thesis-agent sync` is idempotent: unchanged pages make zero API writes, and a second
consecutive run must report `0 writes`. `thesis-agent publish` writes files into
`THESIS_PUBLISH_REPO` and never commits — see `docs/publishing.md` for what is gated and
why the first push of a public site is the irreversible step.
