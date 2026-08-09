---
name: write-macro-adapter
description: Write or extend .thesis-agent/macros.py so a publishing agent can expand the LaTeX macros this document defines. Use when `thesis-agent check` reports macros with no adapter entry, after adding a \newcommand to the preamble, or when published text contains raw LaTeX like \foo{bar}.
---

# Writing the macro adapter

`.thesis-agent/macros.py` teaches the flattener how to expand the macros **this document
defines**. A macro with no entry does not fail: it survives into the corpus as raw LaTeX
and is quoted to a reader verbatim. `\ptg{}{solar}` in the middle of a sentence is the
symptom.

## What needs an entry, and what does not

Only macros the document itself defines. Package conventions — `\acrshort`, `\gls`,
`\cref`, `\ExecuteMetaData`, biblatex commands — are handled upstream, and
re-implementing them here causes double expansion.

Find the candidates:

```bash
thesis-agent check          # lists defined macros with no adapter entry
grep -n '\\newcommand\|\\DeclareRobustCommand' *.sty Preamble/*.tex
```

Then triage each one. Layout shorthands (`\width`, spacing helpers) expand harmlessly and
need nothing. Anything carrying *meaning* — a term, a symbol, a piece of notation — needs
an entry, because that meaning is what a reader of the published version must see.

## The contract

Pure text to text, called once at the start of expansion, and **deterministic** — the
Notion publisher hashes rendered content to decide what to rewrite, so a function that
expands the same input two different ways produces spurious edits on every sync.

```python
def expand(text, vocab, unresolved) -> str:
    """Expand this document's macros. Returns the expanded text."""
```

- `vocab` — the document's loaded vocabulary (acronyms, glossary entries), for macros
  whose expansion depends on it. Ignore it if yours do not.
- `unresolved` — a list to **append** problems to. Do not return them, and do not raise:
  the flattener surfaces the list as build-report entries, whereas raising aborts a whole
  publish over one malformed macro in one paragraph.
- Returning anything but a string raises `AdapterError`.

There is an optional second hook, `vocabulary() -> list[str]`, returning extra vocabulary
lines to prepend to the corpus. A module must define at least one of the two, or loading
it is an error — an adapter that defines neither is almost always a typo.

Check the real signature in `thesis_agent/ingest/adapter.py` before writing: it is the
kind of thing that changes, and a mismatch fails at flatten time rather than at import.

## How to write one

1. **Read the definition.** Argument count and order matter, and a macro with an optional
   argument (`\newcommand{\x}[2][default]`) needs a regex that tolerates its absence.
2. **Decide the rendered form.** What should a reader see? Usually the expansion the
   LaTeX produces, in plain prose — not the macro's internal shorthand.
3. **Treat an unknown key as a problem, not a guess.** If the macro looks up a table of
   forms and the key is missing, append to `problems` and return something conservative.
   Silently guessing puts invented text under the author's name.
4. **Sweep for survivors.** After substitution, scan for any of your macro names still
   present and report them — that catches nesting and unusual spacing the regex missed.

```python
FOO = re.compile(r"\\foo\{([^}]*)\}")

def expand(text, vocab, unresolved):
    def foo(match):
        key = match.group(1).strip()
        if key not in TABLE:
            unresolved.append(rf"\foo{{{key}}}: unknown key")
            return key                      # conservative, never invented
        return TABLE[key]

    text = FOO.sub(foo, text)
    for leftover in sorted(set(re.findall(r"\\(foo)\b", text))):
        unresolved.append(rf"\{leftover} survived expansion")
    return text
```

## Verifying

Regex against LaTeX is approximate, so check the output rather than trusting the pattern:

```bash
thesis-agent build          # then grep the corpus for a stray backslash
grep -n '\\[a-zA-Z]\+{' build/corpus_public.md | head
```

Nested braces are the usual failure: `[^}]*` stops at the first `}`, so
`\foo{\emph{bar}}` truncates. If a macro genuinely takes marked-up arguments, match
balanced braces explicitly rather than widening the character class.

## Pitfalls

- **Do not expand package macros.** Double expansion is harder to spot than none.
- **Keep it deterministic.** No timestamps, no dict iteration order, no randomness.
- **Re-run `thesis-agent check` after editing the preamble** — a macro added to the `.sty`
  months later is exactly the one that reaches a reader as raw LaTeX.
