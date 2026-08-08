"""Cross-reference resolution and page assembly.

Two-pass contract (driven by sync.py): pass one ensures every page and database
exists and records IDs in the manifest; only then does this module render content, so
every cross-reference and citation can resolve to a live URL.

Link granularity: references resolve to the *page* a section lives on (its own page,
or the page it is inlined into). Block-level anchors are deliberately not used - a
page's block IDs change every time its content is replaced, so anchor links would make
every sync dirty every other page and the idempotency guarantee (second run = zero
writes) impossible. Notion opens a page at the top for a stale anchor anyway.
Citations resolve per component: each "Author Year" inside a parenthetical links to
its own row in the References database (falling back to the database itself when the
label is ambiguous or unmatched).

Page anatomy (agreed with Jamie, 2026-08-04):
  signpost callout (approved only) -> table-of-contents block (pages with headings) ->
  own content -> per top-level inlined section: divider + open heading + content ->
  deeper sections as collapsed toggle headings -> "Open questions" callout -> footer.
  Figures and tables render as card callouts; consecutive figures sit in columns
  (max 3 across). Toggle/card children that exceed the API's nesting-per-request limit
  travel via `_deferred` and notion.append_tree.

Figures: caption + static image (external URL) + "Open interactive figure ↗" link.
URL convention: {FIGURES_BASE_URL}/{asset filename} for the static image and
{FIGURES_BASE_URL}/{asset stem}.html for the interactive version. Until the figures
repo exists (FIGURES_BASE_URL unset) a neutral placeholder card is emitted instead -
inventing URLs would publish dead links with real figure labels on them.
"""
from __future__ import annotations

from pathlib import Path

from .emit import Fragment, rich_text
from .structure import Plan

FOOTER = ("This page is generated from the LaTeX source of {source} by `thesis-agent "
          "sync`. The LaTeX is the source of truth - edits made here will be overwritten "
          "by the next sync.")


def footer_text() -> str:
    """Resolved late: the source name is a setting, and this text is published into
    every page, so a wrong value is visible on the whole wiki."""
    from .. import config
    return FOOTER.format(source=config.source_label())

MAX_FIGURE_COLUMNS = 3
# Thesis level at which inlined section headings become collapsed toggles.
# 2 = every inlined section (chapter pages open as outlines of §N.x toggles) -
# widened from 4 (subsubsections only) at Jamie's request, 2026-08-04.
TOGGLE_LEVEL = 2


def page_url(page_id: str) -> str:
    return "https://www.notion.so/" + page_id.replace("-", "")


class Linker:
    def __init__(self, plan: Plan, labels: dict[str, str], page_ids: dict[str, str],
                 figures: list[dict], base_url: str | None,
                 citation_urls: dict[str, str] | None = None,
                 refs_db_url: str | None = None,
                 float_labels: set[str] | None = None):
        self.float_labels = float_labels or set()
        self.plan = plan
        self.page_ids = page_ids                      # page_key -> notion page id
        self.citation_urls = citation_urls or {}
        self.refs_db_url = refs_db_url
        # a label can appear on both a live block and a commented-out one; the live
        # entry must win or a real figure would publish as "pending"
        self.figures: dict[str, dict] = {}
        for f in figures:
            if f["label"] not in self.figures or self.figures[f["label"]].get("commented_out"):
                self.figures[f["label"]] = f
        self.base_url = base_url.rstrip("/") if base_url else None
        self.katex_log: list[tuple[str, str, list[str]]] = []   # (section, expr, issues)
        self.report: list[str] = []
        self._section = "?"
        # labels.json values look like "§2.2.6"; keys are raw LaTeX labels
        self.label_to_key = {lab: sec for lab, sec in labels.items()}

    # -- resolver handed to emit.rich_text -----------------------------------
    def _resolve(self, kind: str, value: str):
        if kind == "citation":
            return self.citation_urls.get(value) or self.refs_db_url
        # signposts (and any text) may reference sections by number: [§5.1.3]
        sec_key = value if value.startswith("§") else self.label_to_key.get(value)
        if sec_key is None:
            return None                                # [TODO], [W K^-1], ... stay literal
        node = self.plan.nodes.get(sec_key)
        if node is None or node.page_key not in self.page_ids:
            self.report.append(f"{self._section}: reference to {value} ({sec_key}) "
                               f"has no published page - left as text")
            return None
        # float references read by name ("Fig: model_examples"), section
        # references by number ("§5.1.3")
        display = value if value in self.float_labels else sec_key
        return display, page_url(self.page_ids[node.page_key])

    def rich(self, text: str) -> list[dict]:
        log: list[tuple[str, list[str]]] = []
        runs = rich_text(text, resolve=self._resolve, katex_log=log)
        for expr, issues in log:
            self.katex_log.append((self._section, expr, issues))
        return runs

    # -- fragment -> blocks ---------------------------------------------------
    def blocks_for(self, frag: Fragment) -> list[dict]:
        if frag.kind == "paragraph":
            return [{"type": "paragraph", "paragraph": {"rich_text": self.rich(frag.text)}}]
        if frag.kind == "bullet":
            return [{"type": "bulleted_list_item",
                     "bulleted_list_item": {"rich_text": self.rich(frag.text)}}]
        if frag.kind == "heading":
            return [_heading(frag.level, self.rich(frag.text))]
        if frag.kind == "equation":
            if frag.issues:
                self.katex_log.append((self._section, frag.text, frag.issues))
            if frag.as_code:
                return [{"type": "code", "code": {
                    "language": "latex",
                    "rich_text": [{"type": "text", "text": {"content": frag.text[:2000]}}],
                    "caption": [{"type": "text", "text": {
                        "content": "Shown as source: this equation is unlikely to render "
                                   "in Notion (see katex_report.md)."}}],
                }}]
            return [{"type": "equation", "equation": {"expression": frag.text}}]
        if frag.kind == "table":
            return [self._table_card(frag)]
        if frag.kind == "figure":
            return [self._figure_card(frag)]
        return []

    def fragment_blocks(self, fragments: list[Fragment]) -> list[dict]:
        """Fragments -> blocks, grouping consecutive figures into columns."""
        blocks: list[dict] = []
        i = 0
        while i < len(fragments):
            if fragments[i].kind == "figure":
                group = []
                while (i < len(fragments) and fragments[i].kind == "figure"
                       and len(group) < MAX_FIGURE_COLUMNS):
                    group.append(self._figure_card(fragments[i]))
                    i += 1
                if len(group) >= 2:
                    blocks.append({"type": "column_list", "column_list": {"children": [
                        {"type": "column", "column": {"children": [card]}}
                        for card in group]}})
                else:
                    blocks += group
                continue
            blocks += self.blocks_for(fragments[i])
            i += 1
        return blocks

    def _table_card(self, frag: Fragment) -> dict:
        title = [{"type": "text", "text": {"content": f"Table {frag.label}"},
                  "annotations": {"bold": True}}]
        if frag.caption:
            title += [{"type": "text", "text": {"content": " — "}}] + self.rich(frag.caption)
        card = _callout("📋", "default", title)
        if frag.rows:
            width = len(frag.rows[0])
            # the table's rows are inline children of the table, which would sit three
            # levels deep inside the card's request - so the table is deferred
            card["_deferred"] = [{"type": "table", "table": {
                "table_width": width,
                "has_column_header": True,
                "has_row_header": False,
                "children": [{"type": "table_row", "table_row": {
                    "cells": [self.rich(c) if c else [] for c in row]}}
                    for row in frag.rows[:99]],
            }}]
        return card

    def _figure_card(self, frag: Fragment) -> dict:
        entry = self.figures.get(frag.label)
        title = [{"type": "text", "text": {"content": f"Figure {frag.label}"},
                  "annotations": {"bold": True}}]
        caption = self.rich(frag.caption) if frag.caption else []

        if entry is None:
            self.report.append(f"{self._section}: figure stub {frag.label} not in figures.json")
            return _callout("📊", "default", title + [{"type": "text", "text": {
                "content": " — no manifest entry; not rendered."}}],
                children=[{"type": "paragraph", "paragraph": {"rich_text": caption}}]
                if caption else None)

        bad = [a for a in entry["assets"] if a["status"] != "ok"]
        if bad:
            status = ", ".join(f"{a['name']} ({a['status']})" for a in bad)
            self.report.append(f"{self._section}: figure {frag.label} has non-live assets: {status}")
            body = caption + [{"type": "text", "text": {
                "content": f"  [asset {status} - awaiting a notebook re-run; not shown]"}}]
            return _callout("📊", "default", title,
                            children=[{"type": "paragraph", "paragraph": {"rich_text": body}}])

        if self.base_url is None:
            body = caption + [{"type": "text", "text": {
                "content": "  [figure hosting pending - image will appear once "
                           "FIGURES_BASE_URL is set]"}}]
            return _callout("📊", "default", title,
                            children=[{"type": "paragraph", "paragraph": {"rich_text": body}}])

        children: list[dict] = []
        for asset in entry["assets"]:
            name = Path(asset["path"]).name
            children.append({"type": "image", "image": {
                "type": "external", "external": {"url": f"{self.base_url}/{name}"}}})
        if caption:
            children.append({"type": "paragraph", "paragraph": {"rich_text": caption}})
        stem = Path(entry["assets"][0]["path"]).stem
        children.append({"type": "paragraph", "paragraph": {"rich_text": [
            {"type": "text", "text": {"content": "Open interactive figure ↗",
                                      "link": {"url": f"{self.base_url}/{stem}.html"}}}]}})
        card = _callout("📊", "default", title)
        card["_deferred"] = children
        return card

    # -- whole page -----------------------------------------------------------
    def render_page(self, page_key: str, signposts: dict[tuple[str, str], list[str]],
                    notes: list[dict], questions: list[dict],
                    body_override: list[dict] | None = None) -> list[dict]:
        """`signposts` maps (section-key, "start"|"end") -> pointer texts, published
        as compact 🧭 callouts at that anchor - including inside toggles."""
        from .callouts import callout_blocks
        node = self.plan.nodes.get(page_key)
        body: list[dict] = []

        def signs(n, position: str) -> list[dict]:
            key = "§" + n.section.number if n.section.number[0].isdigit() else n.section.number
            return [_callout("🧭", "gray_background", self.rich(text))
                    for text in signposts.get((key, position), [])]

        def emit_section(n, depth: int) -> list[dict]:
            self._section = n.section.display
            inline_children = [ch for ch in n.children if ch.placement == "inline"]
            if depth == 0:
                out = signs(n, "start") + self.fragment_blocks(n.section.fragments)
                for ch in inline_children:
                    out += emit_section(ch, 1)
                return out + signs(n, "end")
            # "deep levels collapse" is judged by the *thesis* level, not nesting depth
            # on the page: the page plan makes every section with subsections its own
            # page, so inlined sections are always leaves - subsections (§x.y.z) read
            # openly, subsubsections (§w.x.y.z) fold away.
            if n.section.level < TOGGLE_LEVEL:
                out = [{"type": "divider", "divider": {}},
                       _heading(depth, [{"type": "text",
                                         "text": {"content": n.section.display}}])]
                out += signs(n, "start") + self.fragment_blocks(n.section.fragments)
                for ch in inline_children:
                    out += emit_section(ch, depth + 1)
                return out + signs(n, "end")
            # collapsed toggle heading carrying its whole subtree; top-level toggles
            # on a page keep the divider between them
            content = signs(n, "start") + self.fragment_blocks(n.section.fragments)
            for ch in inline_children:
                content += emit_section(ch, depth + 1)
            content += signs(n, "end")
            self._section = n.section.display
            toggle = _heading(depth,
                              [{"type": "text", "text": {"content": n.section.display}}],
                              toggleable=True)
            toggle["_deferred"] = content
            prefix = [{"type": "divider", "divider": {}}] if depth == 1 else []
            return prefix + [toggle]

        if body_override is not None:
            body = list(body_override)
        elif node is not None:
            body = emit_section(node, 0)

        head: list[dict] = []
        if _has_heading(body):
            head.append({"type": "table_of_contents",
                         "table_of_contents": {"color": "default"}})

        self._section = page_key
        tail = callout_blocks(notes, questions, self.rich)
        tail.append(_callout("⚠️", "gray_background",
                             [{"type": "text", "text": {"content": footer_text()}}]))
        return head + body + tail


def _has_heading(blocks: list[dict]) -> bool:
    return any(b["type"].startswith("heading_") for b in blocks)


def _heading(level: int, runs: list[dict], toggleable: bool = False) -> dict:
    tag = f"heading_{min(max(level, 1), 3)}"
    body: dict = {"rich_text": runs}
    if toggleable:
        body["is_toggleable"] = True
    return {"type": tag, tag: body}


def _callout(emoji: str, color: str, runs: list[dict], children: list | None = None) -> dict:
    out = {"type": "callout", "callout": {
        "icon": {"type": "emoji", "emoji": emoji}, "color": color, "rich_text": runs}}
    if children:
        out["callout"]["children"] = children
    return out
