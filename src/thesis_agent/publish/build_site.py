"""Build the Quarto site from the corpus: thesis-agent site

Second publish stream beside the Notion wiki - same corpus, same page plan (the
pinned placements come from the Notion manifest, so both streams keep identical page
boundaries and the pinning rule keeps its meaning: drift is reported, never applied).
Only the emitter differs.

    thesis-agent site              parse -> plan -> qmd -> render -> report
    thesis-agent site --no-render  write the qmd project only (no Quarto needed)

Inputs:  corpus_public.md (+ labels/figures json) from the build dir; signposts.md
         (approved entries only) and notion_manifest.json (placement pins) from the
         state dir in the document repo.
Outputs: the site dir (qmd project) and site build dir (rendered HTML), both build
         products; anchor_map.json and site_report.md into the state dir, committed -
         the anchor map is what keeps URLs stable across builds.

Publishes the PUBLIC corpus only. Leaked LaTeX comments are excluded and listed in
the report; draft notes and annotations.json never enter this stream at all.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .. import config
from . import signposts
from .emit import INLINE_MATH_RE, KATEX_OK, parse_corpus, katex_fix
from .quarto import site as qsite
from .quarto.figures import FigureEmitter
from .quarto.qmd import (PageWriter, Resolver, build_anchor_map, page_file,
                         PHYSICS_MACROS, SHIM_MACROS)
from .structure import build_plan
from .sync import load_json


def katex_demotion_counts(text: str) -> tuple[int, int]:
    """How many equations the Notion/KaTeX stream demotes to code (display) or
    code-styled text (inline) - the comparison number for the MathJax stream."""
    root, _ = parse_corpus(text, math="katex")
    display = sum(1 for s in root.walk() for f in s.fragments
                  if f.kind == "equation" and f.as_code)
    inline = 0
    for s in root.walk():
        for f in s.fragments:
            for m in INLINE_MATH_RE.finditer(f.text + " " + f.caption):
                if katex_fix(m.group(1))[2]:
                    inline += 1
    return display, inline


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="thesis-agent site")
    ap.add_argument("--no-render", action="store_true",
                    help="write the qmd project but skip `quarto render`")
    args = ap.parse_args(argv)

    try:
        thesis_repo = config.thesis_repo()
        BUILD = config.build_dir()
        STATE = config.state_dir(create=True)
        SITE = config.site_dir()
        SITE_BUILD = config.site_build_dir()
        source = config.source_label()
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    corpus_path = BUILD / "corpus_public.md"
    if not corpus_path.exists():
        print(f"{corpus_path} missing - run `thesis-agent build` first", file=sys.stderr)
        return 2
    text = corpus_path.read_text(encoding="utf-8")

    report: list[str] = []
    root, leaks = parse_corpus(text, math="mathjax")

    manifest = load_json(STATE / "notion_manifest.json", {"sections": {}})
    pinned = {k: v["pinned_placement"] for k, v in manifest["sections"].items()
              if v.get("pinned_placement")}
    plan = build_plan(root, pinned)

    labels = load_json(BUILD / "labels.json", {})
    figures_json = load_json(BUILD / "figures.json", [])

    from .quarto import references
    entries: list[dict] = []
    entries = references.bib_entries(thesis_repo)

    anchors, float_kinds = build_anchor_map(plan, labels, root)
    resolver = Resolver(anchors, float_kinds, section_of=labels,
                        citation_hrefs=references.citation_hrefs(entries, report))

    section_titles = {"§" + s.number: s.title for s in root.walk()
                      if s.number and s.number[0].isdigit()}
    signs = signposts.approved(STATE / "signposts.md", section_titles, report)

    SITE.mkdir(parents=True, exist_ok=True)
    figures = FigureEmitter(
        figures_json,
        [BUILD / "figures_manifest.json", thesis_repo / "figures_manifest.json"],
        BUILD, SITE)

    known_math = KATEX_OK | set(SHIM_MACROS)   # MathJax >= KaTeX; shims cover the rest
    writer = PageWriter(plan, resolver, figures, signs, known_math)

    for key in plan.page_order:
        (SITE / page_file(key)).write_text(writer.render_page(key), encoding="utf-8")

    chapters = [k for k in plan.page_order if plan.parent_of.get(k, "root") == "root"]
    index = ["---", 'title: "Thesis"', "---", "",
             f"Rendered from the LaTeX source of `{source}` by `thesis-agent site`. "
             f"The LaTeX is the source of truth.", "",
             "## Chapters", ""]
    index += [f"- [{plan.nodes[k].section.display}]({page_file(k).replace('.qmd', '.html')})"
              for k in chapters]
    if entries:
        references.write_page(entries, SITE)
        index += ["", "[References](references.html)"]
    (SITE / "index.qmd").write_text("\n".join(index) + "\n", encoding="utf-8")

    anchors.save(STATE / "anchor_map.json")
    qsite.write_config(SITE, SITE_BUILD, plan, references=bool(entries))
    qsite.write_shims(SITE, {})
    qsite.vendor_mathjax(SITE)

    if not args.no_render:
        qsite.render(SITE, SITE_BUILD)

    # ---- report -------------------------------------------------------------
    kd_display, kd_inline = katex_demotion_counts(text)
    physics_used = sorted({c for _, _, cmds in writer.math_warnings
                           for c in cmds if c in PHYSICS_MACROS})
    unshimmed = [(sec, expr, cmds) for sec, expr, cmds in writer.math_warnings]
    L = ["# Site build report", ""]
    L.append(f"- pages: {len(plan.page_order) + 1} (incl. index)")
    L.append(f"- anchors: {len(anchors.map)} labels in anchor_map.json")
    L.append(f"- display equations: {writer.equations}, all passed to MathJax as "
             f"authored (the KaTeX/Notion stream demotes {kd_display} display + "
             f"{kd_inline} inline expressions to code)")
    L.append(f"- figures: {figures.summary()}")
    L.append(f"- citations: {resolver.citations_linked} linked to references.html, "
             f"{resolver.citations_plain} unmatched (left as plain text)")
    L.append(f"- signposts published: {sum(len(v) for v in signs.values())} "
             f"at {len(signs)} anchors (approved entries only)")
    L.append("")
    L.append(f"## Unresolved references ({len(resolver.unresolved)})")
    L += [f"- {u}" for u in resolver.unresolved] or ["(none)"]
    L.append("")
    L.append(f"## Equation warnings ({len(unshimmed)})")
    if physics_used:
        L.append(f"physics-package macros in use ({', '.join(physics_used)}) - "
                 f"these need \\newcommand shims in publish/quarto/qmd.py")
    L += [f"- {sec}: `{expr}` uses {', '.join(cmds)}" for sec, expr, cmds in unshimmed] \
        or ["(none - every command is standard TeX or covered by a shim)"]
    L.append("")
    L.append(f"## Leaked LaTeX comments excluded from the site ({len(leaks)})")
    L += [f"- {f.text[:140]}" for f in leaks] or ["(none)"]
    L.append("")
    L.append("## Structure drift (pinned decisions kept)")
    L += [f"- {d}" for d in plan.drift] or ["(none)"]
    L.append("")
    L += [f"note: {r}" for r in report]
    (STATE / "site_report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L[:12]))
    print(f"\nfull report: {STATE / 'site_report.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
