"""Inline signposts: wayfinding pointers published at section starts and ends.

A signpost is a short navigational note - "to skip ahead to the results this method
produces, see §5.1", "the figure showing these assumptions is Fig X" - rendered as a
compact 🧭 callout at the start or end of the section it is anchored to (including
sections that live inside toggles). Redesigned 2026-08-04 from per-top-level intros to
per-section pointers.

File format (signposts.md in the state dir, committed):

    ## §4.3.4 end
    status: draft
    title: The model family
    To skip ahead to the results these forms produce, see [§5.1.3]. The model
    shapes at a glance: [Fig: model_examples].

- The heading is `## <section-key> <start|end>`; the same anchor may appear more
  than once (each entry becomes its own callout, in file order).
- `[...]` spans resolve to live links: figure/table/equation labels from
  labels.json, or section numbers like [§5.1.3]. Unresolvable spans stay literal.
- Only `status: approved` entries publish. Drafting and editing entries is the
  signpost-editor subagent's job (navigational, never evaluative); this module only
  parses the file and hands approved entries to the linker.
- Entries persist across syncs - nothing is regenerated. `title:` records the
  section title at drafting time; if the section has since been renamed or removed
  the entry is held back and reported instead of published against content that
  may no longer say what it said (sync.py does this check). Re-drafting is an
  explicit ask to the signpost-editor subagent, which only ever rewrites drafts.
"""
from __future__ import annotations

import re
from pathlib import Path

HEADER = """# Signposts

Wayfinding pointers published into the wiki at the anchor each entry names.

- Entry heading: `## <section> <start|end>` (e.g. `## §4.3.4 end`). The section may
  be any §N.N from the corpus - anchors inside toggles work too.
- `status: draft` entries are NOT published; review the text, then change to
  `status: approved`.
- `[...]` spans become links: figure/table labels from build/labels.json, or
  section numbers like [§5.1.3].
- Drafts are written by the signpost-editor subagent; it never approves its own
  entries and never edits approved ones.
"""

ENTRY_RE = re.compile(
    r"^## (\S+) (start|end)\n+status: (draft|approved)\n(?:title: (.*?)\n)?(.*?)(?=\n## |\Z)",
    re.S | re.M)


def load(path: Path) -> list[dict]:
    """All entries, in file order: {section, position, status, title, text}."""
    if not path.exists():
        return []
    return [{"section": m.group(1), "position": m.group(2), "status": m.group(3),
             "title": (m.group(4) or "").strip(),
             "text": re.sub(r"\s+", " ", m.group(5)).strip()}
            for m in ENTRY_RE.finditer(path.read_text(encoding="utf-8"))]


def approved(path: Path, current_titles: dict[str, str],
             report: list[str]) -> dict[tuple[str, str], list[str]]:
    """(section-key, position) -> texts for publication, file order preserved.

    An approved entry publishes only while its anchor still holds: the section
    must exist, and if the entry recorded the section title at drafting time, the
    title must be unchanged - a renamed section may no longer say what the pointer
    assumes, so the entry is held back and reported for re-approval instead.
    """
    out: dict[tuple[str, str], list[str]] = {}
    for e in load(path):
        if e["status"] != "approved" or not e["text"]:
            continue
        if e["section"] not in current_titles:
            report.append(f"signpost for {e['section']} {e['position']}: section no "
                          f"longer in the corpus - held back, review the entry")
            continue
        if e["title"] and e["title"] != current_titles[e["section"]]:
            report.append(f"signpost for {e['section']} {e['position']}: section "
                          f"title changed ('{e['title']}' -> "
                          f"'{current_titles[e['section']]}') - held back, re-approve "
                          f"after checking the pointer still holds")
            continue
        out.setdefault((e["section"], e["position"]), []).append(e["text"])
    return out


def ensure_scaffold(path: Path) -> bool:
    """Create the file with instructions if it does not exist (or holds the retired
    top-level-intro format). Returns True if (re)written."""
    if path.exists() and ("start|end" in path.read_text(encoding="utf-8")
                          or ENTRY_RE.search(path.read_text(encoding="utf-8"))):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(HEADER, encoding="utf-8")
    return True
