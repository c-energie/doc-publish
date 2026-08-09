"""Page plan: which sections get their own Notion page, and pinning.

Rule for a *new* section: own page if it has subsections or its own body exceeds
PAGE_WORD_THRESHOLD words; otherwise it is inlined into the nearest paged ancestor
under a heading. Chapters are always pages.

The first decision per section is pinned in the manifest and later runs never
restructure automatically - a promotion moves content to a new page ID, which breaks
every inbound link and orphans any comments. When a fresh decision disagrees with the
pin, the run *reports* it ("§6.3 now exceeds threshold - promote?") and keeps the pin.
Promotion is a deliberate act: delete the pin from the manifest and re-run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .emit import Section

PAGE_WORD_THRESHOLD = 1000


@dataclass
class PlanNode:
    section: Section
    placement: str                       # "page" | "inline"
    page_key: str                        # manifest key of the page this content lives on
    children: list = field(default_factory=list)


@dataclass
class Plan:
    nodes: dict = field(default_factory=dict)      # key -> PlanNode
    page_order: list = field(default_factory=list) # page keys, document order
    parent_of: dict = field(default_factory=dict)  # page key -> parent page key
    drift: list = field(default_factory=list)      # human-readable pin-vs-fresh reports


def key_of(section: Section) -> str:
    return "§" + section.number if section.number[0].isdigit() else section.number


def fresh_decision(section: Section) -> str:
    if section.level <= 1:
        return "page"
    if section.children or section.own_words() > PAGE_WORD_THRESHOLD:
        return "page"
    return "inline"


def build_plan(root: Section, pinned: dict[str, str]) -> Plan:
    """`pinned` maps section key -> placement from the manifest."""
    plan = Plan()

    def visit(section: Section, parent_page_key: str) -> PlanNode:
        key = key_of(section)
        fresh = fresh_decision(section)
        placement = pinned.get(key, fresh)
        if key in pinned and pinned[key] != fresh:
            verb = "promote to its own page" if fresh == "page" else "could be inlined"
            reason = ("now has subsections or exceeds the word threshold"
                      if fresh == "page" else "no longer meets the page criteria")
            plan.drift.append(f"{key} {reason} - {verb}? (pinned: {pinned[key]}, kept)")

        page_key = key if placement == "page" else parent_page_key
        node = PlanNode(section=section, placement=placement, page_key=page_key)
        plan.nodes[key] = node
        if placement == "page":
            plan.page_order.append(key)
            plan.parent_of[key] = parent_page_key
        node.children = [visit(ch, page_key) for ch in section.children]
        return node

    for ch in root.children:
        visit(ch, "root")
    return plan


def page_title(section: Section) -> str:
    return section.display
