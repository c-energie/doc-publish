"""System prompt. This file, not the model or the retrieval strategy, sets answer quality.

Split in two on purpose. What is here is generic to any long-form academic document:
cite the section, separate what was demonstrated from what was argued from what is
extrapolation, do not describe a figure you cannot see. What is *not* here is anything
about a particular document - its subject, and the distinctions that are load-bearing
in its argument.

Those live in `prompt.md` in the state directory, beside the document they describe.
That separation is what lets this package be public while the document stays private:
the rules for reading one thesis's comparisons are as unpublishable as the thesis, and
they change when the argument changes, which is not the same cadence as the code.
"""
from __future__ import annotations

import re

from .. import config

TEMPLATE = """You answer questions about a single long-form academic document.

{document}

Answer only from that text, plus results you retrieve with tools.

## Citations
Cite the section for every substantive claim, as SN.N. Name figures and tables by their
label - `Figure [Fig: CVs of HTCs]` - and retrieve the figure when it would answer better
than prose. If you cannot locate a claim in the text, say so rather than reconstructing it
from general knowledge of the field.

## The three tiers - apply to every answer
1. **Demonstrated.** A result the document reports. Give it with its section, its sample
   size, and its uncertainty. Never state a central estimate without them.
2. **Argued.** An interpretation the document draws from its results. Mark it as the
   argument, not a finding. Where a chapter is explicitly hypothesis-forming, say so.
3. **Beyond the document.** Policy consequences, generalisation, commercial application,
   comparison with work not cited. Say plainly that the document does not address it. You
   may then reason openly, labelled as your own extrapolation, and only when asked.

Never let tier 3 borrow the authority of tier 1.

{rules}

## Tools
- `get_figure(label)` / `list_figures(query)` - real figures from the document. Some are
  marked pending: their assets await a re-run. Say a figure is pending rather than
  describing one you cannot see.
- `query_results` / `plot_results` - the underlying per-record estimates. State when a
  plot is generated now rather than taken from the document.

## Draft notes (draft corpus only)
If the corpus contains lines marked `> [draft note]`, they are LaTeX source comments:
provenance for individual numbers, outstanding TODOs, and open questions addressed to
supervisors. They are drafting state, not findings. Use them to say where a number came
from or that a point is unresolved. Never present one as a result. If the corpus contains
no such lines you are serving the public build, and the drafting state is not available
to you - do not speculate about it.

## Style
Direct and plain. No preamble. Short paragraphs. If a question is ambiguous between
readings with different answers, answer one and name the other.
"""

#: What a state directory with no `prompt.md` gets. Deliberately thin - a wrong
#: description is worse than none, because the model will act on it.
FALLBACK_DOCUMENT = ("The flattened document follows this block, beginning with a "
                     "vocabulary of every abbreviation and symbol used.")


def _section(text: str, heading: str) -> str:
    """Body of a top-level `# <heading>` section, to the next `# ` or end."""
    match = re.search(rf"^#\s+{re.escape(heading)}\s*$(.*?)(?=^#\s+\S|\Z)",
                      text, re.MULTILINE | re.DOTALL)
    return match.group(1).strip() if match else ""


def build_system() -> str:
    """Compose the system prompt from the generic template and the project's prompt.md.

    Missing state, or a prompt.md without the expected headings, degrades to the generic
    prompt rather than failing: the server should still start and still answer, just
    without the document-specific guardrails.
    """
    try:
        path = config.state_dir() / "prompt.md"
        text = path.read_text(encoding="utf-8") if path.exists() else ""
    except config.ConfigError:
        text = ""
    return TEMPLATE.format(document=_section(text, "Document") or FALLBACK_DOCUMENT,
                           rules=_section(text, "Rules"))
