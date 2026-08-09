"""Scaffolding and validating a document's contract.

`init` and `check` are the engine's answer to a contract that used to be hand-copied into
every document repo. The tests that matter here are the ones about *drift*: that a
scaffolded document is immediately valid, that the skeletons the engine ships actually
satisfy the engine's own loader, and that `check` notices the two ways a document goes
wrong quietly — an unfinished prompt, and a macro nobody taught the adapter about.
"""
import json

import pytest

from doc_publish import config, contract
from doc_publish.ingest import adapter


@pytest.fixture
def document(tmp_path):
    """A minimal document repo: a root LaTeX file and a preamble defining one macro."""
    (tmp_path / "main.tex").write_text(
        "\\documentclass{report}\n\\begin{document}\\end{document}\n", encoding="utf-8")
    (tmp_path / "document_settings.sty").write_text(
        "\\ProvidesPackage{document_settings}\n"
        "\\newcommand{\\width}{0.95\\textwidth}\n", encoding="utf-8")
    return tmp_path


def init(document, *args):
    return contract.init(["--document-repo", str(document), *args])


def check(document):
    return contract.check(["--document-repo", str(document)])


# --------------------------------------------------------------------------- init

def test_init_writes_the_contract(document):
    assert init(document) == 0
    state = document / config.STATE_DIRNAME
    for name in contract.CONTRACT_FILES:
        assert (state / name).exists(), name
    assert (state / ".gitignore").exists()


def test_init_writes_the_authoring_skills(document):
    init(document)
    for skill in contract.SKILLS:
        assert (document / ".claude" / "skills" / skill / "SKILL.md").exists()


def test_init_can_skip_the_skills(document):
    init(document, "--no-skills")
    assert not (document / ".claude").exists()


def test_init_does_not_clobber_authored_work(document):
    """The prompt is hand-written and expensive; a second init must not overwrite it."""
    init(document)
    prompt = document / config.STATE_DIRNAME / "prompt.md"
    prompt.write_text("# Document\nMine.\n", encoding="utf-8")

    init(document)
    assert prompt.read_text(encoding="utf-8") == "# Document\nMine.\n"

    init(document, "--force")
    assert prompt.read_text(encoding="utf-8") != "# Document\nMine.\n"


def test_init_rejects_a_path_that_is_not_a_directory(tmp_path):
    assert contract.init(["--document-repo", str(tmp_path / "nope")]) == 2


# -------------------------------------------------------------------------- check

def test_check_fails_before_init(document):
    assert check(document) == 1


def test_check_fails_on_an_unfinished_prompt(document, capsys):
    init(document)
    assert check(document) == 1
    assert "prompt.md" in capsys.readouterr().out


def test_check_passes_once_the_prompt_is_written(document):
    init(document)
    state = document / config.STATE_DIRNAME
    prompt = state / "prompt.md"
    prompt.write_text(prompt.read_text(encoding="utf-8").replace(contract.TODO, "done:"),
                      encoding="utf-8")
    assert check(document) == 0


def _finish_prompt(document):
    state = document / config.STATE_DIRNAME
    prompt = state / "prompt.md"
    prompt.write_text(prompt.read_text(encoding="utf-8").replace(contract.TODO, "done:"),
                      encoding="utf-8")
    return state


def test_check_flags_a_macro_the_adapter_never_heard_of(document, capsys):
    """The quiet failure: unhandled, a macro reaches a reader as raw LaTeX."""
    init(document)
    _finish_prompt(document)
    with (document / "document_settings.sty").open("a", encoding="utf-8") as f:
        f.write("\\newcommand{\\keyterm2}[1]{\\textsc{#1}}\n")

    assert check(document) == 1
    assert "keyterm2" in capsys.readouterr().out


def test_ignored_silences_a_layout_shorthand(document, capsys):
    """\\width is sizing, not meaning. Naming it records the judgement, not a suppression."""
    init(document)
    _finish_prompt(document)
    assert check(document) == 0
    assert "width" not in capsys.readouterr().out.split("ok       all")[-1]


def test_zero_argument_literals_are_not_reported(document, capsys):
    """The flattener expands these before the adapter runs, so flagging them is noise."""
    init(document)
    _finish_prompt(document)
    with (document / "document_settings.sty").open("a", encoding="utf-8") as f:
        f.write("\\newcommand{\\nmodels}{40}\n")

    assert check(document) == 0
    out = capsys.readouterr().out
    assert "nmodels" not in out
    assert "zero-argument literal" in out


def test_a_macro_with_arguments_is_still_reported(document, capsys):
    """The distinction is the [n], not the name: same document, opposite verdict."""
    init(document)
    _finish_prompt(document)
    with (document / "document_settings.sty").open("a", encoding="utf-8") as f:
        f.write("\\newcommand{\\nmodels}[1]{model #1}\n")

    assert check(document) == 1
    assert "nmodels" in capsys.readouterr().out


def test_check_ignores_commented_out_definitions(document):
    init(document)
    _finish_prompt(document)
    with (document / "document_settings.sty").open("a", encoding="utf-8") as f:
        f.write("% \\newcommand{\\notreal}[1]{#1}\n")
    assert check(document) == 0


# ------------------------------------------------- the skeletons must actually work

def test_the_shipped_adapter_loads_under_the_engines_own_loader(document):
    """The template's macros.py must satisfy adapter.load(), not merely look plausible."""
    init(document)
    module = adapter.load(document / config.STATE_DIRNAME)
    assert module is not None


def test_the_shipped_adapter_has_the_signature_the_flattener_calls(document):
    """Regression: the skeleton once used expand(text) and died on the real call."""
    init(document)
    module = adapter.load(document / config.STATE_DIRNAME)
    unresolved: list[str] = []
    out = adapter.expand(module, r"a \keyterm{example} b", None, unresolved)
    assert out == "a example term b"       # TERMS["example"]
    assert unresolved == []


def test_the_shipped_adapter_reports_rather_than_invents(document):
    """An unknown key must be reported and fall back, never guessed at."""
    init(document)
    module = adapter.load(document / config.STATE_DIRNAME)
    unresolved: list[str] = []
    out = adapter.expand(module, r"\keyterm{nosuchterm}", None, unresolved)
    assert "nosuchterm" in out
    assert any("nosuchterm" in problem for problem in unresolved)


def test_the_shipped_database_settings_are_valid_json(document):
    init(document)
    settings = json.loads(
        (document / config.STATE_DIRNAME / "database_settings.json")
        .read_text(encoding="utf-8"))
    for name, spec in settings.items():
        if name.startswith("_"):
            continue
        assert "properties" in spec, name
        titles = [k for k, v in spec["properties"].items() if "title" in v]
        assert len(titles) == 1, f"{name}: a Notion database needs exactly one title column"


def test_generated_state_is_gitignored_by_the_skeleton(document):
    """Committing anchor_map.json from a template would publish one document's URLs."""
    init(document)
    ignored = (document / config.STATE_DIRNAME / ".gitignore").read_text(
        encoding="utf-8")
    for name in contract.STATE_FILES:
        assert name in ignored
