"""How settings resolve, and how they fail.

Configuration here is almost all environment variables pointing at directories, which is
the kind of thing that goes wrong far from where it was set. These tests pin the two
properties that make that bearable: an unset or wrong value fails with a message naming
what it should have been, and a `.env` never silently overrides something already in the
environment (which is what lets CI pass values in unchanged).
"""
import pytest

from doc_publish import config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts from a known-empty environment for the vars config reads."""
    for name in ("DOC_REPO", "DOC_STATE_DIR", "DOC_ENV",
                 "DOC_PUBLISH_REPO", "DOC_CORPUS_MODE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def document(tmp_path):
    (tmp_path / "main.tex").write_text("\\documentclass{report}\n", encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------- required values

def test_require_names_what_was_missing():
    with pytest.raises(config.ConfigError) as excinfo:
        config.require("NOT_SET_ANYWHERE", "the path to something")
    message = str(excinfo.value)
    assert "NOT_SET_ANYWHERE" in message
    assert "the path to something" in message


def test_require_treats_whitespace_as_unset(monkeypatch):
    """An env var set to "" or " " is a mis-set var, not a value."""
    monkeypatch.setenv("SOME_SETTING", "   ")
    with pytest.raises(config.ConfigError):
        config.require("SOME_SETTING", "something")


def test_document_repo_resolves(monkeypatch, document):
    monkeypatch.setenv("DOC_REPO", str(document))
    assert config.document_repo() == document


def test_document_repo_rejects_a_directory_that_is_not_a_document(monkeypatch, tmp_path):
    """Pointing at the wrong directory is the common mistake; catch it at the source."""
    monkeypatch.setenv("DOC_REPO", str(tmp_path))
    with pytest.raises(config.ConfigError) as excinfo:
        config.document_repo()
    assert "main.tex" in str(excinfo.value)


def test_document_repo_expands_a_user_path(monkeypatch, document):
    monkeypatch.setenv("DOC_REPO", str(document))
    assert "~" not in str(config.document_repo())


# ------------------------------------------------------------------------ state dir

def test_state_dir_defaults_beside_the_document(monkeypatch, document):
    monkeypatch.setenv("DOC_REPO", str(document))
    assert config.state_dir() == document / config.STATE_DIRNAME


def test_state_dir_override_wins(monkeypatch, document, tmp_path):
    """For a document repo you cannot commit to."""
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("DOC_REPO", str(document))
    monkeypatch.setenv("DOC_STATE_DIR", str(elsewhere))
    assert config.state_dir() == elsewhere


def test_state_dir_creates_only_when_asked(monkeypatch, document, tmp_path):
    elsewhere = tmp_path / "made"
    monkeypatch.setenv("DOC_REPO", str(document))
    monkeypatch.setenv("DOC_STATE_DIR", str(elsewhere))
    config.state_dir()
    assert not elsewhere.exists()
    config.state_dir(create=True)
    assert elsewhere.is_dir()


# ---------------------------------------------------------------------- the env file

def test_env_file_does_not_override_the_environment(monkeypatch, tmp_path):
    """The property CI depends on: values passed in win over a committed .env."""
    env = tmp_path / ".env"
    env.write_text("DOC_REPO=/from/file\n", encoding="utf-8")
    monkeypatch.setenv("DOC_REPO", "/from/environment")
    monkeypatch.setenv("DOC_ENV", str(env))

    config.load_env()
    import os
    assert os.environ["DOC_REPO"] == "/from/environment"


def test_env_file_fills_in_what_is_unset(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("DOC_REPO=/from/file\n", encoding="utf-8")
    monkeypatch.setenv("DOC_ENV", str(env))

    config.load_env()
    import os
    assert os.environ["DOC_REPO"] == "/from/file"


def test_env_file_strips_quotes_and_export(monkeypatch, tmp_path):
    """A Windows path with spaces needs quotes; leaving them in breaks exists() silently."""
    env = tmp_path / ".env"
    env.write_text('export DOC_REPO="C:/Program Files/doc"\n', encoding="utf-8")
    monkeypatch.setenv("DOC_ENV", str(env))

    config.load_env()
    import os
    assert os.environ["DOC_REPO"] == "C:/Program Files/doc"


def test_env_file_ignores_comments_and_blanks(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# a comment\n\nDOC_REPO=/x\n", encoding="utf-8")
    monkeypatch.setenv("DOC_ENV", str(env))

    config.load_env()
    import os
    assert os.environ["DOC_REPO"] == "/x"


def test_missing_explicit_env_file_is_an_error(monkeypatch, tmp_path):
    """Silently ignoring a path someone set is worse than failing."""
    monkeypatch.setenv("DOC_ENV", str(tmp_path / "nope.env"))
    with pytest.raises(config.ConfigError):
        config.load_env()


def test_describe_runs_without_configuration():
    """`doc-publish config` is what you run *because* something is unset."""
    assert isinstance(config.describe(), str)


# ------------------------------------------------ surviving the thesis-agent rename

@pytest.fixture(autouse=True)
def clean_legacy_env(monkeypatch):
    for name in ("THESIS_REPO", "THESIS_STATE_DIR", "THESIS_AGENT_ENV"):
        monkeypatch.delenv(name, raising=False)
    config._warned.clear()


def test_a_pre_rename_environment_still_works(monkeypatch, document):
    """Someone with THESIS_REPO already exported must not be broken by the rename."""
    monkeypatch.setenv("THESIS_REPO", str(document))
    assert config.document_repo() == document


def test_the_new_name_wins_when_both_are_set(monkeypatch, document, tmp_path):
    old = tmp_path / "old"
    old.mkdir()
    (old / "main.tex").write_text("x", encoding="utf-8")
    monkeypatch.setenv("THESIS_REPO", str(old))
    monkeypatch.setenv("DOC_REPO", str(document))
    assert config.document_repo() == document


def test_the_error_names_both_spellings(monkeypatch):
    with pytest.raises(config.ConfigError) as excinfo:
        config.document_repo()
    message = str(excinfo.value)
    assert "DOC_REPO" in message and "THESIS_REPO" in message


def test_a_pre_rename_state_directory_is_used(monkeypatch, document):
    """The dangerous one: not finding it duplicates an entire published wiki."""
    monkeypatch.setenv("DOC_REPO", str(document))
    legacy = document / config.LEGACY_STATE_DIRNAME
    legacy.mkdir()
    (legacy / "notion_manifest.json").write_text("{}", encoding="utf-8")

    assert config.state_dir() == legacy


def test_the_new_state_directory_wins_once_migrated(monkeypatch, document):
    monkeypatch.setenv("DOC_REPO", str(document))
    (document / config.LEGACY_STATE_DIRNAME).mkdir()
    (document / config.STATE_DIRNAME).mkdir()
    assert config.state_dir() == document / config.STATE_DIRNAME


def test_a_fresh_document_gets_the_new_state_directory(monkeypatch, document):
    monkeypatch.setenv("DOC_REPO", str(document))
    assert config.state_dir() == document / config.STATE_DIRNAME


# ------------------------------------------- finding the document from the structure

@pytest.fixture(autouse=True)
def clean_resolution(monkeypatch):
    """No --document left over from another test, and no DOC_MAIN_TEX narrowing what
    counts as a document root."""
    for name in ("DOC_MAIN_TEX", "DOC_BUILD_DIR", "THESIS_MAIN_TEX", "THESIS_BUILD_DIR"):
        monkeypatch.delenv(name, raising=False)
    config.set_document(None)
    yield
    config.set_document(None)


def _doc(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "main.tex").write_text("\\documentclass{report}\n", encoding="utf-8")
    return path.resolve()


def _workspace(root, members):
    import json
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "workspace.json").write_text(
        json.dumps({"schema": 1, "name": "w", "members": members}), encoding="utf-8")
    return root.resolve()


def test_running_inside_a_document_finds_it(monkeypatch, tmp_path):
    """The point of the walk: no DOC_REPO to write just to run the tool in the document."""
    doc = _doc(tmp_path / "thesis")
    (doc / "Sections" / "Intro").mkdir(parents=True)
    monkeypatch.chdir(doc / "Sections" / "Intro")
    found = config.resolve_document()
    assert found.path == doc and found.route == "walk-up"


def test_a_nested_analysis_finds_the_document_holding_it(monkeypatch, tmp_path):
    """A nested analysis's parent is its document - and it is often its own git repo, so
    the walk must not stop at a .git."""
    doc = _doc(tmp_path / "thesis")
    analysis = doc / "thesis-analysis"
    (analysis / ".git").mkdir(parents=True)
    monkeypatch.chdir(analysis)
    assert config.document_repo() == doc


def test_a_workspace_analysis_feeding_one_document_resolves_it(monkeypatch, tmp_path):
    root = _workspace(tmp_path, [
        {"dir": "thesis", "kind": "document"},
        {"dir": "paper", "kind": "document"},
        {"dir": "x", "kind": "analysis", "documents": ["paper"], "extras": ["dev"]}])
    _doc(root / "thesis")
    paper = _doc(root / "paper")
    (root / "x" / "notebooks").mkdir(parents=True)
    monkeypatch.chdir(root / "x" / "notebooks")
    found = config.resolve_document()
    assert found.path == paper and found.route == "workspace"


def test_a_workspace_analysis_feeding_two_documents_asks_which(monkeypatch, tmp_path):
    """Picking one of two silently would publish the wrong document's wiki."""
    root = _workspace(tmp_path, [
        {"dir": "thesis", "kind": "document"}, {"dir": "paper", "kind": "document"},
        {"dir": "x", "kind": "analysis", "documents": ["thesis", "paper"]}])
    _doc(root / "thesis"), _doc(root / "paper")
    (root / "x").mkdir()
    monkeypatch.chdir(root / "x")
    with pytest.raises(config.ConfigError) as excinfo:
        config.document_repo()
    message = str(excinfo.value)
    assert str(root / "thesis") in message and str(root / "paper") in message
    assert "--document" in message


def test_a_workspace_analysis_feeding_nothing_says_so(monkeypatch, tmp_path):
    root = _workspace(tmp_path, [{"dir": "x", "kind": "analysis", "documents": []}])
    (root / "x").mkdir()
    monkeypatch.chdir(root / "x")
    with pytest.raises(config.ConfigError) as excinfo:
        config.document_repo()
    assert "x/" in str(excinfo.value) and "feeds no document" in str(excinfo.value)


def test_doc_repo_names_the_document_an_analysis_feeds_nothing_else_describes(
        monkeypatch, tmp_path):
    """An analysis with no documents yet is exactly the case DOC_REPO stays for."""
    root = _workspace(tmp_path / "w", [{"dir": "x", "kind": "analysis", "documents": []}])
    (root / "x").mkdir()
    elsewhere = _doc(tmp_path / "elsewhere")
    monkeypatch.chdir(root / "x")
    monkeypatch.setenv("DOC_REPO", str(elsewhere))
    assert config.document_repo() == elsewhere


def test_the_workspace_root_resolves_its_one_document(monkeypatch, tmp_path):
    root = _workspace(tmp_path, [{"dir": "thesis", "kind": "document"},
                                 {"dir": "tools", "kind": "code"}])
    thesis = _doc(root / "thesis")
    monkeypatch.chdir(root)
    assert config.document_repo() == thesis


def test_the_workspace_root_with_several_documents_asks_which(monkeypatch, tmp_path):
    root = _workspace(tmp_path, [{"dir": "thesis", "kind": "document"},
                                 {"dir": "paper", "kind": "document"}])
    _doc(root / "thesis"), _doc(root / "paper")
    monkeypatch.chdir(root)
    with pytest.raises(config.ConfigError) as excinfo:
        config.document_repo()
    assert "--document" in str(excinfo.value) and str(root / "paper") in str(excinfo.value)


def test_a_code_member_is_not_given_a_document_it_does_not_feed(monkeypatch, tmp_path):
    """The sibling layout: a publish repo in the workspace keeps using DOC_REPO, and the
    workspace's documents are not guessed at for it."""
    root = _workspace(tmp_path, [{"dir": "thesis", "kind": "document"},
                                 {"dir": "thesis-publish", "kind": "code"}])
    _doc(root / "thesis")
    (root / "thesis-publish").mkdir()
    monkeypatch.chdir(root / "thesis-publish")
    with pytest.raises(config.ConfigError) as excinfo:
        config.document_repo()
    assert "DOC_REPO" in str(excinfo.value)


def test_the_walk_up_stops_at_the_workspace_root(monkeypatch, tmp_path):
    """A stray main.tex in the directory holding a workspace is not its document."""
    (tmp_path / "main.tex").write_text("x", encoding="utf-8")
    root = _workspace(tmp_path / "w", [{"dir": "thesis", "kind": "document"}])
    thesis = _doc(root / "thesis")
    monkeypatch.chdir(root)
    assert config.document_repo() == thesis


def test_a_malformed_workspace_file_stops_the_walk(monkeypatch, tmp_path):
    """Skipping a typo'd workspace.json would load an outer workspace's documents."""
    _workspace(tmp_path, [{"dir": "thesis", "kind": "document"}])
    _doc(tmp_path / "thesis")
    inner = tmp_path / "inner"
    (inner / ".claude").mkdir(parents=True)
    (inner / ".claude" / "workspace.json").write_text("{not json", encoding="utf-8")
    monkeypatch.chdir(inner)
    with pytest.raises(config.ConfigError) as excinfo:
        config.document_repo()
    assert str(inner / ".claude" / "workspace.json") in str(excinfo.value)


def test_unknown_workspace_keys_are_not_ours_to_reject(monkeypatch, tmp_path):
    """workspace.json is project-workspace's and setup-connectors'; a field this reader
    has never heard of is somebody else's, not a typo."""
    import json
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "workspace.json").write_text(json.dumps({
        "schema": 1, "connections": {"notion": {}}, "state": {"x": 1},
        "members": [{"dir": "thesis", "kind": "document", "name": "T", "extras": [],
                     "future": True}]}), encoding="utf-8")
    thesis = _doc(tmp_path / "thesis")
    monkeypatch.chdir(tmp_path)
    assert config.document_repo() == thesis


# ----------------------------------------------------------- DOC_REPO must agree

def test_doc_repo_disagreeing_with_the_structure_fails_naming_both(monkeypatch, tmp_path):
    """A stale DOC_REPO in a .env would otherwise build, then sync, the wrong document."""
    here = _doc(tmp_path / "here")
    other = _doc(tmp_path / "other")
    monkeypatch.chdir(here)
    monkeypatch.setenv("DOC_REPO", str(other))
    with pytest.raises(config.ConfigError) as excinfo:
        config.document_repo()
    assert str(here) in str(excinfo.value) and str(other) in str(excinfo.value)


def test_doc_repo_agreeing_with_the_structure_is_silent(monkeypatch, tmp_path, capsys):
    here = _doc(tmp_path / "here")
    (here / "sub").mkdir()
    monkeypatch.chdir(here / "sub")
    # A different spelling of the same directory still agrees.
    monkeypatch.setenv("DOC_REPO", str(here / "sub" / ".."))
    found = config.resolve_document()
    assert found.route == "DOC_REPO" and found.path.resolve() == here
    assert capsys.readouterr().out == ""


def test_doc_repo_naming_one_of_several_documents_chooses_it(monkeypatch, tmp_path):
    root = _workspace(tmp_path, [{"dir": "thesis", "kind": "document"},
                                 {"dir": "paper", "kind": "document"}])
    _doc(root / "thesis")
    paper = _doc(root / "paper")
    monkeypatch.chdir(root)
    monkeypatch.setenv("DOC_REPO", str(paper))
    assert config.document_repo() == paper


# ---------------------------------------------------------------------- --document

def test_document_flag_overrides_doc_repo_and_the_structure(monkeypatch, tmp_path):
    here = _doc(tmp_path / "here")
    chosen = _doc(tmp_path / "chosen")
    monkeypatch.chdir(here)
    monkeypatch.setenv("DOC_REPO", str(_doc(tmp_path / "third")))
    config.set_document(chosen)
    found = config.resolve_document()
    assert found.path == chosen and found.route == "--document"


def test_document_flag_still_needs_a_root_tex(tmp_path):
    config.set_document(tmp_path)
    with pytest.raises(config.ConfigError) as excinfo:
        config.document_repo()
    assert "--document" in str(excinfo.value) and "main.tex" in str(excinfo.value)


def test_the_cli_takes_document_for_any_command(monkeypatch, tmp_path, capsys):
    """`build` has no parser of its own, so the flag is the CLI's, not each command's."""
    from doc_publish import cli
    doc = _doc(tmp_path / "doc")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["config", "--document", str(doc)]) == 0
    assert f"document: {doc} - --document" in capsys.readouterr().out


def test_config_says_which_step_found_the_document(monkeypatch, tmp_path):
    doc = _doc(tmp_path / "doc")
    monkeypatch.chdir(doc)
    assert f"document: {doc} - found at or above {doc}" in config.describe()


# ------------------------------------------------- the build dir inside a document

needs_git = pytest.mark.skipif(not __import__("shutil").which("git"), reason="needs git")


def _git_init(path):
    import subprocess
    subprocess.run(["git", "init", "-q", str(path)], check=True)


@needs_git
def test_the_build_dir_is_refused_inside_a_document_that_does_not_ignore_it(
        monkeypatch, tmp_path):
    """Found by walking up means cwd is the document, the repo Overleaf syncs - where an
    unignored build/corpus_draft.md is one `git add .` from being shared."""
    doc = _doc(tmp_path / "doc")
    _git_init(doc)
    monkeypatch.chdir(doc)
    with pytest.raises(config.ConfigError) as excinfo:
        config.build_dir(create=True)
    assert "DOC_BUILD_DIR" in str(excinfo.value) and ".gitignore" in str(excinfo.value)
    assert not (doc / "build").exists()
    # Reading an existing build/ - the chat servers do at import - writes nothing.
    assert config.build_dir() == doc / "build"


@needs_git
def test_the_build_dir_is_allowed_where_git_ignores_it(monkeypatch, tmp_path):
    doc = _doc(tmp_path / "doc")
    _git_init(doc)
    (doc / ".gitignore").write_text("build/\n", encoding="utf-8")
    monkeypatch.chdir(doc)
    assert config.build_dir(create=True) == doc / "build"


@needs_git
def test_the_build_dir_check_leaves_doc_repo_setups_alone(monkeypatch, tmp_path):
    """Existing setups name the document with DOC_REPO; where they run is unchanged."""
    doc = _doc(tmp_path / "doc")
    _git_init(doc)
    monkeypatch.chdir(doc)
    monkeypatch.setenv("DOC_REPO", str(doc))
    assert config.build_dir(create=True) == doc / "build"
