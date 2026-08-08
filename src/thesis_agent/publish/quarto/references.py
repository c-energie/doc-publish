"""Bib-backed references page for the Quarto site.

Reuses databases.bib_entries deliberately, despite that module belonging to the
Notion stream: it is pure bib parsing with no Notion coupling, and both streams
must construct the *identical* author-year label ("Bauwens et al. 2020") or the
in-text citations flatten writes into the corpus would not match their entries.

Each entry renders inside a div anchored by its BibTeX key (`#ref-<slug>`), and
citation_hrefs() hands the writer a label -> "references.html#ref-..." map. Two
entries can share an author-year label (same surname, same year); the first wins
and the collision count is reported, exactly like the Notion database stream.
"""
from __future__ import annotations

from pathlib import Path

from ..databases import bib_entries  # noqa: F401  (re-exported for build_site)
from .qmd import slugify


def ref_anchor(key: str) -> str:
    return "ref-" + slugify(key)


def citation_hrefs(entries: list[dict], report: list[str]) -> dict[str, str]:
    hrefs: dict[str, str] = {}
    collisions = 0
    for e in entries:
        if e["label"] in hrefs:
            collisions += 1
            continue
        hrefs[e["label"]] = f"references.html#{ref_anchor(e['key'])}"
    if collisions:
        report.append(f"references: {collisions} author-year labels are ambiguous - "
                      f"citations link the first matching entry")
    return hrefs


def write_page(entries: list[dict], site_dir: Path) -> None:
    lines = ["---", 'title: "References"', "---", "",
             "Built from the thesis bib files; in-text citations across the site "
             "link here. The LaTeX bibliography is the source of truth.", ""]
    seen: set[str] = set()
    for e in sorted(entries, key=lambda e: (e["label"].lower(), e["key"])):
        if e["key"] in seen:
            continue
        seen.add(e["key"])
        body = [f"**{e['label']}**"]
        if e["title"]:
            body.append(f" — {e['title']}.")
        if e["venue"]:
            body.append(f" *{e['venue']}.*")
        if e["doi"]:
            body.append(f" [DOI]({e['doi']})")
        if e["url"] and not e["doi"]:
            body.append(f" [link]({e['url']})")
        lines.append(f"::: {{#{ref_anchor(e['key'])}}}")
        lines.append("".join(body))
        lines.append(":::")
        lines.append("")
    (site_dir / "references.qmd").write_text("\n".join(lines), encoding="utf-8")
