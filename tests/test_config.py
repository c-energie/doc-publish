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
