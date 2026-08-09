"""Open-questions callouts from build/annotations.json.

Draft notes are the one channel through which drafting state reaches the wiki, and only
after filtering: substantive items (discrepancies, withheld figures/tables, supervisor
questions, unresolved TODOs) become an "Open questions" callout at the foot of the
relevant page; pure provenance ("values from notebook X, result group N") stays out.

Where a note matches an entry in the Notion open-questions database, the callout links
it - resolving the question there clears the callout on the next sync, because only
Open-status entries are fetched.
"""
from __future__ import annotations

import re

# substantive: any of these signals means the note is a live problem, not provenance
SUBSTANTIVE_RE = re.compile(
    r"\?|TODO|FIXME|unresolved|inconsisten|discrepan|withheld|awaiting|pending|"
    r"supervisor|decide|confirm whether|check whether|open question|REF NEEDED|"
    r"commented out|not yet|needs? (?:a |re-)?run|placeholder",
    re.I,
)
# provenance: only excluded when nothing substantive fires
PROVENANCE_RE = re.compile(
    r"^(?:from|source|values? (?:from|read)|generated|produced|figure produced|"
    r"notebook|result group|numbers? (?:from|come)|see |data from)",
    re.I,
)

_WORD_RE = re.compile(r"[a-z0-9]{3,}")
_STOP = {"the", "and", "for", "with", "that", "this", "from", "which", "are", "was",
         "not", "has", "have", "been", "its", "each", "per", "into"}


def substantive(annotations: list[dict]) -> list[dict]:
    keep = []
    for a in annotations:
        note = a["note"].strip()
        if SUBSTANTIVE_RE.search(note):
            keep.append(a)
        elif PROVENANCE_RE.match(note):
            continue
        # anything neither clearly substantive nor clearly provenance stays out too:
        # the wiki is public-facing, so the filter fails closed
    return keep


def _tokens(s: str) -> set[str]:
    return {t for t in _WORD_RE.findall(s.lower()) if t not in _STOP}


def match_open_question(note: str, questions: list[dict]) -> dict | None:
    """Conservative fuzzy match: at least 60% of the question title's content words
    must appear in the note. A wrong link misdirects a reader; no link is just quieter."""
    best, best_score = None, 0.0
    note_tokens = _tokens(note)
    for q in questions:
        q_tokens = _tokens(q["title"])
        if not q_tokens:
            continue
        score = len(q_tokens & note_tokens) / len(q_tokens)
        if score > best_score:
            best, best_score = q, score
    return best if best_score >= 0.6 else None


def fetch_open_questions(notion, database_id: str) -> list[dict]:
    """Open-status rows only - a Resolved question must clear its callout link."""
    rows = notion.query_database(
        database_id, {"property": "Status", "select": {"equals": "Open"}}
    )
    out = []
    for r in rows:
        title_prop = r["properties"].get("Question", {}).get("title", [])
        title = "".join(t.get("plain_text", "") for t in title_prop)
        if title:
            out.append({"title": title, "url": r.get("url", "")})
    return out


def callout_blocks(notes: list[dict], questions: list[dict], rich_text) -> list[dict]:
    """One callout listing this page's open items. `rich_text` is emit.rich_text
    partially applied by the linker (so refs inside notes resolve too)."""
    if not notes:
        return []
    children = []
    for a in notes:
        runs = rich_text(a["note"])
        q = match_open_question(a["note"], questions)
        if q and q.get("url"):
            runs += [{"type": "text", "text": {"content": "  → "}},
                     {"type": "text", "text": {"content": "tracked in Open questions",
                                               "link": {"url": q["url"]}}}]
        children.append({"type": "bulleted_list_item",
                         "bulleted_list_item": {"rich_text": runs}})
    return [{
        "type": "callout",
        "callout": {
            "icon": {"type": "emoji", "emoji": "❓"},
            "color": "yellow_background",
            "rich_text": [{"type": "text", "text": {"content": "Open questions"},
                           "annotations": {"bold": True}}],
            "children": children[:99],
        },
    }]
