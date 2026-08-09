"""Scaffold and validate the per-document contract in `.thesis-agent/`.

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


def _document_repo(args) -> Path:
    return Path(args.document_repo).expanduser() if args.document_repo else config.thesis_repo()


def _state_dir(args) -> Path:
    repo = _document_repo(args)
    return repo / config.STATE_DIRNAME


# --------------------------------------------------------------------------- init

def init(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="thesis-agent init",
        description="Write the contract skeletons into a document repo.")
    ap.add_argument("--document-repo", default=None,
                    help="Document repo root (default: $THESIS_REPO)")
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
        print("\nNext: write .thesis-agent/prompt.md. It is the only file here that\n"
              "cannot be generated, and the one that decides whether the agent\n"
              "represents the document honestly. Then run `thesis-agent check`.")
    return 0


# -------------------------------------------------------------------------- check

def _defined_macros(repo: Path) -> set[str]:
    """Macro names this document defines in its own .sty / preamble sources."""
    names: set[str] = set()
    for path in list(repo.glob("*.sty")) + list(repo.glob("Preamble/*.tex")):
        text = path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            if line.lstrip().startswith("%"):
                continue
            names.update(MACRO_DEF.findall(line))
    return names


def check(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="thesis-agent check",
        description="Report what is missing or unfinished in a document's contract.")
    ap.add_argument("--document-repo", default=None,
                    help="Document repo root (default: $THESIS_REPO)")
    args = ap.parse_args(argv or [])

    repo = _document_repo(args)
    state = _state_dir(args)
    print(f"Contract for {repo}\n")

    if not state.is_dir():
        print(f"  MISSING  {config.STATE_DIRNAME}/ — run `thesis-agent init`")
        return 1

    problems = 0

    for name in CONTRACT_FILES:
        path = state / name
        if not path.exists():
            print(f"  MISSING  {name} — run `thesis-agent init`")
            problems += 1
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        todos = text.count(TODO)
        if todos:
            print(f"  UNFINISHED  {name} — {todos} [TODO:] marker(s) left")
            problems += 1
        else:
            print(f"  ok       {name}")

    # Macros the document defines, against those the adapter mentions.
    macros_path = state / "macros.py"
    if macros_path.exists():
        adapter = macros_path.read_text(encoding="utf-8", errors="replace")
        defined = _defined_macros(repo)
        # Mentioned anywhere in the adapter counts as handled — including inside the
        # IGNORED set, which is how an author records "considered, needs no entry" for a
        # layout shorthand. Substring matching keeps that escape hatch cheap.
        unhandled = sorted(m for m in defined if m not in adapter)
        if unhandled:
            print(f"\n  {len(unhandled)} macro(s) defined by the document with no entry "
                  f"in macros.py:")
            for name in unhandled:
                print(f"      \\{name}")
            print("      Unhandled, a macro survives into the corpus as raw LaTeX and is\n"
                  "      quoted to a reader verbatim. Add an expansion, or name it in\n"
                  "      IGNORED to record that it deliberately needs none.")
            problems += 1
        elif defined:
            print(f"\n  ok       all {len(defined)} document macro(s) appear in macros.py")

    present = [n for n in STATE_FILES if (state / n).exists()]
    if present:
        print(f"\n  state    {', '.join(present)} present "
              f"(publisher-owned; do not edit by hand)")

    if problems:
        print(f"\n{problems} thing(s) to finish before publishing.")
        return 1
    print("\nContract complete.")
    return 0
