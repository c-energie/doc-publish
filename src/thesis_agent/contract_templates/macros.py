r"""Macro adapter for this document. Loaded and executed by the agent's flattener.

The contract, in short: pure text -> text, called once at the start of macro expansion,
deterministic, and it *reports* unresolved keys rather than dropping them. Determinism
matters because a wiki publisher hashes rendered content to decide what to rewrite; a
function that expands the same input two ways produces spurious edits on every sync.

You only need an entry here for macros **this document defines**. Package conventions the
engine already understands — \acrshort, \gls, \cref, \ExecuteMetaData, biblatex commands —
are handled upstream and must not be re-implemented.

A macro that is defined in document_settings.sty but missing here does not fail loudly: it
survives into the corpus as raw LaTeX and the agent quotes it at a reader verbatim. That is
the failure this file exists to prevent, which is why unresolved keys are collected and
returned rather than silently passed through.
"""
import re

# --- deliberately not expanded -------------------------------------------------
# `thesis-agent check` flags any macro this document defines that is not mentioned
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
# argument, and an unknown key treated as an error rather than guessed at.
#
# Corresponds to a hypothetical  \newcommand{\keyterm}[1]{\textsc{#1}}  in
# document_settings.sty.

KEYTERM = re.compile(r"\\keyterm\{([^}]*)\}")


def expand(text: str) -> tuple[str, list[str]]:
    """Expand this document's bespoke macros.

    Returns the expanded text and a list of human-readable problems. Anything in that
    list is surfaced by the flattener rather than swallowed, so an unknown key shows up
    as a build report entry instead of as garbled prose in a published page.
    """
    problems: list[str] = []

    def keyterm(match: re.Match) -> str:
        term = match.group(1).strip()
        if not term:
            problems.append(r"\keyterm{} with an empty argument")
            return ""
        return term

    text = KEYTERM.sub(keyterm, text)

    # Catch anything this document defines that nobody taught the flattener about.
    for leftover in sorted(set(re.findall(r"\\(keyterm)\b", text))):
        problems.append(rf"\{leftover} survived expansion")

    return text, problems
