"""Scaffolding and auditing the engine's own `.env`.

Two properties carry the weight here. That an existing file is never damaged — it may
hold the only copy of a token, and there is no `--force` to argue about. And that the
template stays the single source of truth: the audit derives its idea of "required" from
the template rather than a list kept beside it, and the repo-root copy is held identical
to the packaged one, because two copies of a config example drift the moment one is
edited in a hurry.
"""
from pathlib import Path

import pytest

from doc_publish import config, envfile

REPO_ROOT = Path(__file__).resolve().parents[1]


def run(directory, *args):
    return envfile.main(["--dir", str(directory), *args])


def write_env(directory, text):
    (directory / ".env").write_text(text, encoding="utf-8")


def complete_env(directory, extra=""):
    """A .env setting every active key the template declares."""
    active, _ = envfile.template_keys()
    write_env(directory, "".join(f"{key}=x\n" for key in active) + extra)


# ------------------------------------------------------------------- writing

def test_writes_an_env_when_there_is_none(tmp_path):
    assert run(tmp_path) == 0
    assert (tmp_path / ".env").is_file()


def test_written_file_is_the_template_verbatim(tmp_path):
    run(tmp_path)
    assert ((tmp_path / ".env").read_text(encoding="utf-8")
            == envfile.TEMPLATE.read_text(encoding="utf-8"))


def test_written_file_parses_as_the_engine_will_read_it(tmp_path):
    """The scaffold is worthless if config's own parser disagrees with it."""
    run(tmp_path)
    values = config._parse_env_file(tmp_path / ".env")
    active, _ = envfile.template_keys()
    assert set(active) <= set(values)


def test_no_dir_means_the_working_directory(tmp_path, monkeypatch):
    """The CLI passes an empty list for a bare `doc-publish env`; it must not then
    fall through to sys.argv, which is pytest's."""
    monkeypatch.chdir(tmp_path)
    assert envfile.main([]) == 0
    assert (tmp_path / ".env").is_file()


def test_a_missing_directory_is_reported_not_created(tmp_path):
    target = tmp_path / "nope"
    assert envfile.main(["--dir", str(target)]) == 2
    assert not target.exists()


def test_an_env_above_is_flagged_as_shadowed(tmp_path, capsys):
    """Settings resolve from the nearest .env upward, so this changes behaviour."""
    write_env(tmp_path, "DOC_REPO=/from/the/parent\n")
    child = tmp_path / "sub"
    child.mkdir()

    assert run(child) == 0
    assert "note:" in capsys.readouterr().out


# --------------------------------------------------------------- not clobbering

def test_an_existing_env_is_never_overwritten(tmp_path):
    write_env(tmp_path, "DOC_REPO=/mine\nNOTION_TOKEN=secret-value\n")
    run(tmp_path)
    assert "secret-value" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_check_does_not_write(tmp_path):
    assert run(tmp_path, "--check") == 1
    assert not (tmp_path / ".env").exists()


# -------------------------------------------------------------------- auditing

def test_a_missing_required_setting_is_reported(tmp_path, capsys):
    write_env(tmp_path, "DOC_REPO=/mine\n")
    assert run(tmp_path) == 1
    assert "DOC_PUBLISH_REPO" in capsys.readouterr().out


def test_a_complete_env_passes(tmp_path):
    complete_env(tmp_path)
    assert run(tmp_path) == 0


def test_an_unrecognised_key_is_reported(tmp_path, capsys):
    complete_env(tmp_path, extra="DOC_REPOO=/typo\n")
    run(tmp_path)
    assert "DOC_REPOO" in capsys.readouterr().out


def test_a_legacy_spelling_satisfies_the_setting_it_feeds(tmp_path, capsys):
    """config.getenv falls back to THESIS_*, so a file using one is not missing anything.

    Calling it missing would send someone to add a setting the engine already reads —
    and the duplicate they added could then disagree with the one in use.
    """
    active, _ = envfile.template_keys()
    text = "".join(f"{key}=x\n" for key in active if key != "DOC_REPO")
    write_env(tmp_path, text + "THESIS_REPO=/old\n")

    assert run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "MISSING" not in out
    assert "THESIS_REPO" in out and "rename" in out


def test_a_legacy_spelling_is_not_also_called_unrecognised(tmp_path, capsys):
    active, _ = envfile.template_keys()
    text = "".join(f"{key}=x\n" for key in active if key != "DOC_REPO")
    write_env(tmp_path, text + "THESIS_REPO=/old\n")

    run(tmp_path)
    assert "does not recognise" not in capsys.readouterr().out


def test_optional_settings_do_not_fail_the_audit(tmp_path, capsys):
    complete_env(tmp_path)
    assert run(tmp_path) == 0
    _, optional = envfile.template_keys()
    assert optional, "the template should document some optional settings"
    assert "optional" in capsys.readouterr().out


# ------------------------------------------------- the template is the one truth

def test_the_template_declares_the_settings_config_requires():
    """A setting config.require()s but the template omits is unscaffoldable."""
    active, _ = envfile.template_keys()
    assert "DOC_REPO" in active
    assert "DOC_PUBLISH_REPO" in active


def test_prose_comments_are_not_mistaken_for_settings():
    """"# public = comments stripped" partitions on '=' like a setting does."""
    _, optional = envfile.template_keys()
    assert all(key.isupper() for key in optional)
    assert "public" not in optional


def test_the_packaged_template_matches_the_repo_root_example():
    """Two copies exist only because package data cannot reach outside the package."""
    root = REPO_ROOT / ".env.example"
    if not root.is_file():
        pytest.skip("installed package, not a checkout")
    # splitlines, not the raw text: the checkout may hold either line ending.
    assert (root.read_text(encoding="utf-8").splitlines()
            == envfile.TEMPLATE.read_text(encoding="utf-8").splitlines())
