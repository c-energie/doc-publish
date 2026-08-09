"""Fragment tree -> Quarto .qmd pages.

One .qmd per planned page (the same plan the Notion stream uses - structure.build_plan
with the pinned placements from the Notion manifest, so both streams keep identical
page boundaries). Files land in site/ (gitignored); everything that keeps filenames
and anchors stable across builds lives in anchor_map.json in the state dir (committed).

Anchors: every referenceable label maps to {page, anchor}. Slugs are deterministic
("Fig: CVs of HTCs" -> "fig-cvs-of-htcs") and asserted injective - a collision fails
the build rather than silently landing two labels on one anchor.

Cross-references reuse emit.XREF_RE / emit.CITE_RE (their comments record real bugs
already fixed: the kind word owning its trailing space, `[TODO]` staying literal).
The resolver mirrors the contract of the linker's `resolve(kind, label)`: same-page
floats become native @fig-/@tbl- references, everything else a *relative* link
(`page.html#anchor`), so the site works from a folder and from a zip.

Maths: display equations pass through as parsed (`Fragment.raw`, math="mathjax").
The single mutation applied at write time is escaping bare `%` inside maths - `%`
starts a comment in MathJax exactly as in KaTeX, so `$\\pm10%$` would silently eat
the rest of the expression; escaping it is a TeX-source correction, not a rewrite.
Commands MathJax cannot know (physics-package macros, the corpus's `\\deltaT`) are
reported; site.py ships `\\newcommand` shims for the known ones via a hidden block
included before every page body.

Citations stay plain text in this stream (the Notion stream links them to its
References database; a bib-backed references page here is future work) - the count
is reported so nothing disappears unremarked.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..emit import CITE_RE, INLINE_MATH_RE, XREF_RE, Fragment, Section
from ..structure import Plan

# physics-package macros (and corpus oddities) that need \newcommand shims to render
SHIM_MACROS = {"deltaT": r"\delta T"}
PHYSICS_MACROS = {"dv", "pdv", "abs", "norm", "qty", "vb", "va", "vu", "dd", "ev",
                  "mel", "braket", "bra", "ket", "expval", "comm", "acomm", "pb"}


def slugify(s: str) -> str:
    s = s.lower().replace("\u00a7", "sec ")
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", s)).strip("-")


def float_slug(kind: str, label: str) -> str:
    """Quarto's native crossref prefixes: figures #fig-, tables #tbl-."""
    bare = re.sub(r"^(?:fig|tab)\s*:\s*", "", label, flags=re.I)
    return ("fig-" if kind == "figure" else "tbl-") + slugify(bare)


def page_file(page_key: str) -> str:
    return (slugify(page_key) if page_key[0] == "\u00a7" else slugify(page_key)) + ".qmd"


def display_name(label: str, strip_prefix: bool) -> str:
    """Float references read by name, matching the Notion stream's convention:
    'Figure [Fig: x]' must not render 'Figure Fig: x'."""
    return re.sub(r"^(?:Fig|Tab)\s*:\s*", "", label, flags=re.I) if strip_prefix else label


def escape_math_pct(expr: str) -> str:
    return re.sub(r"(?<!\\)%", r"\\%", expr)


def unknown_math_commands(expr: str, known: set[str]) -> set[str]:
    return {m.group(1) for m in re.finditer(r"\\([a-zA-Z]+)", expr)
            if m.group(1) not in known}


class AnchorMap:
    """label -> {page: 'x.html', anchor: 'fig-...'} with injectivity enforced.

    Injectivity applies to *owners* - the labels whose slug generates the anchor
    (section keys, float labels). A LaTeX label aliasing its section's anchor
    ('Ch:Intro' -> the same target as '§1') is expected, not a collision.
    """

    def __init__(self):
        self.map: dict[str, dict] = {}
        self._owner: dict[tuple[str, str], str] = {}

    def add(self, label: str, page_html: str, anchor: str, alias: bool = False) -> None:
        key = (page_html, anchor)
        if not alias:
            if key in self._owner and self._owner[key] != label:
                raise SystemExit(f"anchor collision: '{label}' and '{self._owner[key]}' "
                                 f"both slugify to {page_html}#{anchor}")
            self._owner[key] = label
        self.map[label] = {"page": page_html, "anchor": anchor}

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.map, indent=2, ensure_ascii=False,
                                   sort_keys=True), encoding="utf-8")


class Resolver:
    """Same contract as the linker's resolve(kind, label), for markdown output.

    resolve(label, current_page, kind_word) ->
      None                          unknown label: the span stays literal
      ("native", "@fig-slug")      same-page float, Quarto renders "Figure N"
      ("link", display, href)      relative link, cross-page or same-page section
    """

    def __init__(self, anchors: AnchorMap, float_kinds: dict[str, str],
                 section_of: dict[str, str],
                 citation_hrefs: dict[str, str] | None = None):
        self.anchors = anchors
        self.float_kinds = float_kinds       # label -> "figure" | "table"
        self.section_of = section_of         # labels.json: label -> "\u00a74.2"
        self.citation_hrefs = citation_hrefs or {}   # "Bauwens et al. 2020" -> href
        self.unresolved: list[str] = []
        self.citations_linked = 0
        self.citations_plain = 0

    def resolve(self, label: str, current_page: str, kind_word: str):
        entry = self.anchors.map.get(label)
        if entry is None:
            return None
        same_page = entry["page"] == current_page
        if label in self.float_kinds and same_page:
            return ("native", "@" + entry["anchor"])
        # empty anchor = the target is a whole page; never emit a dangling '#'
        frag = f"#{entry['anchor']}" if entry["anchor"] else ""
        href = frag if (same_page and frag) else entry["page"] + frag
        if label in self.float_kinds:
            # floats read by name, matching the Notion stream
            display = display_name(label, strip_prefix=bool(kind_word))
        elif label.startswith("\u00a7"):
            display = label                  # [\u00a75.1.3] in signposts
        else:
            # section labels display as their section number, like the Notion stream
            display = self.section_of.get(label, label)
        return ("link", display, href)


def md_text(text: str, resolver: Resolver, current_page: str, section: str,
            math_warnings: list, known_math: set[str]) -> str:
    """Fragment text -> markdown, resolving refs and preserving inline maths."""
    out: list[str] = []

    def plain(s: str) -> None:
        # citations first, then cross-references. Each author-year component links
        # separately to its references-page entry, "(A 2020; B 2021)" = two links,
        # matching the Notion stream; unmatched components stay literal (counted).
        pos = 0
        for m in CITE_RE.finditer(s):
            refs(s[pos:m.start()])
            out.append("(")
            for j, comp in enumerate(m.group(1).split("; ")):
                if j:
                    out.append("; ")
                href = resolver.citation_hrefs.get(comp.strip())
                if href:
                    resolver.citations_linked += 1
                    out.append(f"[{comp}]({href})")
                else:
                    resolver.citations_plain += 1
                    out.append(comp)
            out.append(")")
            pos = m.end()
        refs(s[pos:])

    def refs(s: str) -> None:
        pos = 0
        for m in XREF_RE.finditer(s):
            kind_word, label = (m.group(1) or "").strip(), m.group(2).strip()
            target = resolver.resolve(label, current_page, kind_word)
            if target is None and "," in label:
                # \cref{a,b} flattens to one bracket span "[a,b]"; if every part
                # resolves, link them all ("Figure [a] and [b]")
                parts = [resolver.resolve(p.strip(), current_page, kind_word)
                         for p in label.split(",")]
                if all(p is not None for p in parts):
                    out.append(s[pos:m.start()])
                    if kind_word:
                        out.append(f"{kind_word} ")
                    rendered = [t[1] if t[0] == "native" else f"[{t[1]}]({t[2]})"
                                for t in parts]
                    out.append(" and ".join(rendered))
                    pos = m.end()
                    continue
            if target is None:
                if kind_word:
                    # "Figure [x]" with an unknown x is a real dangling reference;
                    # a bare [TODO]/[W K^-1] staying literal is not
                    resolver.unresolved.append(f"{section}: {kind_word} [{label}]")
                continue
            out.append(s[pos:m.start()])
            if target[0] == "native":
                out.append(target[1])         # kind word absorbed: Quarto prints it
            else:
                _, display, href = target
                if kind_word:
                    out.append(f"{kind_word} ")
                out.append(f"[{display}]({href})")
            pos = m.end()
        out.append(s[pos:])

    pos = 0
    for m in INLINE_MATH_RE.finditer(text):
        plain(text[pos:m.start()])
        expr = m.group(1)
        bad = unknown_math_commands(expr, known_math)
        if bad:
            math_warnings.append((section, expr[:80], sorted(bad)))
        out.append("$" + escape_math_pct(expr) + "$")
        pos = m.end()
    plain(text[pos:])
    return "".join(out)


class PageWriter:
    def __init__(self, plan: Plan, resolver: Resolver, figures, signposts_by_anchor,
                 known_math: set[str]):
        self.plan = plan
        self.resolver = resolver
        self.figures = figures               # quarto.figures.FigureEmitter
        self.signposts = signposts_by_anchor  # (section-key, start|end) -> [texts]
        self.known_math = known_math
        self.math_warnings: list = []
        self.equations = 0

    def _md(self, text: str, page_html: str, section: str) -> str:
        return md_text(text, self.resolver, page_html, section,
                       self.math_warnings, self.known_math)

    def _signposts(self, key: str, position: str, page_html: str) -> list[str]:
        blocks = []
        for text in self.signposts.get((key, position), []):
            body = self._md(text, page_html, key)
            blocks.append(f'::: {{.callout-note appearance="simple" icon=false}}\n'
                          f"\U0001f9ed {body}\n:::\n")
        return blocks

    def render_page(self, page_key: str) -> str:
        node = self.plan.nodes[page_key]
        page_html = page_file(page_key).replace(".qmd", ".html")
        parts = ["---", f'title: "{node.section.display}"', "---", ""]

        def emit(n, depth: int) -> None:
            key = "\u00a7" + n.section.number if n.section.number[0].isdigit() \
                else n.section.number
            sec_name = n.section.display
            if depth > 0:
                level = "#" * min(depth + 1, 6)
                anchor = slugify(key)
                parts.append(f"{level} {n.section.display} {{#{anchor}}}\n")
            parts.extend(self._signposts(key, "start", page_html))
            for frag in n.section.fragments:
                parts.append(self._fragment(frag, page_html, sec_name))
            for ch in n.children:
                if ch.placement == "inline":
                    emit(ch, depth + 1)
            parts.extend(self._signposts(key, "end", page_html))

        emit(node, 0)
        return "\n".join(p for p in parts if p is not None) + "\n"

    def _fragment(self, frag: Fragment, page_html: str, section: str) -> str | None:
        if frag.kind == "paragraph":
            return self._md(frag.text, page_html, section) + "\n"
        if frag.kind == "bullet":
            return "- " + self._md(frag.text, page_html, section)
        if frag.kind == "heading":
            return f"\n{'#' * (frag.level + 1)} {frag.text}\n"
        if frag.kind == "equation":
            self.equations += 1
            expr = escape_math_pct(frag.raw or frag.text)
            bad = unknown_math_commands(expr, self.known_math)
            if bad:
                self.math_warnings.append((section, expr[:80], sorted(bad)))
            return f"$$\n{expr}\n$$\n"
        if frag.kind == "figure":
            caption = self._md(frag.caption, page_html, section) if frag.caption else ""
            return self.figures.emit(frag.label, caption)
        if frag.kind == "table":
            return self._table(frag, page_html, section)
        return None

    def _table(self, frag: Fragment, page_html: str, section: str) -> str:
        if not frag.rows:
            return ""
        slug = float_slug("table", frag.label)
        rows = [[self._md(c, page_html, section).replace("|", "\\|").replace("\n", " ")
                 for c in row] for row in frag.rows]
        width = len(rows[0])
        lines = ["| " + " | ".join(rows[0]) + " |",
                 "|" + "---|" * width]
        for row in rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
        caption = self._md(frag.caption, page_html, section) if frag.caption \
            else display_name(frag.label, True)
        lines.append(f"\n: {caption} {{#{slug}}}\n")
        return "\n".join(lines)


def build_anchor_map(plan: Plan, labels: dict[str, str], root: Section
                     ) -> tuple[AnchorMap, dict[str, str]]:
    """All referenceable labels -> (page, anchor).

    - section keys ("§4.3.2") and section labels from labels.json anchor at the
      section's heading (or page top when the section is a page);
    - float labels anchor at their #fig-/#tbl- id, on the page that owns them.
    labels.json only records labels near headings - float labels never reach it
    (ingest strips \\label inside float environments), so floats come from the
    parsed tree, exactly as the Notion stream does it.
    """
    anchors = AnchorMap()
    float_kinds: dict[str, str] = {}

    for key, node in plan.nodes.items():
        html = page_file(node.page_key).replace(".qmd", ".html")
        anchor = "" if node.placement == "page" else slugify(key)
        anchors.add(key, html, anchor)

    for label, sec_key in labels.items():
        node = plan.nodes.get(sec_key)
        if node is None:
            continue
        entry = anchors.map[sec_key]
        anchors.add(label, entry["page"], entry["anchor"], alias=True)

    for s in root.walk():
        if not s.number or not s.number[0].isdigit():
            continue
        node = plan.nodes.get("\u00a7" + s.number)
        if node is None:
            continue
        html = page_file(node.page_key).replace(".qmd", ".html")
        for f in s.fragments:
            if f.kind in ("figure", "table") and f.label:
                anchors.add(f.label, html, float_slug(f.kind, f.label))
                float_kinds[f.label] = f.kind
    return anchors, float_kinds
