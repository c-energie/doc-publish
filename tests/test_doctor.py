"""`doc-publish doctor` - the setup diagnostic.

Its whole value is being trustworthy about state, so what is pinned here is the grading:
a document that will build must not report FAIL, an optional feature that is merely
unconfigured must not report FAIL either, and the things that genuinely stop a build must.

A diagnostic that cries wolf is worse than none, because it trains you to skim it.
"""
import json

import pytest

from doc_publish import config, doctor


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DOC_REPO", "DOC_MAIN_TEX", "DOC_TITLE", "DOC_STATE_DIR", "DOC_ENV",
                 "DOC_PUBLISH_REPO", "NOTION_TOKEN", "DOC_NOTION_PARENT", "CORPUS_MODE",
                 "QUARTO", "THESIS_REPO", "THESIS_PUBLISH_REPO"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def document(tmp_path):
    """A minimal but genuinely buildable document."""
    (tmp_path / "main.tex").write_text(
        "\\documentclass{report}\n\\begin{document}\n"
        "\\chapter{Intro}\\label{ch:i}\nText.\n\\end{document}\n", encoding="utf-8")
    return tmp_path


def _states(report):
    return {row["check"]: row["state"] for row in report.rows}


def _run(monkeypatch, repo=None, **env):
    if repo is not None:
        monkeypatch.setenv("DOC_REPO", str(repo))
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    r = doctor.Report()
    doctor._check_python(r)
    found = doctor._check_document(r)
    if found is not None:
        doctor._check_inputs(r, found)
        doctor._check_sections(r, found)
        doctor._check_contract(r, found)
    doctor._check_streams(r)
    return r


# ------------------------------------------------------------------- grading

def test_a_buildable_document_does_not_fail(monkeypatch, document):
    """The important one: no FAIL for a document `doc-publish build` handles."""
    r = _run(monkeypatch, document)
    assert not r.failed, [x for x in r.rows if x["state"] == doctor.FAIL]


def test_an_unset_doc_repo_fails(monkeypatch):
    r = _run(monkeypatch)
    assert _states(r)["DOC_REPO"] == doctor.FAIL


def test_a_doc_repo_with_no_root_tex_fails(monkeypatch, tmp_path):
    r = _run(monkeypatch, tmp_path)
    assert _states(r)["DOC_REPO"] == doctor.FAIL


def test_unconfigured_optional_streams_never_fail(monkeypatch, document):
    """A document that only wants a corpus is correctly set up, not broken."""
    r = _run(monkeypatch, document)
    states = _states(r)
    assert states["notion (sync)"] == doctor.SKIP
    assert states["publish repo"] == doctor.SKIP
    assert not r.failed


def test_a_missing_contract_warns_rather_than_fails(monkeypatch, document):
    """`build` works without it; `doc-publish check` is the gate that fails."""
    r = _run(monkeypatch, document)
    assert _states(r)["state dir"] == doctor.WARN
    assert not r.failed


def test_half_configured_notion_warns(monkeypatch, document):
    """One of the two set is a mistake worth surfacing; neither set is a choice."""
    r = _run(monkeypatch, document, NOTION_TOKEN="secret")
    assert _states(r)["notion (sync)"] == doctor.WARN


def test_draft_corpus_mode_warns(monkeypatch, document):
    r = _run(monkeypatch, document, CORPUS_MODE="draft")
    assert _states(r)["CORPUS_MODE"] == doctor.WARN


# --------------------------------------------------------------- what it reports

def test_it_reports_the_root_tex_it_found(monkeypatch, tmp_path):
    (tmp_path / "dissertation.tex").write_text(
        "\\documentclass{report}\n\\begin{document}\n\\end{document}\n", encoding="utf-8")
    r = _run(monkeypatch, tmp_path)
    row = next(x for x in r.rows if x["check"] == "root .tex")
    assert row["detail"] == "dissertation.tex"


def test_a_missing_graphicspath_dir_is_named(monkeypatch, document):
    """\\graphicspath does not recurse, so a missing entry is a silent build error."""
    (document / "settings.sty").write_text(
        "\\graphicspath{%\n  {Sections/Gone/Figures/}%\n}\n", encoding="utf-8")
    r = _run(monkeypatch, document)
    row = next(x for x in r.rows if x["check"] == "\\graphicspath")
    assert row["state"] == doctor.WARN
    assert "Gone" in row["fix"]


def test_a_legacy_chapters_tree_is_reported_by_its_real_name(monkeypatch, document):
    (document / "Chapters").mkdir()
    r = _run(monkeypatch, document)
    row = next(x for x in r.rows if x["check"] == "section tree")
    assert row["detail"].startswith("Chapters/")


def test_no_glossary_is_skip_not_warn(monkeypatch, document):
    """Having no glossary is a legitimate document, not a defect."""
    r = _run(monkeypatch, document)
    assert _states(r)["glossary"] == doctor.SKIP


# ---------------------------------------------------------------- the interfaces

def test_json_is_valid_and_carries_every_finding(monkeypatch, document, capsys):
    monkeypatch.setenv("DOC_REPO", str(document))
    code = doctor.main(["--json"])
    payload = json.loads(capsys.readouterr().out)
    assert code == 0 and payload["ok"] is True
    assert len(payload["findings"]) > 5
    assert {"group", "check", "state", "detail", "fix"} == set(payload["findings"][0])


def test_exit_code_follows_failure(monkeypatch, document, capsys):
    monkeypatch.setenv("DOC_REPO", str(document))
    assert doctor.main([]) == 0
    monkeypatch.setenv("DOC_REPO", str(document / "nope"))
    assert doctor.main([]) == 1
    capsys.readouterr()


def test_rendered_output_shows_the_fix_for_anything_wrong(monkeypatch, capsys):
    """A finding without an actionable line is the failure mode of this whole command."""
    doctor.main([])
    out = capsys.readouterr().out
    assert "->" in out
    assert "DOC_REPO" in out
