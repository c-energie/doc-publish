"""Idempotent document -> Notion wiki sync. Entry point: doc-publish sync

    doc-publish sync                       publish to DOC_NOTION_PARENT
    doc-publish sync --sandbox <page-id>   publish under a scratch page instead
    doc-publish sync --dry-run             no network writes; reports only

Publishes the PUBLIC corpus only. Draft-note text reaches the wiki solely through the
filtered "Open questions" callouts (see callouts.py).

Idempotency contract: each page's rendered content is hashed against the manifest.
Unchanged -> skipped without any API write. Changed -> that page's child blocks are
replaced in place, so the page ID (and every inbound link) survives. A section that
disappears from the corpus is flagged in the sync report and never deleted. Running
sync twice in a row makes zero write calls on the second run - the run prints the
write-call count so this is checkable.

State lives in the *document* repo, under `.doc-publish/` (see config.state_dir):
    notion_manifest.json   label -> page_id / pinned_placement / hash
    katex_report.md        equations rewritten or unlikely to render
    sync_report.md         what the last run did and what needs a human
    signposts.md           chapter intros for review (see signposts.py)
Sandbox runs keep their own manifest (notion_manifest.sandbox.json) so a scratch
publish can never orphan the real wiki's manifest.

IDs come from env vars and context.json only (override the location with
DOC_CONTEXT_JSON). The open-questions database ID is read from context.json's
`pageParents` map - database IDs, the kind page creation and (on API 2022-06-28)
queries accept.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

from .. import config
from . import callouts, databases, signposts
from .emit import CITE_RE, parse_corpus
from .linker import Linker, page_url
from .notion import Notion, NotionError
from .structure import build_plan, page_title

# themed chapter icons (agreed 2026-08-04); section pages inherit their chapter's
CHAPTER_ICONS = {"§1": "🎯", "§2": "📚", "§3": "🧩", "§4": "📐", "§5": "📊",
                 "§6": "💬", "§7": "🏁", "§8": "🎓", "vocabulary": "📖"}


def icon_for(key: str) -> str:
    chapter = "§" + key[1:].split(".")[0] if key.startswith("§") else key
    return CHAPTER_ICONS.get(chapter, "📄")


def cited_in_map(root) -> dict[str, list[str]]:
    """author-year label -> section keys citing it, in document order."""
    out: dict[str, list[str]] = {}
    for section in root.walk():
        if not section.number[0].isdigit():
            continue
        text = " ".join([f.text + " " + f.caption for f in section.fragments]
                        + [c for f in section.fragments for row in f.rows for c in row])
        for m in CITE_RE.finditer(text):
            for comp in m.group(1).split("; "):
                key = "§" + section.number
                out.setdefault(comp.strip(), [])
                if key not in out[comp.strip()]:
                    out[comp.strip()].append(key)
    return out


def content_hash(title: str, blocks: list[dict]) -> str:
    payload = json.dumps({"title": title, "blocks": blocks}, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_json(path: Path, default):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def load_context_json() -> dict | None:
    """The project's context repo, holding the Notion database ids.

    Explicit path wins. Otherwise try the conventional sibling checkout, relative to
    the working directory first and then to the document repo - which is what makes
    this work when the entry points are driven from another repo's directory.
    """
    explicit = os.environ.get("DOC_CONTEXT_JSON", "").strip()
    if explicit:
        return load_json(Path(explicit), None)
    candidates = [Path("../thesis-context/context.json")]
    try:
        candidates.append(config.document_repo().parent / "thesis-context" / "context.json")
    except config.ConfigError:
        pass
    for candidate in candidates:
        found = load_json(candidate, None)
        if found is not None:
            return found
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="doc-publish sync")
    ap.add_argument("--sandbox", metavar="PAGE_ID",
                    help="publish under this scratch page, with a separate manifest")
    ap.add_argument("--dry-run", action="store_true", help="no network calls that write")
    ap.add_argument("--corpus", default=None,
                    help="corpus to publish (default: corpus_public.md in the build dir)")
    args = ap.parse_args(argv)

    try:
        BUILD = config.build_dir()
        STATE = config.state_dir(create=True)
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    corpus_path = Path(args.corpus) if args.corpus else BUILD / "corpus_public.md"
    if not corpus_path.exists():
        print(f"{corpus_path} missing - run `doc-publish build` first", file=sys.stderr)
        return 2

    manifest_path = STATE / ("notion_manifest.sandbox.json" if args.sandbox
                             else "notion_manifest.json")
    manifest = load_json(manifest_path, {"parent": None, "sections": {}})

    # -- parent + manifest binding (before pins are read from the manifest) ---
    parent = args.sandbox or os.environ.get("DOC_NOTION_PARENT")
    if not parent and not args.dry_run:
        print("set DOC_NOTION_PARENT (or pass --sandbox <page id>)", file=sys.stderr)
        return 2
    if manifest["parent"] and parent and manifest["parent"] != parent:
        if args.sandbox:
            print(f"sandbox parent changed ({manifest['parent']} -> {parent}); "
                  f"starting a fresh sandbox manifest")
            manifest = {"parent": None, "sections": {}}
        else:
            print(f"manifest is bound to parent {manifest['parent']} but "
                  f"DOC_NOTION_PARENT is {parent}. Refusing: publishing the wiki "
                  f"under a second parent would fork it. Fix the env var, or move "
                  f"{manifest_path} aside to deliberately start over.",
                  file=sys.stderr)
            return 2

    # -- parse + plan ---------------------------------------------------------
    root, leaks = parse_corpus(corpus_path.read_text(encoding="utf-8"))
    pinned = {k: v["pinned_placement"] for k, v in manifest["sections"].items()
              if v.get("pinned_placement")}
    plan = build_plan(root, pinned)

    signpost_path = STATE / "signposts.md"
    if signposts.ensure_scaffold(signpost_path):
        print(f"signposts: wrote scaffold {signpost_path} - draft entries with the "
              f"signpost-editor subagent, approve them, then re-run sync")
    section_titles = {("§" + s.number if s.number[0].isdigit() else s.number): s.title
                      for s in root.walk() if s.number != "root"}

    labels = load_json(BUILD / "labels.json", {})
    figures = load_json(BUILD / "figures.json", [])
    # Written by `doc-publish figures`; absent until that has run, and every figure then
    # publishes as its static image rather than an embed of the hosted plot.
    interactive = load_json(BUILD / "figures_manifest.json", {})
    annotations = load_json(BUILD / "annotations.json", [])

    # labels.json only records labels near section headings - figure/table labels
    # never reach it (ingest strips \label inside float environments). The parsed
    # corpus knows which section owns every float stub, so float references
    # ("Figure [Fig: x]" in prose, "[Fig: x]" in signposts) resolve from here.
    float_labels = {f.label: "§" + s.number
                    for s in root.walk() if s.number and s.number[0].isdigit()
                    for f in s.fragments if f.kind in ("figure", "table") and f.label}
    labels.update(float_labels)

    notion = Notion(dry_run=args.dry_run)
    report: list[str] = []

    # Save incrementally: a run can die mid-publish (500+ writes take many minutes),
    # and a manifest that only lands at exit would forget every page and database row
    # already created - the next run would then duplicate them.
    manifest["parent"] = manifest["parent"] or parent

    def save() -> None:
        if not args.dry_run:
            manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    # -- open questions (best-effort: the wiki must build without them) -------
    questions: list[dict] = []
    ctx = load_context_json()
    if ctx is None:
        report.append("context.json not found - open-questions callouts will not be "
                      "linked to the Notion database this run")
    elif not args.dry_run:
        try:
            oq_db = ctx["state"]["notion"]["pageParents"]["openQuestions"]
            questions = callouts.fetch_open_questions(notion, oq_db)
        except (KeyError, NotionError) as e:
            report.append(f"open-questions lookup failed ({e}) - callouts unlinked this run")

    notes = callouts.substantive(annotations)
    notes_by_page: dict[str, list[dict]] = {}
    for a in notes:
        node = plan.nodes.get(a["section"])
        if node is None:
            report.append(f"draft note in {a['section']} has no page (front matter?) - "
                          f"not published: {a['note'][:80]}")
            continue
        notes_by_page.setdefault(node.page_key, []).append(a)

    # -- pass one: ensure every page exists, with its icon --------------------
    sections = manifest["sections"]
    page_keys = list(plan.page_order)
    page_ids: dict[str, str] = {"root": parent or "dry-run-root"}
    created, renamed = [], []
    titles = {k: page_title(plan.nodes[k].section) for k in page_keys}

    if sections.get("references", {}).get("page_id"):
        report.append("the old References *page* is superseded by the References "
                      "database - delete the page manually when convenient")

    for key in page_keys:
        entry = sections.setdefault(key, {})
        title, icon = titles[key], icon_for(key)
        if not entry.get("page_id"):
            parent_key = plan.parent_of.get(key, "root")
            entry["page_id"] = notion.create_page(page_ids[parent_key], title, icon)
            entry.update({"title": title, "icon": icon})
            created.append(key)
        else:
            if entry.get("title") != title:
                notion.rename_page(entry["page_id"], title)
                report.append(f"{key}: title changed to '{title}'")
                entry["title"] = title
                renamed.append(key)
            if entry.get("icon") != icon:
                notion.set_icon(entry["page_id"], icon)
                entry["icon"] = icon
        page_ids[key] = entry["page_id"]

    # record placement pins (first decision wins, forever - see structure.py)
    for key, node in plan.nodes.items():
        sections.setdefault(key, {}).setdefault("pinned_placement", node.placement)
    save()

    # -- pass 1.5: databases (references + vocabulary) ------------------------
    dbs = manifest.setdefault("databases", {})
    try:
        document_repo = config.document_repo()
    except config.ConfigError:
        document_repo = None
    # schema-level settings captured from the live databases (select colours,
    # user-added properties, icon, description); views are not API-visible
    settings_path = STATE / "database_settings.json"
    settings = load_json(settings_path, {})

    cit_urls: dict[str, str] = {}
    refs_db_url = None
    if document_repo and document_repo.exists():
        entries = databases.bib_entries(document_repo)
        cited = cited_in_map(root)
        rows = [(e["key"], databases.refs_properties(e, cited.get(e["label"], [])))
                for e in entries]
        refs_db = databases.sync_database(
            notion, dbs.setdefault("references", {}), page_ids["root"],
            "References", "🔖", databases.REFS_SCHEMA, rows, report,
            key_property="Bib key", recorded=settings.get("references"))
        save()
        cit_urls = databases.citation_urls(refs_db, entries, report)
        if refs_db.get("database_id"):
            refs_db_url = refs_db.get("url") or page_url(refs_db["database_id"])
    else:
        report.append("DOC_REPO not set/found - References database skipped, "
                      "citations published as plain text")

    vocab_override = None
    if "vocabulary" in plan.nodes:
        terms = databases.vocab_terms(plan.nodes["vocabulary"].section.fragments)
        if terms:
            databases.sync_database(
                notion, dbs.setdefault("vocabulary", {}), page_ids["vocabulary"],
                "Vocabulary", "📖", databases.VOCAB_SCHEMA,
                [(t["term"], databases.vocab_properties(t)) for t in terms], report,
                inline=True, key_property="Term", recorded=settings.get("vocabulary"))
            save()
            vocab_override = [{"type": "paragraph", "paragraph": {"rich_text": [
                {"type": "text", "text": {"content":
                 "Every term the thesis defines, as a filterable database - built from "
                 "the LaTeX glossary. Use the Kind column to see only abbreviations, "
                 "symbols, or model notation."}}]}}]

    # -- pass two: render with live links, then update only what changed ------
    base_url = os.environ.get("FIGURES_BASE_URL") or None
    linker = Linker(plan, labels, page_ids, figures, base_url,
                    citation_urls=cit_urls, refs_db_url=refs_db_url,
                    float_labels=set(float_labels), interactive=interactive)
    if base_url is None:
        report.append("FIGURES_BASE_URL not set - figures published as "
                      "'hosting pending' placeholders")
    elif not interactive:
        report.append("build/figures_manifest.json missing - every figure publishes as a "
                      "static image, not an embed of the hosted plot. Run "
                      "`doc-publish figures`. Statics exported as PDF cannot be shown in "
                      "a Notion image block, so those figures will not render.")

    approved_signposts = signposts.approved(signpost_path, section_titles, report)

    updated, skipped = [], []
    for key in page_keys:
        blocks = linker.render_page(
            key,
            approved_signposts,
            notes_by_page.get(key, []),
            questions,
            body_override=vocab_override if key == "vocabulary" else None,
        )
        h = content_hash(titles[key], blocks)
        if sections[key].get("content_hash") == h and key not in created:
            skipped.append(key)
            continue
        if key in created:
            notion.append_tree(page_ids[key], blocks)
        else:
            notion.replace_content(page_ids[key], blocks)
            updated.append(key)
        sections[key]["content_hash"] = h
        save()

    # -- record database settings so the next fresh publish reproduces them ---
    # (schema, select colours, icon, description; views cannot be captured -
    # carry those by moving the tuned database in the UI, which sync adopts)
    if not args.dry_run:
        snap = {name: databases.snapshot_schema(notion, db["database_id"])
                for name, db in dbs.items() if db.get("database_id")}
        if snap and snap != settings:
            settings_path.write_text(json.dumps(snap, indent=2, ensure_ascii=False),
                                     encoding="utf-8")
            report.append(f"database settings changed - recorded to {settings_path}")

    # -- sections gone from the corpus: flag, never delete --------------------
    gone = [k for k in sections
            if k not in plan.nodes and k != "references" and sections[k].get("page_id")]
    for k in gone:
        report.append(f"{k} is in the manifest but no longer in the corpus - page kept, "
                      f"review manually (never auto-deleted)")

    # -- reports + manifest ----------------------------------------------------
    _write_katex_report(STATE, linker.katex_log)
    for line in linker.report:
        report.append(line)
    _write_sync_report(STATE, args, created, renamed, updated, skipped, gone, plan.drift,
                       leaks, report, approved_signposts, notes, notion)

    save()

    print(f"pages: {len(created)} created, {len(updated)} updated, {len(skipped)} unchanged"
          + (f", {len(gone)} gone (kept)" if gone else ""))
    if plan.drift:
        print("structure drift (pinned, not applied):")
        for d in plan.drift:
            print("  ", d)
    print(f"API calls: {notion.writes} writes, {notion.reads} reads"
          + (" [dry run]" if args.dry_run else ""))
    print(f"reports: {STATE / 'sync_report.md'}, {STATE / 'katex_report.md'}")
    return 0


def _write_katex_report(STATE: Path, log: list[tuple[str, str, list[str]]]) -> None:
    lines = ["# KaTeX compatibility report\n",
             "Notion renders equations with KaTeX. Entries here were either rewritten "
             "(trivial fixes, applied automatically) or judged unlikely to render and "
             "published as LaTeX code blocks instead. Nothing is dropped silently.\n"]
    fixes = [(s, e, i) for s, e, i in log if all(x.startswith(("stripped", "escaped")) or "->" in x for x in i)]
    broken = [(s, e, i) for s, e, i in log if (s, e, i) not in fixes]
    lines.append(f"\n## Unlikely to render ({len(broken)})\n")
    for sec, expr, issues in broken or []:
        lines.append(f"- **{sec}**: `{expr[:160]}`\n  - " + "; ".join(issues))
    if not broken:
        lines.append("(none - every equation in the corpus uses KaTeX-supported constructs)")
    lines.append(f"\n## Rewritten automatically ({len(fixes)})\n")
    for sec, expr, issues in fixes or []:
        lines.append(f"- **{sec}**: `{expr[:120]}` - " + "; ".join(issues))
    if not fixes:
        lines.append("(none)")
    (STATE / "katex_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_sync_report(STATE: Path, args, created, renamed, updated, skipped, gone, drift,
                       leaks, report, approved_signposts, notes, notion) -> None:
    L = ["# Sync report\n"]
    L.append(f"mode: {'sandbox' if args.sandbox else 'dry-run' if args.dry_run else 'live'}")
    L.append(f"API calls: {notion.writes} writes, {notion.reads} reads\n")
    L.append(f"## Pages")
    L.append(f"- created: {len(created)}" + (f" — {', '.join(created)}" if created else ""))
    L.append(f"- updated in place: {len(updated)}" + (f" — {', '.join(updated)}" if updated else ""))
    L.append(f"- renamed: {len(renamed)}" + (f" — {', '.join(renamed)}" if renamed else ""))
    L.append(f"- unchanged (no write): {len(skipped)}")
    if gone:
        L.append(f"- gone from corpus, kept in Notion: {', '.join(gone)}")
    L.append(f"\n## Structure drift (pinned decisions kept)\n")
    L += [f"- {d}" for d in drift] or ["(none)"]
    anchors = [f"{k} {p}" for (k, p) in approved_signposts]
    L.append(f"\n## Signposts\n- approved & published: "
             f"{sum(len(v) for v in approved_signposts.values())} "
             f"({', '.join(anchors) or 'none yet'})")
    L.append(f"\n## Open-question callouts\n- substantive draft notes published: {len(notes)}")
    L.append(f"\n## Leaked LaTeX comments excluded from the wiki ({len(leaks)})\n")
    L += [f"- {f.text[:140]}" for f in leaks] or ["(none)"]
    L.append(f"\n## Notes\n")
    L += [f"- {r}" for r in report] or ["(none)"]
    (STATE / "sync_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
