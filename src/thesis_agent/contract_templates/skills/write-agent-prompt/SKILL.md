---
name: write-agent-prompt
description: Author or revise .thesis-agent/prompt.md — the rules that stop a publishing agent from misrepresenting this document. Use when setting up an agent for a document, when `thesis-agent check` reports TODO markers in prompt.md, or when a reader has been given a wrong or overconfident answer about the work.
---

# Writing the agent prompt

`.thesis-agent/prompt.md` is the only file in the contract that cannot be generated. It is
prepended to the flattened document, and it decides whether an agent answering questions
represents the work honestly or confidently misstates it **in the author's name**.

That is the failure to design against. A publishing agent that loses a citation is
annoying; one that promotes a comparison into a validation has told a reader something the
author would never say.

## How to work

Interview, then draft. Do not write the prompt from the document alone — the distinctions
that matter most are usually the ones the author has internalised and therefore left
implicit.

### 1. Read before asking

Skim the document's own claims first, so the questions are specific:

- The abstract and conclusions: what is claimed, and how strongly.
- Any section comparing two methods, or validating one against another.
- Anywhere hedged language clusters (`suggests`, `indicative`, `may`) — that hedging is
  usually load-bearing and an agent will flatten it.

### 2. Ask about what the evidence does *not* establish

This is the section that matters. One bullet per distinction a careful reader of the field
would insist on. Useful questions:

- Is anything here a *benchmark* rather than a ground truth? What is the right word for
  agreement with it — never "validation" or "accuracy" if it is itself uncertain.
- Do any two quantities being compared actually *measure different things*? If so, what is
  the expected ordering, so a difference is not read as an error?
- What is the cohort, and what would be over-generalising from it?
- Which results are exploratory, and which confirmatory?

Write each as: **the thing often said**, then what should be said instead. Vague guidance
("be careful about causality") does nothing; name the specific claim.

### 3. Ask about numbers

- If a value appears in more than one place and they disagree, **which one is canonical**?
  An agent will otherwise faithfully report whichever it finds first. If the answer is
  "I don't know yet", say so in the prompt — an agent that flags the inconsistency is far
  better than one that picks.
- Which figures or tables are authoritative for which quantity?

### 4. Ask about vocabulary

Terms whose meaning here differs from the everyday or field-standard one, and terms that
must not be paraphrased. Acronyms already in `glossary_terms.tex` are resolved by the
flattener — do not repeat them.

### 5. Draft, then test

Write the file, then check it the only way that works: ask the agent three questions you
already know the answers to — one factual, one about a limitation, one about something the
document does not cover. The third is the real test. An agent that answers it anyway needs
a firmer "say the document does not address this" rule.

## Shape

Keep the skeleton's headings; they are what the engine expects.

```markdown
# Document
One paragraph: what this is, who wrote it, what field.

# Rules
## What the evidence does and does not establish
- **X is a benchmark, not ground truth.** Agreement is *method agreement*, never
  validation and never accuracy.
- **Y and Z measure different quantities.** A value above Y is the expected ordering,
  not an error.

## Numbers
Where to read each quantity from; which source wins when two disagree.

## Vocabulary
Terms that must not be paraphrased.

## Answering
House style: cite sections, quote where wording carries the argument, and say
"the document does not address this" rather than reasoning past it.
```

## Pitfalls

- **Do not restate the document.** The document is already in the context; the prompt is
  for what the document does not say about itself.
- **Do not write aspirationally.** If a limitation is real, state it — a prompt describing
  the work you wish you had done produces an agent that oversells it.
- **Revisit after a substantive revision.** A prompt written against chapter three's old
  argument silently misdescribes the new one.
