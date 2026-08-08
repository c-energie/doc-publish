"""Map interactive `.html` figure exports onto LaTeX figure labels.

`figures.json` keys figures by label; the html exports are named by filename stem. Match
on stem, and report both directions so nothing is silently unmatched - an export that
matches no label is usually a renamed PNG, which breaks the LaTeX `\\graphicspath`
lookup and the figure index at the same time, with no error from either.

The exports come from the publish repo (thesis-publish writes `figures_html/` beside
its notebooks), so by default this reads `$THESIS_PUBLISH_REPO/figures_html`.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .. import config


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="thesis-agent figures")
    ap.add_argument("html_dir", nargs="?",
                    help="directory of .html exports (default: "
                         "$THESIS_PUBLISH_REPO/figures_html)")
    args = ap.parse_args(argv)

    try:
        build = config.build_dir()
        html_dir = Path(args.html_dir) if args.html_dir \
            else config.publish_repo() / "figures_html"
    except config.ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2

    figures_path = build / "figures.json"
    if not figures_path.exists():
        print(f"{figures_path} missing - run `thesis-agent build` first", file=sys.stderr)
        return 2
    if not html_dir.is_dir():
        print(f"{html_dir} does not exist - nothing to map", file=sys.stderr)
        return 2

    figures = json.loads(figures_path.read_text(encoding="utf-8"))
    html = {p.stem: p for p in html_dir.glob("*.html")}

    manifest, matched = {}, set()
    for f in figures:
        stems = {Path(a.get("name", "")).stem for a in f.get("assets", []) if a.get("name")}
        stems |= {Path(a["path"]).stem for a in f.get("assets", []) if a.get("path")}
        hit = next((html[s] for s in stems if s in html), None)
        if not hit:
            continue
        entry = {"interactive": os.path.relpath(
            hit.resolve(), build.resolve()).replace("\\", "/")}
        static = next((a["path"] for a in f.get("assets", []) if a.get("status") == "ok"), None)
        if static:
            entry["static"] = static
        manifest[f["label"]] = entry
        matched.add(hit.stem)

    out = build / "figures_manifest.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"{len(manifest)} figures mapped to interactive exports -> {out}")

    unmatched = sorted(set(html) - matched)
    if unmatched:
        print(f"\n{len(unmatched)} .html files matched no figure label - check the stem:")
        for s in unmatched:
            print("  ", s)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
