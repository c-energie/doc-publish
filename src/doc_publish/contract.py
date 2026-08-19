"""Scaffold and validate the per-document contract in `.doc-publish/`.

The contract is the handful of files this engine needs from a document it has never
seen: what the document claims and how strongly (`prompt.md`), how to expand any macro
the document defines (`macros.py`), and what shape the published databases take
(`database_settings.json`). Everything else in the state directory is written by the
publisher and must not be authored by hand.

Before this module existed the contract was copied into each document repo by hand, from
a template that had no way of knowing when the engine's expectations changed. Owning both
the skeletons and the validation here means there is one source of truth: `init` writes
the current shape, `check` reports where a document has drifted from it.

`check` is deliberately noisy about `prompt.md`. An unfinished prompt does not fail
loudly at publish time — it produces an agent that answers confidently and wrongly, in
the author's name, which is the most expensive failure this system has.
"""
from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

from . import config

TEMPLATES = Path(__file__).resolve().parent / "contract_templates"

#: Written by the author; the engine reads them and cannot generate them.
CONTRACT_FILES = ("README.md", "prompt.md", "macros.py", "database_settings.json")

#: Written by the publisher. Listed so `check` can report on them, never authored here.
STATE_FILES = ("notion_manifest.json", "anchor_map.json")

#: Marker the skeletons use for "you have not written this yet".
TODO = "[TODO:"

#: Macro-defining commands worth noticing in a document's own .sty files.
MACRO_DEF = re.compile(
    r"\\(?:newcommand|renewcommand|providecommand|newrobustcmd|DeclareRobustCommand)"
    r"\s*\*?\s*\{?\\(\w+)")

SKILLS = ("write-agent-prompt", "write-macro-adapter")

#: Written at the document repo *root*, not in the state dir, because that is where every
#: agent looks for them. AGENTS.md carries the guidance and is tool-neutral on purpose:
#: .claude/skills only helps one assistant, and a document is as likely to be worked on in
#: Cursor or Copilot. CLAUDE.md holds no guidance of its own — Claude Code does not read
#: AGENTS.md by name, so without a file that imports it the guidance is simply never
#: loaded there, and a second copy of it would drift from the first within a month.
ROOT_FILES = ("AGENTS.md", "CLAUDE.md")


def _document_repo(args) -> Path:
    return Path(args.document_repo).expanduser() if args.document_repo else config.document_repo()


def _state_dir(args) -> Path:
    # Via config so a document set up before the rename resolves to its existing
    # .thesis-agent/ instead of being reported as unconfigured.
    return config.state_dir_for(_document_repo(args))


# --------------------------------------------------------------------------- init

def init(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="doc-publish init",
        description="Write the contract skeletons into a document repo.")
    ap.add_argument("--document-repo", default=None,
                    help="Document repo root (default: $DOC_REPO)")
    ap.add_argument("--force", action="store_true",
                    help="Overwrite files that already exist")
    ap.add_argument("--no-skills", action="store_true",
                    help="Skip the .claude/skills helpers for authoring the contract")
    args = ap.parse_args(argv or [])

    repo = _document_repo(args)
    if not repo.is_dir():
        print(f"not a directory: {repo}")
        return 2

    state = _state_dir(args)
    state.mkdir(parents=True, exist_ok=True)

    written, skipped = [], []
    for name in CONTRACT_FILES:
        target = state / name
        if target.exists() and not args.force:
            skipped.append(target)
            continue
        shutil.copyfile(TEMPLATES / name, target)
        written.append(target)

    gitignore = state / ".gitignore"
    if gitignore.exists() and not args.force:
        skipped.append(gitignore)
    else:
        shutil.copyfile(TEMPLATES / "gitignore", gitignore)
        written.append(gitignore)

    for name in ROOT_FILES:
        target = repo / name
        if target.exists() and not args.force:
            skipped.append(target)
            continue
        shutil.copyfile(TEMPLATES / name, target)
        written.append(target)

    if not args.no_skills:
        skills_root = repo / ".claude" / "skills"
        for skill in SKILLS:
            target = skills_root / skill / "SKILL.md"
            if target.exists() and not args.force:
                skipped.append(target)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(TEMPLATES / "skills" / skill / "SKILL.md", target)
            written.append(target)

    for path in written:
        print(f"  wrote   {path.relative_to(repo)}")
    for path in skipped:
        print(f"  kept    {path.relative_to(repo)}  (exists; --force to replace)")

    print(f"\n{len(written)} written, {len(skipped)} kept, in {repo}")
    if written:
        print("\nNext:\n"
              "  1. `doc-publish doctor` - what is set up and what is not.\n"
              "  2. Write .doc-publish/prompt.md. It is the only file here that cannot\n"
              "     be generated, and the one that decides whether an agent represents\n"
              "     the document honestly. See .claude/skills/write-agent-prompt/.\n"
              "  3. `doc-publish check` - gates publishing on step 2.\n"
              "\nAGENTS.md at the repo root tells any coding agent - Cursor, Copilot,\n"
              "Codex, Claude - how to work in this document. Edit it freely; CLAUDE.md\n"
              "beside it only imports it, because Claude Code does not read AGENTS.md.")
    return 0


# -------------------------------------------------------------------------- check

def _defined_macros(repo: Path) -> tuple[set[str], set[str]]:
    """Macros this document defines, split into (needs an adapter, handled already).

    A zero-argument `\\newcommand` with a plain-text body is expanded by the flattener
    before the adapter is ever consulted, so reporting one as unhandled is noise — and
    noise in a check is worse than silence, because it trains you to skim past the real
    entries.

    The handled set comes from calling `literal_macros()` itself rather than re-deriving
    it from the pattern it uses. The two differ: the pattern matches the *shape* of a
    zero-argument definition, while the function additionally skips bodies containing
    commands or braces (`\\width` -> `0.95\\textwidth`, `\\reddot` -> a tikz picture),
    which it deliberately leaves alone as layout. Reusing the regex would quietly mark
    those as handled when the flattener never touches them — under-reporting, which is the
    worse failure for a check whose whole job is to notice what nobody has decided about.
    """
    from .ingest.flatten import literal_macros

    handled = set(literal_macros(repo))
    defined: set[str] = set()
    for path in list(repo.glob("*.sty")) + list(repo.glob("Preamble/*.tex")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.lstrip().startswith("%"):
                continue
            defined.update(MACRO_DEF.findall(line))
    return defined - handled, handled & defined


def check(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="doc-publish check",
        description="Report what is missing or unfinished in a document's contract.")
    ap.add_argument("--document-repo", default=None,
                    help="Document repo root (default: $DOC_REPO)")
    args = ap.parse_args(argv or [])

    repo = _document_repo(args)
    state = _state_dir(args)
    print(f"Contract for {repo}\n")

    if not state.is_dir():
        print(f"  MISSING  {config.STATE_DIRNAME}/ - run `doc-publish init`")
        return 1

    problems = 0

    for name in CONTRACT_FILES:
        path = state / name
        if not path.exists():
            print(f"  MISSING  {name} - run `doc-publish init`")
            problems += 1
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        todos = text.count(TODO)
        if todos:
            print(f"  UNFINISHED  {name} - {todos} [TODO:] marker(s) left")
            problems += 1
        else:
            print(f"  ok       {name}")

    # Macros the document defines, against those the adapter mentions.
    macros_path = state / "macros.py"
    if macros_path.exists():
        adapter = macros_path.read_text(encoding="utf-8", errors="replace")
        defined, literals = _defined_macros(repo)
        # Mentioned anywhere in the adapter counts as handled — including inside the
        # IGNORED set, which is how an author records "considered, needs no entry" for a
        # layout shorthand. Substring matching keeps that escape hatch cheap.
        unhandled = sorted(m for m in defined if m not in adapter)
        if unhandled:
            print(f"\n  {len(unhandled)} macro(s) taking arguments with no entry "
                  f"in macros.py:")
            for name in unhandled:
                print(f"      \\{name}")
            print("      Unhandled, a macro survives into the corpus as raw LaTeX and is\n"
                  "      quoted to a reader verbatim. Add an expansion, or name it in\n"
                  "      IGNORED to record that it deliberately needs none.")
            problems += 1
        elif defined:
            print(f"\n  ok       all {len(defined)} macro(s) taking arguments appear "
                  f"in macros.py")
        if literals:
            print(f"  ok       {len(literals)} zero-argument literal(s) expanded by the "
                  f"flattener, no entry needed")

    present = [n for n in STATE_FILES if (state / n).exists()]
    if present:
        print(f"\n  state    {', '.join(present)} present "
              f"(publisher-owned; do not edit by hand)")

    if problems:
        print(f"\n{problems} thing(s) to finish before publishing.")
        return 1
    print("\nContract complete.")
    return 0
