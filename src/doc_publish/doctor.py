"""`doc-publish doctor` - one command that says what is set up and what is not.

Setting this toolchain up means satisfying a handful of conditions spread across three
repositories, an environment variable, an optional system dependency and two optional
credentials. Each of them already fails with a decent message *at the moment you trip
over it* - but that means discovering them one at a time, several commands apart, which
is the actual cost of getting started.

This walks the whole list at once and prints, for anything not ready, the specific thing
to do about it. It reads and never writes, so it is safe to run at any point.

Exit code is 0 when everything *required* is in place, 1 otherwise. Optional streams
(site, Notion, publish) report as `--` and never fail the command: a document that only
wants a corpus is correctly set up.

Written to be useful to an agent as well as a person. `--json` emits the same findings as
a machine-readable object, so an assistant helping someone set this up can read the state
rather than guessing from prose.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

from . import config

#: Result states. `warn` means usable but worth knowing; `skip` means an optional
#: feature that is simply not configured, which is not a problem.
OK, WARN, FAIL, SKIP = "ok", "warn", "fail", "skip"

MARK = {OK: "ok  ", WARN: "warn", FAIL: "FAIL", SKIP: "--  "}


class Report:
    """Findings, in the order they were added."""

    def __init__(self) -> None:
        self.rows: list[dict] = []

    def add(self, group: str, name: str, state: str, detail: str = "",
            fix: str = "") -> None:
        self.rows.append({"group": group, "check": name, "state": state,
                          "detail": detail, "fix": fix})

    @property
    def failed(self) -> bool:
        return any(r["state"] == FAIL for r in self.rows)

    def render(self) -> str:
        out: list[str] = []
        group = None
        for row in self.rows:
            if row["group"] != group:
                group = row["group"]
                out.append(f"\n{group}")
            detail = f"  {row['detail']}" if row["detail"] else ""
            out.append(f"  {MARK[row['state']]}  {row['check']}{detail}")
            if row["fix"]:
                for line in row["fix"].splitlines():
                    out.append(f"          -> {line}")
        return "\n".join(out)


# ------------------------------------------------------------------ the checks

def _check_python(r: Report) -> None:
    v = sys.version_info
    version = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= (3, 11):
        r.add("Environment", "python", OK, version)
    else:
        r.add("Environment", "python", FAIL, version,
              "doc-publish needs Python 3.11.4 or newer.")

    env_file = os.environ.get("DOC_ENV") or _nearest_env()
    if env_file:
        r.add("Environment", ".env", OK, str(env_file))
    else:
        r.add("Environment", ".env", WARN, "none found",
              "Run `doc-publish env` to write one, or set DOC_REPO in your shell.")


def _nearest_env() -> Path | None:
    base = Path.cwd().resolve()
    return next((p / ".env" for p in (base, *base.parents) if (p / ".env").is_file()), None)


def _check_document(r: Report) -> Path | None:
    """The document repo and its root .tex. Everything else depends on this."""
    try:
        repo = config.document_repo()
    except config.ConfigError as exc:
        r.add("Document", "DOC_REPO", FAIL, str(exc),
              "Set DOC_REPO to your LaTeX document repo, in .env or your shell.")
        return None

    r.add("Document", "DOC_REPO", OK, str(repo))
    root = config.main_tex(repo)
    r.add("Document", "root .tex", OK, root)
    r.add("Document", "title", OK, config.title())
    return repo


def _check_inputs(r: Report, repo: Path) -> None:
    """What the flattener will find when it reads this document."""
    from .ingest import flatten

    settings = flatten.settings_files(repo)
    if settings:
        r.add("Inputs", "settings .sty", OK,
              ", ".join(p.name for p in settings))
    else:
        r.add("Inputs", "settings .sty", WARN, "none at the repo root",
              "\\graphicspath and zero-argument macros are read from root .sty files.")

    gloss = flatten.glossary_source(repo)
    n_acr = gloss.count("\\newacronym")
    n_gls = gloss.count("\\newglossaryentry")
    if n_acr or n_gls:
        r.add("Inputs", "glossary", OK, f"{n_acr} acronym(s), {n_gls} glossary entry(ies)")
    else:
        r.add("Inputs", "glossary", SKIP, "none found",
              "Fine if the document has none. \\acrshort{...} would not resolve.")

    bibs = flatten.bib_files(repo)
    if bibs:
        r.add("Inputs", "bibliography", OK,
              ", ".join(str(p.relative_to(repo)) for p in bibs))
    else:
        r.add("Inputs", "bibliography", WARN, "no .bib found",
              "Every \\cite would become [UNRESOLVED: cite ...]. Check \\addbibresource.")

    _check_graphicspath(r, repo, flatten)


def _check_graphicspath(r: Report, repo: Path, flatten) -> None:
    """\\graphicspath does not recurse, so a missing directory is a silent build error."""
    from .ingest import figures

    roots = figures.graphics_roots(repo)
    if roots == [repo]:
        r.add("Inputs", "\\graphicspath", WARN, "not declared",
              "Figures resolve by bare filename via \\graphicspath. Without it only the "
              "repo root is searched.")
        return

    missing = [p for p in roots if not p.exists()]
    if missing:
        r.add("Inputs", "\\graphicspath", WARN,
              f"{len(roots)} dir(s), {len(missing)} missing",
              "\n".join(f"missing: {p.relative_to(repo)}" for p in missing))
    else:
        r.add("Inputs", "\\graphicspath", OK, f"{len(roots)} dir(s), all present")


def _check_sections(r: Report, repo: Path) -> None:
    """Where analysis-template writes. Only matters if figures are generated."""
    for name in ("Sections", "Chapters"):
        found = repo / name
        if found.is_dir():
            actual = next((c.name for c in repo.iterdir()
                           if c.is_dir() and c.name.lower() == name.lower()), name)
            note = "" if actual == "Sections" else "  (not the Sections/ default; still read)"
            r.add("Figures", "section tree", OK, actual + "/" + note)
            return
    r.add("Figures", "section tree", SKIP, "no Sections/",
          "Only needed for analysis-template, which writes to $DOC_REPO/Sections/<Name>/.")


def _check_contract(r: Report, repo: Path) -> None:
    from . import contract

    state = config.state_dir_for(repo)
    if not state.is_dir():
        r.add("Contract", "state dir", WARN, "missing",
              "Run `doc-publish init`. Needed to publish or to point an agent at this "
              "document; `doc-publish build` works without it.")
        return
    r.add("Contract", "state dir", OK, str(state.relative_to(repo)))

    prompt = state / "prompt.md"
    if not prompt.exists():
        r.add("Contract", "prompt.md", WARN, "missing",
              "Run `doc-publish init`.")
        return
    todos = prompt.read_text(encoding="utf-8", errors="replace").count(contract.TODO)
    if todos:
        r.add("Contract", "prompt.md", WARN, f"{todos} [TODO:] marker(s) left",
              "Write it before pointing any agent at this document - an unfinished "
              "prompt produces confident, wrong answers in your name.")
    else:
        r.add("Contract", "prompt.md", OK, "written")


def _check_streams(r: Report) -> None:
    """The optional outputs. None of these failing is a setup problem."""
    quarto = config.getenv("QUARTO") or shutil.which("quarto")
    if quarto:
        r.add("Optional streams", "quarto (site)", OK, str(quarto))
    else:
        r.add("Optional streams", "quarto (site)", SKIP, "not on PATH",
              "Needed only for `doc-publish site`. A Python extra cannot install it: "
              "`winget install Posit.Quarto` or your platform's equivalent.")

    token = config.getenv("NOTION_TOKEN")
    parent = config.getenv("DOC_NOTION_PARENT")
    if token and parent:
        r.add("Optional streams", "notion (sync)", OK, "token and parent set")
    elif token or parent:
        have, missing = ("NOTION_TOKEN", "DOC_NOTION_PARENT") if token else (
            "DOC_NOTION_PARENT", "NOTION_TOKEN")
        r.add("Optional streams", "notion (sync)", WARN, f"{have} set, {missing} not",
              f"`doc-publish sync` needs both. Set {missing}.")
    else:
        r.add("Optional streams", "notion (sync)", SKIP, "not configured",
              "Needed only for `doc-publish sync`.")

    try:
        repo = config.publish_repo()
        r.add("Optional streams", "publish repo", OK, str(repo))
    except config.ConfigError as exc:
        state = SKIP if "is not set" in str(exc) else WARN
        r.add("Optional streams", "publish repo", state, str(exc).split(" - ")[0],
              "Needed only for `doc-publish publish`." if state == SKIP else str(exc))

    mode = config.getenv("CORPUS_MODE", "public")
    if mode == "draft":
        r.add("Optional streams", "CORPUS_MODE", WARN, "draft",
              "The draft corpus carries your source comments. The publish path refuses "
              "to run until this is `public`.")
    else:
        r.add("Optional streams", "CORPUS_MODE", OK, mode)


# -------------------------------------------------------------------------- main

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="doc-publish doctor",
        description="Report what is set up and what is not. Reads only.")
    ap.add_argument("--json", action="store_true",
                    help="Emit findings as JSON, for an agent or a script")
    args = ap.parse_args(argv or [])

    r = Report()
    _check_python(r)
    repo = _check_document(r)
    if repo is not None:
        _check_inputs(r, repo)
        _check_sections(r, repo)
        _check_contract(r, repo)
    _check_streams(r)

    if args.json:
        print(json.dumps({"ok": not r.failed, "findings": r.rows}, indent=2))
        return 1 if r.failed else 0

    print(r.render())
    broken = [x for x in r.rows if x["state"] == FAIL]
    warned = [x for x in r.rows if x["state"] == WARN]
    print()
    if broken:
        print(f"{len(broken)} thing(s) must be fixed before `doc-publish build` runs.")
    else:
        print("`doc-publish build` should work.")
    if warned:
        print(f"{len(warned)} thing(s) worth a look, marked `warn` above.")
    print("Publishing readiness is a separate gate: `doc-publish check`.")
    return 1 if r.failed else 0
