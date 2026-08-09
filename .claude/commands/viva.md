---
description: Find what the document says on a topic, with citations and open questions
argument-hint: <topic, e.g. "model selection" or "the calibration comparison">
---

Viva-prep lookup on: $ARGUMENTS

1. `grep -n` the topic and its likely synonyms in `build/corpus_draft.md`. Try the
   abbreviation and the long form both - the corpus uses short forms in prose and defines
   every one of them in the vocabulary block at the top, so read that block first if the
   topic is unfamiliar.
2. Read each hit with surrounding context, and note the nearest `## §N.N` heading above it.
3. Answer in three parts, clearly separated:
   - **What the document demonstrates** - results with section, n, and uncertainty.
   - **What it argues** - interpretations, marked as such.
   - **What is unresolved** - any `> [draft note]` in those sections: TODOs, withheld
     figures or tables, provenance gaps, supervisor questions.
4. End with the two or three questions an examiner would most plausibly ask about this,
   and where the answer would have to come from.

Check `$DOC_REPO/.doc-publish/prompt.md` before answering: it records which of this
document's comparisons establish what, and which numbers are contested. Those
distinctions are the ones an examiner presses on, and they cannot be inferred from the
corpus alone.

Do not fill gaps from general knowledge of the field. An absence is the finding.