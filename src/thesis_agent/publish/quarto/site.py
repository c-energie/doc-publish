"""Quarto project scaffolding and rendering.

Writes site/_quarto.yml (website project, sidebar mirroring the page plan, search,
cosmo + a small serif SCSS), vendors MathJax locally, and runs `quarto render`.

Offline is a hard requirement: the rendered site must work with networking off.
Quarto's theme/bootstrap/search assets are copied into site_libs by default, but its
MathJax defaults to a CDN - so MathJax is vendored into site/mathjax/ (tex-chtml.js
plus its woff-v2 fonts, fetched once from the npm registry into a gitignored vendor
cache) and `html-math-method.url` points at the local copy. Macro shims
(\\newcommand for the corpus's \\deltaT and any physics macros) ride in a hidden
include-before-body block, which MathJax processes first on every page.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import subprocess
import tarfile
import urllib.request
from pathlib import Path

from ... import config
from .qmd import SHIM_MACROS, page_file

MATHJAX_TARBALL = "https://registry.npmjs.org/mathjax/-/mathjax-3.2.2.tgz"

SCSS = """/*-- scss:defaults --*/
$font-family-serif: "Iowan Old Style", "Palatino Linotype", Palatino, Georgia, serif;
$font-family-base: $font-family-serif;
$headings-font-weight: 600;
$primary: #31538f;
$link-color: #31538f;
$body-color: #26262b;

/*-- scss:rules --*/
h1, h2, h3 { letter-spacing: -0.01em; }
.sidebar-title { font-weight: 600; }
figcaption, caption { color: #5b5b63; font-size: 0.92em; }
.callout-note .callout-body { font-size: 0.95em; }
"""


def quarto_exe() -> str:
    exe = os.environ.get("QUARTO") or shutil.which("quarto")
    if exe:
        return exe
    # prefer quarto.exe: quarto.cmd mis-splits its own path on the "Program Files"
    # space when invoked with a different working directory
    for default in (Path(os.environ.get("ProgramFiles", "")) / "Quarto/bin/quarto.exe",
                    Path(os.environ.get("ProgramFiles", "")) / "Quarto/bin/quarto.cmd",
                    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Quarto/bin/quarto.exe"):
        if default.exists():
            return str(default)
    raise SystemExit("quarto not found - install it or set QUARTO to the executable")


def vendor_mathjax(site_dir: Path) -> None:
    """MathJax es5 tex-chtml.js + fonts into site/mathjax/, via a cached download.

    The cache sits under the build directory, not beside this module: once installed,
    the package may live in a read-only site-packages.
    """
    cache = config.vendor_dir() / "mathjax"
    if not (cache / "tex-chtml.js").exists():
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(MATHJAX_TARBALL, timeout=60) as r:
            buf = io.BytesIO(r.read())
        with tarfile.open(fileobj=buf, mode="r:gz") as tar:
            for member in tar.getmembers():
                rel = Path(member.name)           # package/es5/...
                if not member.isfile() or rel.parts[1:2] != ("es5",):
                    continue
                sub = Path(*rel.parts[2:])
                if str(sub) == "tex-chtml.js" or str(sub).startswith(
                        os.path.join("output", "chtml", "fonts")):
                    dest = cache / sub
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(tar.extractfile(member).read())
    dest = site_dir / "mathjax"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(cache, dest)


def write_shims(site_dir: Path, extra_macros: dict[str, str]) -> None:
    """Hidden display-math block registering \\newcommand shims; MathJax keeps
    macros defined in earlier expressions available for the rest of the page."""
    macros = {**SHIM_MACROS, **extra_macros}
    body = " ".join(rf"\newcommand{{\{name}}}{{{repl}}}" for name, repl in
                    sorted(macros.items()))
    (site_dir / "_math-shims.html").write_text(
        f'<div style="display:none" aria-hidden="true">\\[{body}\\]</div>\n',
        encoding="utf-8")


def sidebar_yaml(plan, references: bool) -> list[str]:
    """Sidebar mirroring the page plan: chapters with nested section pages."""
    children: dict[str, list[str]] = {}
    for key in plan.page_order:
        children.setdefault(plan.parent_of.get(key, "root"), []).append(key)

    lines = ["    contents:", "      - index.qmd"]

    def emit(key: str, indent: str) -> None:
        kids = children.get(key, [])
        href = page_file(key)
        if not kids:
            lines.append(f"{indent}- {href}")
            return
        lines.append(f"{indent}- section: {href}")
        lines.append(f"{indent}  contents:")
        for k in kids:
            emit(k, indent + "    ")

    for key in children.get("root", []):
        emit(key, "      ")
    if references:
        lines.append("      - references.qmd")
    return lines


def write_config(site_dir: Path, build_dir: Path, plan, title: str = "Thesis",
                 references: bool = False) -> None:
    # Quarto resolves output-dir relative to the project directory, so the two
    # directories no longer have to be siblings - they are configured independently.
    output_dir = os.path.relpath(build_dir.resolve(), site_dir.resolve()).replace("\\", "/")
    lines = [
        "project:",
        "  type: website",
        f"  output-dir: {output_dir}",
        "  resources:",
        '    - "mathjax/**"',      # Quarto only copies referenced files; MathJax
        '    - "figures/**"',      # lazy-loads its fonts, so force the whole tree
        "",
        "website:",
        f'  title: "{title}"',
        "  search:",
        "    location: sidebar",
        "    type: textbox",
        "  sidebar:",
        "    style: docked",
        "    collapse-level: 1",
        *sidebar_yaml(plan, references),
        "",
        "format:",
        "  html:",
        "    theme:",
        "      - cosmo",
        "      - custom.scss",
        "    toc: true",
        "    toc-depth: 4",
        "    html-math-method:",
        "      method: mathjax",
        '      url: "mathjax/tex-chtml.js"',
        "    include-before-body: _math-shims.html",
        "    link-external-newwindow: true",
        "",
        "execute:",
        "  enabled: false",
        "",
    ]
    (site_dir / "_quarto.yml").write_text("\n".join(lines), encoding="utf-8")
    (site_dir / "custom.scss").write_text(SCSS, encoding="utf-8")


def render(site_dir: Path, build_dir: Path) -> None:
    # Quarto refuses to clean an output-dir outside the project directory, so stale
    # pages would accumulate across builds - a removed page lingering in the output
    # is exactly what the leak checks exist to prevent. Fresh output every build.
    if build_dir.exists():
        shutil.rmtree(build_dir)
    subprocess.run([quarto_exe(), "render"], cwd=site_dir, check=True)
    _strip_cdn_polyfill(build_dir)


def _strip_cdn_polyfill(build_dir: Path) -> None:
    """Quarto pairs MathJax with a cdnjs es6-polyfill <script> and offers no switch
    for it. It exists for pre-2017 browsers; offline is a hard requirement here, so
    it is removed post-render - the only external reference in the whole site."""
    pat = re.compile(r'\s*<script src="https://cdnjs\.cloudflare\.com/polyfill[^"]*"></script>')
    for page in build_dir.glob("*.html"):
        html = page.read_text(encoding="utf-8")
        stripped = pat.sub("", html)
        if stripped != html:
            page.write_text(stripped, encoding="utf-8")
