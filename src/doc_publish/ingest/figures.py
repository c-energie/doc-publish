"""Figure manifest: resolve bare \\includegraphics filenames via \\graphicspath.

\\graphicspath lives in custom_settings.sty (7 directories at time of writing) and figure
filenames are unique thesis-wide by convention - so a filename resolving to two files means
the convention has been broken, and that is reported rather than silently resolved.

Commented-out figure blocks are captured too, with status "pending": the results section
carries several figures whose assets are awaiting a notebook re-run, and an agent that
claims those figures exist is worse than one that says they are pending.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .flatten import LABEL_RE, _brace, inline, settings_files

SETTINGS = "custom_settings.sty"
GRAPHICSPATH_RE = re.compile(r"\\graphicspath\{(.*?)\n\}", re.S)
INCLUDEGFX_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\{([^}]+)\}")
FIGURE_RE = re.compile(r"\\begin\{figure\}(.*?)\\end\{figure\}", re.S)
EXTS = (".png", ".pdf", ".jpg", ".jpeg", ".eps")

#: Where a document keeps its sections. "Chapters" is the pre-rename spelling and is
#: still searched, so this engine reads an older document repo unchanged. Both are
#: swept because this is a last-resort fallback: a hit under either is still a hit, and
#: an ambiguous filename is reported rather than silently resolved (see `resolve`).
SECTION_ROOTS = ("Sections", "Chapters")


def graphics_roots(repo: Path) -> list[Path]:
    # \graphicspath may live in any of the document's own .sty files, not only one
    # with an assumed name; the first that declares it wins.
    for path in settings_files(repo):
        raw = path.read_text(encoding="utf-8", errors="replace")
        m = GRAPHICSPATH_RE.search(raw)
        if m:
            return [repo / p for p in re.findall(r"\{([^{}]+)\}", m.group(1))]
    return [repo]


def resolve(name: str, roots: list[Path], repo: Path) -> list[Path]:
    stem = Path(name).stem
    hits: set[Path] = set()
    for root in roots:
        if root.exists():
            for ext in EXTS:
                hits.update(root.glob(f"{stem}{ext}"))
    if not hits:  # last resort: anywhere under the section tree
        for root in SECTION_ROOTS:
            for ext in EXTS:
                hits.update((repo / root).rglob(f"{stem}{ext}"))
    return sorted(hits)


def _blocks(raw: str) -> list[tuple[str, bool]]:
    """(figure_block, is_commented).

    The two scans must run on disjoint text. A commented-out figure block still contains
    the literal \\begin{figure}, so scanning the raw source counts it as live - which is
    exactly the failure mode that would have the agent promising a figure whose PNG is
    waiting on a notebook re-run.
    """
    lines = raw.splitlines()
    live = "\n".join(l for l in lines if not l.lstrip().startswith("%"))
    dead = "\n".join(l.lstrip().lstrip("%").rstrip() for l in lines if l.lstrip().startswith("%"))
    return ([(m.group(1), False) for m in FIGURE_RE.finditer(live)]
            + [(m.group(1), True) for m in FIGURE_RE.finditer(dead)])


def build_manifest(repo: Path, out_dir: Path, section_of: dict[str, str]) -> list[dict]:
    roots = graphics_roots(repo)
    raw = inline(repo, "main.tex")
    (out_dir / "figures").mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []

    for block, commented in _blocks(raw):
        files = INCLUDEGFX_RE.findall(block)
        if not files:
            continue
        lab = LABEL_RE.search(block)
        cap = re.search(r"\\caption\{", block)
        label = lab.group(1) if lab else f"fig:unlabelled:{len(manifest)}"
        entry = {
            "label": label,
            "caption": re.sub(r"\s+", " ", _brace(block, cap.end() - 1)[0]).strip() if cap else "",
            "section": section_of.get(label, "unknown"),
            "commented_out": commented,
            "assets": [],
        }
        for f in files:
            hits = resolve(f, roots, repo)
            if not hits:
                entry["assets"].append({"name": f, "status": "pending" if commented else "missing"})
            elif len(hits) > 1:
                entry["assets"].append({"name": f, "status": "ambiguous",
                                        "candidates": [str(h.relative_to(repo)) for h in hits]})
            else:
                dest = out_dir / "figures" / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', label)}{hits[0].suffix}"
                shutil.copy2(hits[0], dest)
                entry["assets"].append({"name": f, "status": "ok",
                                        "path": str(dest.relative_to(out_dir))})
        manifest.append(entry)

    (out_dir / "figures.json").write_text(json.dumps(manifest, indent=2))
    return manifest
