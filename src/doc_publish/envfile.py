"""Scaffold and audit the engine's own `.env`: doc-publish env

The bootstrap step before everything else. `doc-publish init` writes the contract into a
document repo, but you have to know `DOC_REPO` to get that far; this writes the file that
names it, and so must work with nothing configured at all.

Deliberately an explicit command rather than either of the two things it looks like:

*Not at install time.* Wheels do not run post-install code — setuptools' install hooks are
skipped for wheel installs and uv never runs them — so it would fire on exactly the
installs nobody performs. The install-time working directory is arbitrary anyway, which
makes writing into it a way to litter CI runners.

*Not on first call.* `config.load_env` searches **upward**, so a `.env` created in the
working directory silently shadows one a parent already provides — the precise failure
config.py exists to prevent. A command that writes files as a side effect of
`doc-publish config` also breaks the one property that command has: reporting how things
resolved without changing how they resolve.

An existing `.env` is never overwritten and there is no `--force`. It may hold
NOTION_TOKEN and ANTHROPIC_API_KEY, and no scaffolding is worth a flag that destroys
them; delete the file if you truly want a fresh one. What you get instead is the more
useful half — which documented settings it is missing, and which of its keys this engine
does not recognise.
"""
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from . import config

#: The shipped template. Lives inside the package because package data cannot reach
#: outside it; the repo-root `.env.example` is a copy, held identical by a test.
TEMPLATE = Path(__file__).resolve().parent / "templates" / "env.example"

ENV_FILENAME = ".env"


def template_keys() -> tuple[list[str], list[str]]:
    """(active, optional) setting names, in the order the template lists them.

    Active means uncommented: what a working install needs. Optional means commented
    out (`#DOC_STATE_DIR=`) — documented, but defaulted or gating one feature. The
    distinction is the template's own, so adding a setting there is the only edit
    needed to make this command report on it.
    """
    active: list[str] = []
    optional: list[str] = []
    for raw in TEMPLATE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            # Prose comments partition too ("public = comments stripped"), so require
            # the left side to look like a setting name rather than a sentence.
            key, sep, _ = line.lstrip("#").strip().partition("=")
            key = key.strip()
            if sep and key.isupper() and key.replace("_", "").isalnum():
                optional.append(key)
            continue
        key, sep, _ = line.partition("=")
        if sep:
            active.append(key.strip())
    return active, optional


def _modern_name(key: str) -> str | None:
    """The post-rename spelling of a legacy `THESIS_*` key, if it has one."""
    for modern, legacy in config._LEGACY_ALIASES.items():
        if key == legacy:
            return modern
    return "DOC_" + key[len("THESIS_"):] if key.startswith("THESIS_") else None


def _shadowed(directory: Path) -> Path | None:
    """An existing `.env` above `directory`, which a new one here would shadow."""
    base = directory.resolve()
    for parent in base.parents:
        candidate = parent / ENV_FILENAME
        if candidate.is_file():
            return candidate
    return None


def _audit(env_path: Path, active: list[str], optional: list[str]) -> int:
    """Report what an existing `.env` is missing or spells wrongly."""
    # config's parser, not a second one: it strips `export` and matching quotes, and a
    # report that disagreed with what the engine actually reads would be worse than none.
    values = config._parse_env_file(env_path)

    def provided_by(key: str) -> str | None:
        """The name actually setting `key` here — itself, or its pre-rename spelling.

        config.getenv falls back to the legacy name, so a file setting THESIS_REPO is
        not missing DOC_REPO. Reporting it as missing would send someone to add a
        setting the engine is already reading.
        """
        if key in values:
            return key
        legacy = config._legacy_name(key)
        return legacy if legacy and legacy in values else None

    missing = [key for key in active if provided_by(key) is None]
    for key in active:
        source = provided_by(key)
        if source is None:
            print(f"  MISSING  {key}")
        elif source == key:
            print(f"  ok       {key}")
        else:
            print(f"  ok       {key}  (as {source} - the pre-rename name; still read, "
                  f"but rename it when convenient)")

    absent_optional = [key for key in optional if provided_by(key) is None]
    if absent_optional:
        print(f"\n  {len(absent_optional)} optional setting(s) not set - defaulted or "
              f"feature-gating:\n      {', '.join(absent_optional)}")

    known = set(active) | set(optional)
    # A legacy spelling is reported against the key it feeds, above; listing it here too
    # would contradict that line.
    unknown = [key for key in values
               if key not in known and (_modern_name(key) or "") not in known]
    if unknown:
        print(f"\n  {len(unknown)} key(s) this engine does not recognise:")
        for key in unknown:
            print(f"      {key}")

    if missing:
        print(f"\n{len(missing)} required setting(s) missing. "
              f"`doc-publish config` shows how the rest resolved.")
        return 1
    print("\nEvery required setting is present. "
          "`doc-publish config` shows what each resolved to.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="doc-publish env",
        description="Write a .env from the shipped template, or audit the one already here.")
    ap.add_argument("--dir", default=None,
                    help="Where the .env lives (default: the working directory)")
    ap.add_argument("--check", action="store_true",
                    help="Report only; never write")
    # Not `argv or []`: the CLI hands us a list that is empty for a bare `doc-publish
    # env`, and collapsing that to the same value as None would make argparse fall back
    # to sys.argv either way. None means "read sys.argv", which is what keeps
    # `python -m doc_publish.envfile --dir X` working, as cli.py promises for every module.
    args = ap.parse_args(argv)

    directory = Path(args.dir).expanduser() if args.dir else Path.cwd()
    if not directory.is_dir():
        print(f"not a directory: {directory}")
        return 2

    env_path = directory / ENV_FILENAME
    active, optional = template_keys()

    if env_path.exists():
        print(f"{env_path} exists - not overwritten (it may hold tokens).\n")
        return _audit(env_path, active, optional)

    if args.check:
        print(f"no {ENV_FILENAME} in {directory} - run `doc-publish env` to write one")
        return 1

    shadowed = _shadowed(directory)
    shutil.copyfile(TEMPLATE, env_path)
    print(f"  wrote   {env_path}")
    if shadowed:
        # Settings resolve from the nearest .env above the working directory, so the new
        # file wins over the old one for anything it names - including the commented-out
        # settings, which name nothing and so change nothing. Worth saying out loud.
        print(f"\nnote: {shadowed} already serves this tree. The new file takes "
              f"precedence\n      for every setting it sets.")
    print(f"\nNext: fill in DOC_REPO - the path to the LaTeX document repo - then run\n"
          f"`doc-publish config` to see how every setting resolved and from where.\n"
          f"Never commit this file: it names private paths and may carry tokens.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
