"""How settings resolve, and how they fail.

Configuration here is almost all environment variables pointing at directories, which is
the kind of thing that goes wrong far from where it was set. These tests pin the two
properties that make that bearable: an unset or wrong value fails with a message naming
what it should have been, and a `.env` never silently overrides something already in the
environment (which is what lets CI pass values in unchanged).
"""
import pytest

from thesis_agent import config


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """Every test starts from a known-empty environment for the vars config reads."""
    for name in ("THESIS_REPO", "THESIS_STATE_DIR", "THESIS_AGENT_ENV",
                 "THESIS_PUBLISH_REPO", "THESIS_CORPUS_MODE"):
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


def test_thesis_repo_resolves(monkeypatch, document):
    monkeypatch.setenv("THESIS_REPO", str(document))
    assert config.thesis_repo() == document


def test_thesis_repo_rejects_a_directory_that_is_not_a_document(monkeypatch, tmp_path):
    """Pointing at the wrong directory is the common mistake; catch it at the source."""
    monkeypatch.setenv("THESIS_REPO", str(tmp_path))
    with pytest.raises(config.ConfigError) as excinfo:
        config.thesis_repo()
    assert "main.tex" in str(excinfo.value)


def test_thesis_repo_expands_a_user_path(monkeypatch, document):
    monkeypatch.setenv("THESIS_REPO", str(document))
    assert "~" not in str(config.thesis_repo())


# ------------------------------------------------------------------------ state dir

def test_state_dir_defaults_beside_the_document(monkeypatch, document):
    monkeypatch.setenv("THESIS_REPO", str(document))
    assert config.state_dir() == document / config.STATE_DIRNAME


def test_state_dir_override_wins(monkeypatch, document, tmp_path):
    """For a document repo you cannot commit to."""
    elsewhere = tmp_path / "elsewhere"
    monkeypatch.setenv("THESIS_REPO", str(document))
    monkeypatch.setenv("THESIS_STATE_DIR", str(elsewhere))
    assert config.state_dir() == elsewhere


def test_state_dir_creates_only_when_asked(monkeypatch, document, tmp_path):
    elsewhere = tmp_path / "made"
    monkeypatch.setenv("THESIS_REPO", str(document))
    monkeypatch.setenv("THESIS_STATE_DIR", str(elsewhere))
    config.state_dir()
    assert not elsewhere.exists()
    config.state_dir(create=True)
    assert elsewhere.is_dir()


# ---------------------------------------------------------------------- the env file

def test_env_file_does_not_override_the_environment(monkeypatch, tmp_path):
    """The property CI depends on: values passed in win over a committed .env."""
    env = tmp_path / ".env"
    env.write_text("THESIS_REPO=/from/file\n", encoding="utf-8")
    monkeypatch.setenv("THESIS_REPO", "/from/environment")
    monkeypatch.setenv("THESIS_AGENT_ENV", str(env))

    config.load_env()
    import os
    assert os.environ["THESIS_REPO"] == "/from/environment"


def test_env_file_fills_in_what_is_unset(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("THESIS_REPO=/from/file\n", encoding="utf-8")
    monkeypatch.setenv("THESIS_AGENT_ENV", str(env))

    config.load_env()
    import os
    assert os.environ["THESIS_REPO"] == "/from/file"


def test_env_file_strips_quotes_and_export(monkeypatch, tmp_path):
    """A Windows path with spaces needs quotes; leaving them in breaks exists() silently."""
    env = tmp_path / ".env"
    env.write_text('export THESIS_REPO="C:/Program Files/doc"\n', encoding="utf-8")
    monkeypatch.setenv("THESIS_AGENT_ENV", str(env))

    config.load_env()
    import os
    assert os.environ["THESIS_REPO"] == "C:/Program Files/doc"


def test_env_file_ignores_comments_and_blanks(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# a comment\n\nTHESIS_REPO=/x\n", encoding="utf-8")
    monkeypatch.setenv("THESIS_AGENT_ENV", str(env))

    config.load_env()
    import os
    assert os.environ["THESIS_REPO"] == "/x"


def test_missing_explicit_env_file_is_an_error(monkeypatch, tmp_path):
    """Silently ignoring a path someone set is worse than failing."""
    monkeypatch.setenv("THESIS_AGENT_ENV", str(tmp_path / "nope.env"))
    with pytest.raises(config.ConfigError):
        config.load_env()


def test_describe_runs_without_configuration():
    """`thesis-agent config` is what you run *because* something is unset."""
    assert isinstance(config.describe(), str)
