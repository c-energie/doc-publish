"""Every path and secret the engine uses, resolved in one place.

Before this module the paths were module-level constants like `Path("build")` and
`Path("publish/state")`, which quietly assumed the process had been started from the
repo root. That is fine for `python -m ingest.build` in a checkout and wrong the moment
the package is installed and driven from somewhere else, which is now the normal case:
the entry points run from the thesis-publish working copy.

Resolution order for every setting is the same - real environment variable, then `.env`,
then a default - so CI can set variables directly (the site workflow does) without a
`.env` existing at all, and a local `.env` never silently overrides an explicit export.

Nothing here guesses. A missing or implausible setting raises `ConfigError` with the
variable name and what it should point at; the CLI turns that into exit code 2. The old
`Path(os.environ.get("DOC_REPO", ""))` resolved to the current directory when unset
and failed much later, deep in the flattener, with a bare filename in the message.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

__all__ = ["ConfigError", "load_env", "getenv", "document_repo", "resolve_document",
           "set_document", "Resolved", "main_tex", "state_dir",
           "state_dir_for", "build_dir", "site_dir", "site_build_dir", "dist_dir",
           "vendor_dir", "publish_repo", "corpus_mode", "source_label", "title", "require",
           "describe"]

#: Filenames that identify a LaTeX document root, used to sanity-check DOC_REPO.
#: An existing document is as likely to be `thesis.tex` or `dissertation.tex` as
#: `main.tex`, and renaming someone's root file to adopt this tooling is a poor trade —
#: so DOC_MAIN_TEX names it, and these are only the fallbacks tried in order.
_DOC_ROOTS = ("main.tex", "thesis.tex", "dissertation.tex", "report.tex", "book.tex")

#: Directory name for publishing state, relative to the document repo.
STATE_DIRNAME = ".doc-publish"

#: What the state directory was called before this tool was renamed from thesis-agent.
#: Still read when it is the only one present; see state_dir().
LEGACY_STATE_DIRNAME = ".thesis-agent"

#: The project-workspace manifest, owned by claude-tools' project-workspace plugin. Read
#: here, never written: which documents an analysis feeds is that plugin's to record.
WORKSPACE_FILE = Path(".claude") / "workspace.json"


class ConfigError(RuntimeError):
    """A required setting is missing, or points somewhere that cannot be what was meant."""


def _parse_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # `export FOO=bar` is common in files shared with a POSIX shell.
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, sep, value = line.partition("=")
        if not sep:
            continue
        value = value.strip()
        # Strip one matching pair of quotes; a Windows path with spaces needs them, and
        # leaving them in produces a path that fails an exists() check for no visible reason.
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        out[key.strip()] = value
    return out


def load_env(start: Path | None = None) -> Path | None:
    """Merge the nearest `.env` into `os.environ` without overwriting existing values.

    Looked for at `$DOC_ENV` if set, otherwise by walking up from `start`
    (default: the current directory). Returns the file used, or None.

    Called once on import. Values already in the environment win, which is what makes
    the CI workflow work unchanged.
    """
    explicit = os.environ.get("DOC_ENV")
    if explicit:
        path = Path(explicit)
        if not path.is_file():
            raise ConfigError(f"DOC_ENV={path} is not a file")
        candidates = [path]
    else:
        base = (start or Path.cwd()).resolve()
        candidates = [p / ".env" for p in (base, *base.parents)]

    for candidate in candidates:
        if candidate.is_file():
            for key, value in _parse_env_file(candidate).items():
                if key not in os.environ:
                    os.environ[key] = value
                    _from_env_file[key] = candidate
            return candidate
    return None


#: Which settings load_env() filled in, and from which file - so `doc-publish config` can
#: say that DOC_REPO came from a .env two directories up rather than from the shell.
_from_env_file: dict[str, Path] = {}


#: This was thesis-agent, and every setting was THESIS_*. A rename that silently stops
#: reading someone's configured environment breaks their setup mid-task, so the old names
#: are still honoured — with a note, once per name, saying what replaced them.
_LEGACY_ALIASES = {"DOC_ENV": "THESIS_AGENT_ENV"}
_warned: set[str] = set()


def _legacy_name(name: str) -> str | None:
    """The pre-rename spelling of `name`, if it had one."""
    if name in _LEGACY_ALIASES:
        return _LEGACY_ALIASES[name]
    return "THESIS_" + name[len("DOC_"):] if name.startswith("DOC_") else None


def getenv(name: str, default: str = "") -> str:
    """os.environ.get, falling back to the pre-rename name and saying so once."""
    value = os.environ.get(name, "").strip()
    if value:
        return value

    legacy = _legacy_name(name)
    if legacy:
        value = os.environ.get(legacy, "").strip()
        if value:
            if legacy not in _warned:
                _warned.add(legacy)
                print(f"note: {legacy} is the old name for {name} - still honoured, "
                      f"but rename it when convenient.")
            return value
    return default


def require(name: str, what: str) -> str:
    value = getenv(name)
    if not value:
        legacy = _legacy_name(name)
        also = f" (or its former name {legacy})" if legacy else ""
        raise ConfigError(f"{name}{also} is not set - it should be {what}")
    return value


@dataclass(frozen=True)
class Resolved:
    """The document, and which step of the resolution order found it: `route` is one of
    "--document", "DOC_REPO", "walk-up" or "workspace"; `source` says it in a sentence."""
    path: Path
    source: str
    route: str


#: Set by the CLI's `--document <dir>`; outranks everything, DOC_REPO included.
_explicit_document: Path | None = None


def set_document(path: str | Path | None) -> None:
    """Name the document explicitly, as `--document <dir>` does. None clears it."""
    global _explicit_document
    _explicit_document = Path(path).expanduser() if path is not None else None


def document_repo() -> Path:
    """The LaTeX document repo. An input, never written to by ingest."""
    return resolve_document().path


def resolve_document() -> Resolved:
    """Find the one document, in this order:

      1. `--document <dir>`, then DOC_REPO (or THESIS_REPO);
      2. the nearest document root at or above the current directory - running inside a
         document, or inside an analysis nested in one;
      3. `.claude/workspace.json`: the documents a workspace analysis feeds, or from the
         workspace root its `kind: "document"` members.

    Steps 2-3 still run when DOC_REPO is set, because DOC_REPO naming a *different*
    document from the one the structure describes is a stale .env, and building the wrong
    document - then syncing it over the right one's wiki - is not something to do quietly
    (analysis-template ADR 0003). When they agree nothing is said. `--document` is the
    deliberate override and is not checked against anything but its root .tex.
    """
    if _explicit_document is not None:
        return Resolved(_checked(_explicit_document, "--document"), "--document",
                        "--document")

    found = _find_by_structure(Path.cwd().resolve())
    named = getenv("DOC_REPO")
    if named:
        path = Path(named).expanduser()
        if found and found.candidates and not any(_same(path, c) for c in found.candidates):
            them = ", ".join(str(c) for c in found.candidates)
            raise ConfigError(
                f"DOC_REPO={path} is not the document {found.how} ({them}). One of them "
                f"is stale: fix or unset DOC_REPO, or pass --document <dir> to choose.")
        return Resolved(_checked(path, "DOC_REPO"), _doc_repo_source(), "DOC_REPO")

    if found is None:
        # Names both spellings, as require() always has, and every place that was looked.
        raise ConfigError(
            f"DOC_REPO (or its former name {_legacy_name('DOC_REPO')}) is not set, and no "
            f"document was found at or above {Path.cwd()} nor through a "
            f"{WORKSPACE_FILE.as_posix()}. Set DOC_REPO to the LaTeX document repo, run "
            f"from inside the document, or pass --document <dir>.")
    if not found.candidates:
        raise ConfigError(f"{found.empty} Attach it to a document, set DOC_REPO, or pass "
                          f"--document <dir>.")
    if len(found.candidates) > 1:
        listed = "".join(f"\n  {c}" for c in found.candidates)
        raise ConfigError(f"There are {len(found.candidates)} documents {found.how}:"
                          f"{listed}\nPass --document <dir> to choose one.")
    path = found.candidates[0]
    return Resolved(_checked(path, f"the document {found.how}"), found.how, found.route)


def _doc_repo_source() -> str:
    name = "DOC_REPO" if os.environ.get("DOC_REPO", "").strip() else _legacy_name("DOC_REPO")
    origin = _from_env_file.get(name)
    return f"{name} (from {origin})" if origin else f"{name} (from the environment)"


def _same(a: Path, b: Path) -> bool:
    # normcase: on Windows C:\Thesis and c:\thesis are one directory.
    return os.path.normcase(str(a.resolve())) == os.path.normcase(str(b.resolve()))


def _checked(path: Path, who: str) -> Path:
    """`path`, if it holds the root .tex; otherwise a ConfigError saying who named it."""
    named = getenv("DOC_MAIN_TEX")
    # `DOC_REPO=<path>` reads as the setting it is; a found document reads as a sentence.
    at = f"{who}={path}" if who in ("DOC_REPO", "--document") else f"{who} ({path})"
    if named:
        if not (path / named).exists():
            raise ConfigError(f"DOC_MAIN_TEX={named} does not exist in {at}")
    elif not any((path / root).exists() for root in _DOC_ROOTS):
        raise ConfigError(
            f"{at} does not look like a LaTeX document repo - none of "
            f"{', '.join(_DOC_ROOTS)} is there. If your root file has another name, "
            f"set DOC_MAIN_TEX to it.")
    return path


def _is_document_root(d: Path) -> bool:
    named = getenv("DOC_MAIN_TEX")
    if named:
        return (d / named).is_file()
    return any((d / root).is_file() for root in _DOC_ROOTS)


@dataclass(frozen=True)
class _Found:
    """What the project structure says the document is. `candidates` may hold several
    (ambiguous) or none (an analysis that feeds nothing yet, described by `empty`)."""
    candidates: tuple[Path, ...]
    how: str
    route: str
    empty: str = ""


def _find_by_structure(here: Path) -> _Found | None:
    """Steps 2 and 3 of resolve_document(), or None when the structure says nothing."""
    workspace = _find_workspace(here)
    # A document root is a directory holding its root .tex - not necessarily a git root,
    # just as DOC_REPO has never had to be one. The walk stops at the workspace root: a
    # stray main.tex in the directory *holding* a workspace is not this workspace's
    # document.
    stop = workspace[0] if workspace else None
    for d in (here, *here.parents):
        if _is_document_root(d):
            return _Found((d,), f"found at or above {here}", "walk-up")
        if d == stop:
            break
    if workspace is None:
        return None
    root, file, members = workspace

    def under(d: str) -> Path:
        return root / d.strip("/")

    if here != root:
        # The deepest analysis member holding the current directory, so a notebook dir
        # inside an analysis resolves as the analysis does.
        mine = [m for m in members if m.get("kind") == "analysis"
                and _is_within(here, under(m["dir"]))]
        if not mine:
            return None
        member = max(mine, key=lambda m: len(under(m["dir"]).parts))
        documents = member.get("documents", [])
        if not isinstance(documents, list) or not all(isinstance(d, str) for d in documents):
            raise ConfigError(f"{file}: `documents` of analysis member {member['dir']!r} "
                              f"must be a list of document dirs, not {documents!r}")
        where = f"workspace analysis {member['dir'].strip('/')}/ feeds (in {file})"
        return _Found(tuple(under(d) for d in documents), where, "workspace",
                      empty=f"Workspace analysis {member['dir'].strip('/')}/ feeds no "
                            f"document yet: its `documents` in {file} is empty.")

    docs = tuple(under(m["dir"]) for m in members if m.get("kind") == "document")
    if not docs:
        return None
    return _Found(docs, f"listed as document members of {file}", "workspace")


def _find_workspace(start: Path) -> tuple[Path, Path, list[dict]] | None:
    """(root, file, members) of the nearest .claude/workspace.json at or above `start`.

    The same rules as context-kit-core's findWorkspace() and project-workspace's port of
    it: nearest wins and levels never merge; the walk does not stop at a `.git`, because
    members are nested clones; and a malformed nearest file - a directory of that name
    included - is an error, never skipped past, since a typo here must not silently load
    an outer workspace's documents. Unknown keys and member fields are other owners'
    business and are ignored.
    """
    for d in (start, *start.parents):
        file = d / WORKSPACE_FILE
        if not file.exists():
            continue
        try:
            data = json.loads(file.read_text(encoding="utf-8").removeprefix("\ufeff"))
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            raise ConfigError(f"{file} is malformed ({str(exc).splitlines()[0]}) - fix it, "
                              f"or pass --document <dir>") from None
        if not isinstance(data, dict):
            raise ConfigError(f"{file} is malformed (top level is not a JSON object)")
        members = data.get("members", [])
        if not isinstance(members, list):
            raise ConfigError(f"{file}: `members` must be a list, not {type(members).__name__}")
        # A member with no dir cannot be placed on disk, so cannot be a match either;
        # project-workspace skips them the same way.
        return d, file, [m for m in members
                         if isinstance(m, dict) and isinstance(m.get("dir"), str) and m["dir"]]
    return None


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def main_tex(repo: Path | None = None) -> str:
    """Filename of the document's root .tex, relative to the document repo.

    DOC_MAIN_TEX wins; otherwise the first of _DOC_ROOTS that exists. Returns the first
    fallback name when none is present, so callers still produce a sensible error
    naming the file they looked for rather than an empty one.
    """
    named = getenv("DOC_MAIN_TEX")
    if named:
        return named
    root = Path(repo) if repo is not None else document_repo()
    return next((name for name in _DOC_ROOTS if (root / name).exists()), _DOC_ROOTS[0])


def state_dir(create: bool = False) -> Path:
    """Publishing state: page ids, anchor map, signposts, the project prompt.

    Defaults to `<DOC_REPO>/.doc-publish` and is *committed there*, deliberately.
    The manifests are keyed to labels in that repo, so co-locating them means an older
    checkout carries matching state rather than drifting; and the reports quote excluded
    source comments, which keeps them out of this repo now that it is public.

    Override with DOC_STATE_DIR when the document repo is one you cannot commit to.

    A document set up before the rename keeps its `.thesis-agent/` directory, and that is
    honoured rather than ignored. This is the one piece of the rename that could destroy
    something: losing notion_manifest.json makes the next sync create a *second copy of
    the whole wiki*, orphaning the existing pages and every link into them. Silently
    looking in the new place and finding nothing would do exactly that, so the old
    directory wins whenever it exists and the new one does not.
    """
    override = getenv("DOC_STATE_DIR")
    path = Path(override).expanduser() if override else state_dir_for(document_repo())
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def state_dir_for(repo: Path) -> Path:
    """The state directory inside `repo`, honouring a pre-rename one.

    Separate from state_dir() so every caller that already knows which repo it means —
    `init` and `check` take an explicit --document-repo — resolves it the same way. A
    caller that reimplements `repo / STATE_DIRNAME` skips the fallback and reports a
    migrated document as unconfigured.
    """
    path = repo / STATE_DIRNAME
    if path.exists():
        return path
    legacy = repo / LEGACY_STATE_DIRNAME
    if legacy.is_dir():
        if "state_dir" not in _warned:
            _warned.add("state_dir")
            print(f"note: using {LEGACY_STATE_DIRNAME}/ (pre-rename). To migrate, "
                  f"`git mv {LEGACY_STATE_DIRNAME} {STATE_DIRNAME}` in the document "
                  f"repo — commit it, do not delete it.")
        return legacy
    return path


def build_dir(create: bool = False) -> Path:
    """Derived corpora and figure copies. Never committed anywhere - it holds the draft
    build, with source comments, provisional numbers and open supervisor questions."""
    override = os.environ.get("DOC_BUILD_DIR", "").strip()
    path = Path(override).expanduser() if override else Path.cwd() / "build"
    if create:
        if not override:
            _refuse_an_unignored_build_inside_the_document(path)
        path.mkdir(parents=True, exist_ok=True)
    return path


def _refuse_an_unignored_build_inside_the_document(build: Path) -> None:
    """Stop `<cwd>/build` from being created, unignored, in the document's own tree.

    Before the document could be found by walking up, the build ran from a publish repo
    that ignores build/. Run from inside the document, the default puts corpus_draft.md -
    source comments, provisional numbers, open questions - into the repo Overleaf syncs,
    one `git add .` from being shared. Only that route is checked: with DOC_REPO set the
    working directory was always the user's choice, and is left as it was. And only on
    create, which is how `build` writes it; a reader of an existing build/ adds no risk.
    """
    try:
        found = resolve_document()
    except ConfigError:
        return
    if found.route != "walk-up":
        return
    try:
        # A path inside build/, so a directory-only `build/` pattern matches before the
        # directory exists. 0 = ignored, 1 = not ignored, 128 = not in a git repo.
        code = subprocess.run(
            ["git", "-C", str(build.parent), "check-ignore", "-q",
             f"{build.name}/corpus_draft.md"],
            capture_output=True).returncode
    except OSError:
        code = None
    if code in (0, 128):
        return
    raise ConfigError(
        f"the build dir {build} would sit inside the document ({found.path}) and git does "
        f"not ignore it - the draft corpus would be one commit from Overleaf. Add `build/` "
        f"to that repo's .gitignore, set DOC_BUILD_DIR, or run from outside the document.")


def _output(env: str, default: Path, create: bool = False) -> Path:
    override = os.environ.get(env, "").strip()
    path = Path(override).expanduser() if override else default
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def site_dir(create: bool = False) -> Path:
    """The generated Quarto project (`.qmd` sources). A build product.

    Under the build directory, not the working directory: the entry points are meant to
    be driven from the publish repo, and `<cwd>/site` is exactly where `doc-publish
    publish` writes - the render would have been copying over its own sources.
    """
    return _output("DOC_SITE_DIR", build_dir() / "site", create)


def site_build_dir(create: bool = False) -> Path:
    """Rendered HTML. What gets copied into the publish repo, zipped, or served."""
    return _output("DOC_SITE_BUILD_DIR", build_dir() / "site_build", create)


def dist_dir(create: bool = False) -> Path:
    """Zipped bundles for hand-carried distribution."""
    return _output("DOC_DIST_DIR", Path.cwd() / "dist", create)


def vendor_dir(create: bool = False) -> Path:
    """Download cache for vendored MathJax. Under the build directory because the
    package may be installed read-only in site-packages, where the old
    `publish/quarto/_vendor` path is not writable."""
    path = build_dir() / "_vendor"
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def source_label() -> str:
    """How the source document is named in generated prose ("Rendered from ...").
    Defaults to the document repo's directory name."""
    return os.environ.get("DOC_SOURCE_LABEL", "").strip() or document_repo().name


def title() -> str:
    """The document's own title, for the corpus header and the site index.

    These used to read "Thesis" whatever the document was — harmless for a thesis and
    wrong for anything else, in output a reader sees. DOC_TITLE sets it; otherwise the
    root .tex's \\title{...} is used, falling back to the source label.
    """
    explicit = getenv("DOC_TITLE")
    if explicit:
        return explicit
    repo = document_repo()
    try:
        raw = (repo / main_tex(repo)).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return source_label()
    # Only a literal \title{...}; a title assembled from macros is not worth guessing at.
    m = re.search(r"\\title\s*\{([^{}]+)\}", raw)
    return m.group(1).strip() if m else source_label()


def publish_repo() -> Path:
    """The repo that receives the published site. Checked for `.git` because a typo
    here scatters a few hundred files into whatever directory was named."""
    path = Path(require(
        "DOC_PUBLISH_REPO", "the path to the repo that hosts the published site")).expanduser()
    if not path.is_dir():
        raise ConfigError(f"DOC_PUBLISH_REPO={path} does not exist")
    if not (path / ".git").exists():
        raise ConfigError(
            f"DOC_PUBLISH_REPO={path} is not a git repo - refusing to write a site "
            f"into a directory that cannot be reviewed as a diff")
    return path


def corpus_mode() -> str:
    """`public` or `draft`. Anything else is a typo that would otherwise serve nothing."""
    mode = os.environ.get("CORPUS_MODE", "public").strip() or "public"
    if mode not in ("public", "draft"):
        raise ConfigError(f"CORPUS_MODE={mode!r} must be 'public' or 'draft'")
    return mode


def describe() -> str:
    """Human-readable resolution of every setting, for `doc-publish config`."""
    lines = [f".env: {_ENV_FILE or '(none found)'}"]
    try:
        found = resolve_document()
        lines.append(f"document: {found.path} - {found.source}")
    except ConfigError as exc:
        lines.append(f"document: unresolved - {exc}")
    for label, fn in (("state dir", state_dir),
                      ("build dir", build_dir), ("DOC_PUBLISH_REPO", publish_repo),
                      ("CORPUS_MODE", corpus_mode)):
        try:
            lines.append(f"{label}: {fn()}")
        except ConfigError as exc:
            lines.append(f"{label}: unset - {exc}")
    for name in ("ANTHROPIC_API_KEY", "NOTION_TOKEN", "DOC_NOTION_PARENT"):
        lines.append(f"{name}: {'set' if os.environ.get(name) else 'unset'}")
    return "\n".join(lines)


_ENV_FILE = load_env()
