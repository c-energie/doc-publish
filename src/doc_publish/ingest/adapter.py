"""Optional per-document macro adapter.

Package-level conventions - `\\acrshort`, `\\gls`, `\\cite`, `\\cref`,
`\\ExecuteMetaData`, `\\graphicspath` - are the same in every LaTeX document and stay in
the flattener. What differs per document is its own `\\newcommand`s, and those resist a
config schema: the one this was written against renders `\\ptg{max}{solar,wind}` as
`PTG^max_{solar,additive-wind}`, validating both argument positions against separate key
tables. Expressing that declaratively means inventing a category from a single example.

So a document may ship `macros.py` in its state directory, exporting either hook:

    def expand(text: str, vocab: Vocab, unresolved: list[str]) -> str
        Rewrite the document's own macros. Called once, at the start of stage 4, before
        acronyms and citations. Append to `unresolved` for any key you cannot resolve
        and return an `[UNRESOLVED: ...]` marker in its place, exactly as the flattener
        does - that is what makes the build exit non-zero instead of publishing a
        half-expanded corpus.

    def vocabulary() -> list[str]
        Markdown lines defining that notation, appended to the corpus vocabulary block
        after Symbols. Include your own `###` heading.

Four rules keep this from becoming a liability:

- **One hook, one stage.** The adapter never sees subfile inlining or comment splitting,
  so it cannot disturb the ordering that makes `\\ExecuteMetaData` resolve correctly.
- **Pure text -> text.** No I/O, no clock, no randomness. The Notion stream hashes
  rendered content to decide what to write, so an adapter that is not deterministic
  turns every sync into a full rewrite.
- **Report, never drop.** Unresolved keys go through `unresolved` like everything else.
- **A broken adapter is fatal.** No file at all is fine and means the document uses no
  bespoke macros. A file that fails to import raises: silently skipping it would leave
  raw `\\ptg{...}` in the corpus and publish it.

Note that this imports and executes Python from the document repo. That is the same
trust already extended to a Makefile or a conftest.py in a repo you control - but it is
worth knowing before pointing the engine at a repo you do not.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

FILENAME = "macros.py"


class AdapterError(RuntimeError):
    """The document's macros.py exists but could not be loaded or does not fit."""


def load(state_dir: Path) -> ModuleType | None:
    """Import `<state_dir>/macros.py`, or return None if the document has none."""
    path = state_dir / FILENAME
    if not path.exists():
        return None

    spec = importlib.util.spec_from_file_location("doc_publish._document_macros", path)
    if spec is None or spec.loader is None:
        raise AdapterError(f"{path} could not be loaded as a Python module")
    module = importlib.util.module_from_spec(spec)
    # Don't leave a __pycache__ behind. The document repo is an input the engine only
    # reads; dropping bytecode into it puts an untracked directory in someone's LaTeX
    # checkout, which then syncs to Overleaf or shows up in git status.
    previous = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec.loader.exec_module(module)
    except Exception as exc:                                  # noqa: BLE001
        raise AdapterError(f"{path} failed to import: {exc}") from exc
    finally:
        sys.dont_write_bytecode = previous

    for hook in ("expand", "vocabulary"):
        if hasattr(module, hook) and not callable(getattr(module, hook)):
            raise AdapterError(f"{path}: {hook} is defined but is not callable")
    if not hasattr(module, "expand") and not hasattr(module, "vocabulary"):
        raise AdapterError(
            f"{path} defines neither expand() nor vocabulary() - delete it, or see "
            f"doc_publish/ingest/adapter.py for the contract")
    return module


def load_for(repo: Path) -> ModuleType | None:
    """Locate the state directory for `repo` and load its adapter, if any."""
    from .. import config
    try:
        state_dir = config.state_dir()
    except config.ConfigError:
        # flatten() is usable standalone, without the environment configured.
        state_dir = repo / config.STATE_DIRNAME
    return load(state_dir)


def expand(module: ModuleType | None, text: str, vocab, unresolved: list[str]) -> str:
    if module is None or not hasattr(module, "expand"):
        return text
    result = module.expand(text, vocab, unresolved)
    if not isinstance(result, str):
        raise AdapterError(
            f"{FILENAME}: expand() returned {type(result).__name__}, expected str")
    return result


def vocabulary(module: ModuleType | None) -> list[str]:
    if module is None or not hasattr(module, "vocabulary"):
        return []
    lines = module.vocabulary()
    if not isinstance(lines, list) or not all(isinstance(x, str) for x in lines):
        raise AdapterError(f"{FILENAME}: vocabulary() must return a list of strings")
    return lines
