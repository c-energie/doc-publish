---
description: List every unresolved item in the document draft, by section
argument-hint: [optional: section number or section name to filter]
---

Read `build/annotations.json` - every LaTeX source comment with the section it sits in.

Filter to: $ARGUMENTS (if empty, report everything).

Group the output by section number and classify each note:

- **Blocked on a notebook run** - withheld figures and tables, backlog items
- **Unresolved discrepancy** - two numbers that disagree, or a question to a supervisor
- **Provenance only** - records which notebook produced a number; no action needed
- **Writing TODO** - prose to add or reconcile

Lead with the discrepancies. Those are the ones that cost marks in a viva, and they are
the only category where the document currently contradicts itself.

Finish with a count per category and the three sections carrying the most open items.
