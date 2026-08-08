"""Build both corpora and the figure manifest.

Two corpora, because the LaTeX comments are not noise:
  corpus_public.md  comments stripped - safe to serve to anyone
  corpus_draft.md   comments kept as draft notes - for Jamie and viva prep only

The results chapter's comments contain notebook provenance for individual numbers,
inline TODOs, and at least one unresolved discrepancy addressed to a supervisor. Serving
those to an external reader would leak the thesis's open problems; hiding them from
yourself would waste the most useful thing in the repo.
"""
from __future__ import annotations

import json
import sys

from .. import config
from .figures import build_manifest
from .flatten import flatten, vocabulary_block

HEADER = """# Thesis corpus - {mode}

Flattened from the LaTeX source of `{source}`. Section numbers match the thesis and
are cited as SN.N. `[FIGURE label]` / `[TABLE label]` mark where floats sit; figures are
retrievable by label with the get_figure tool. Cross-references appear as `Figure [label]`.
{note}
---

"""

NOTE_DRAFT = """
Lines beginning `> [draft note]` are LaTeX source comments: provenance for specific
numbers, outstanding TODOs, and open questions. They are drafting state, not findings.
"""


def main() -> int:
    try:
        repo = config.thesis_repo()
        source = config.source_label()
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    OUT = config.build_dir(create=True)

    public = flatten(repo, mode="public")
    draft = flatten(repo, mode="draft")
    vocab = vocabulary_block(public.vocab)

    for mode, c, note in (("public", public, ""), ("draft", draft, NOTE_DRAFT)):
        (OUT / f"corpus_{mode}.md").write_text(
            HEADER.format(mode=mode, note=note, source=source) + vocab + "\n---\n\n" + c.text,
            encoding="utf-8"
        )

    (OUT / "labels.json").write_text(json.dumps(public.labels, indent=2))
    (OUT / "annotations.json").write_text(json.dumps(draft.annotations, indent=2))
    manifest = build_manifest(repo, OUT, public.labels)

    for mode, c in (("public", public), ("draft", draft)):
        w = len((vocab + c.text).split())
        print(f"corpus_{mode}.md  {w:,} words  ~{int(w * 1.4):,} tokens")
    ok = sum(1 for e in manifest for a in e["assets"] if a["status"] == "ok")
    pend = sum(1 for e in manifest for a in e["assets"] if a["status"] == "pending")
    miss = [e["label"] for e in manifest for a in e["assets"] if a["status"] == "missing"]
    amb = [e["label"] for e in manifest for a in e["assets"] if a["status"] == "ambiguous"]
    print(f"figures       {len(manifest)} blocks | {ok} resolved | {pend} pending (commented out)")
    print(f"labels        {len(public.labels)}   draft notes {len(draft.annotations)}")

    if miss:
        print(f"\nMISSING assets in live figure blocks - these will 404 at runtime: {miss}")
    if amb:
        print(f"AMBIGUOUS filenames (breaks the unique-figure-name convention): {amb}")

    bad = sorted(set(public.unresolved))
    if bad:
        print(f"\n{len(bad)} unresolved macro/key markers:")
        for u in bad[:30]:
            print("  ", u)
    return 1 if (bad or miss or amb) else 0


if __name__ == "__main__":
    raise SystemExit(main())
