r"""Macro adapter for this document. Loaded and executed by the engine's flattener.

See `doc_publish/ingest/adapter.py` for the contract. In short: pure text -> text,
called once at the start of macro expansion, deterministic, and it *reports* unresolved
keys rather than dropping them. Determinism matters because the Notion publisher hashes
rendered content to decide what to rewrite; a function that expands the same input two
ways produces spurious edits on every sync.

    def expand(text, vocab, unresolved) -> str

`vocab` is the document's loaded vocabulary (acronyms and glossary entries), for macros
whose expansion depends on it. `unresolved` is a list to **append** problems to — the
flattener surfaces them as build-report entries. Returning a non-string raises.

`vocabulary()` is the optional second hook: return a list of strings to prepend to the
corpus as extra vocabulary. A module must define at least one of the two.

You only need an entry for macros **this document defines**. Package conventions the
engine already understands — \acrshort, \gls, \cref, \ExecuteMetaData, biblatex commands —
are handled upstream and must not be re-implemented here.

A macro that is defined in document_settings.sty but missing here does not fail loudly: it
survives into the corpus as raw LaTeX and gets quoted to a reader verbatim. That is the
failure this file exists to prevent.
"""
import re

# --- deliberately not expanded -------------------------------------------------
# `doc-publish check` flags any macro this document defines that is not mentioned
# anywhere in this file. Listing one here is how you say "considered, needs no entry" —
# the right answer for layout shorthands, which carry no meaning a reader must see and
# appear only inside markup the flattener drops.
#
# Anything carrying meaning — a term, a symbol, a piece of notation — belongs below
# instead.
IGNORED = {
    "width",   # \newcommand{\width}{0.95\textwidth}: figure sizing only
}

# --- example -----------------------------------------------------------------
# Delete this and write your own. It shows the shape: a lookup keyed by the macro's
# argument, and an unknown key reported rather than guessed at.
#
# Corresponds to a hypothetical  \newcommand{\keyterm}[1]{\textsc{#1}}  in
# document_settings.sty.

KEYTERM = re.compile(r"\\keyterm\{([^}]*)\}")

TERMS = {
    "example": "example term",
}


def expand(text, vocab, unresolved):
    """Expand this document's bespoke macros. Returns the expanded text."""

    def keyterm(match):
        key = match.group(1).strip()
        if not key:
            unresolved.append(r"\keyterm{} with an empty argument")
            return ""
        if key not in TERMS:
            # Report and fall back to the literal key: never invent text, because
            # whatever this returns is published under the author's name.
            unresolved.append(rf"\keyterm key '{key}' is not in TERMS")
            return key
        return TERMS[key]

    text = KEYTERM.sub(keyterm, text)

    # Catch anything this document defines that the substitutions above missed —
    # nesting and unusual spacing are the usual causes.
    for leftover in sorted(set(re.findall(r"\\(keyterm)\b", text))):
        unresolved.append(rf"\{leftover} survived expansion")

    return text
