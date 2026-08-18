"""Ingesting a document that was not built from writing-template.

The engine grew against one thesis, and three of its habits had hardened into
requirements: the root had to be `main.tex`, `glossary_terms.tex` had to exist, and the
bibliography had to sit under `Bibliographies/`. None of those is a LaTeX convention, and
an existing document meeting none of them is the normal case when someone adopts this.

These tests pin the looser contract: name your root what you like, have no glossary at
all, and keep your .bib wherever your `\\addbibresource` says it is.
"""
import pytest

from doc_publish import config
from doc_publish.ingest import flatten


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in ("DOC_REPO", "DOC_MAIN_TEX", "DOC_TITLE", "DOC_SOURCE_LABEL"):
        monkeypatch.delenv(name, raising=False)


def _doc(tmp_path, root="main.tex", body="\\chapter{Intro}\\label{ch:intro}\n"):
    (tmp_path / root).write_text(
        "\\documentclass{report}\n\\begin{document}\n" + body + "\\end{document}\n",
        encoding="utf-8")
    return tmp_path


# ------------------------------------------------------------------ the root .tex

def test_a_conventional_root_name_is_found(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_REPO", str(_doc(tmp_path, root="thesis.tex")))
    assert config.main_tex() == "thesis.tex"


def test_doc_main_tex_names_an_unconventional_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_REPO", str(_doc(tmp_path, root="Dissertation-final.tex")))
    monkeypatch.setenv("DOC_MAIN_TEX", "Dissertation-final.tex")
    assert config.main_tex() == "Dissertation-final.tex"


def test_main_tex_wins_over_the_other_conventional_names(tmp_path, monkeypatch):
    repo = _doc(tmp_path, root="main.tex")
    (repo / "thesis.tex").write_text("\\documentclass{report}\n", encoding="utf-8")
    monkeypatch.setenv("DOC_REPO", str(repo))
    assert config.main_tex() == "main.tex"


def test_a_repo_with_no_root_says_how_to_name_one(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_REPO", str(tmp_path))
    with pytest.raises(config.ConfigError, match="DOC_MAIN_TEX"):
        config.document_repo()


def test_doc_main_tex_pointing_at_nothing_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_REPO", str(_doc(tmp_path)))
    monkeypatch.setenv("DOC_MAIN_TEX", "absent.tex")
    with pytest.raises(config.ConfigError, match="absent.tex"):
        config.document_repo()


# --------------------------------------------------------------------- the glossary

def test_no_glossary_anywhere_is_not_an_error(tmp_path):
    """This used to raise FileNotFoundError before the build reported anything."""
    assert flatten.glossary_source(_doc(tmp_path)) == ""


def test_the_conventional_glossary_file_is_read(tmp_path):
    repo = _doc(tmp_path)
    (repo / "glossary_terms.tex").write_text(
        "\\newacronym{htc}{HTC}{heat transfer coefficient}\n", encoding="utf-8")
    assert "htc" in flatten.glossary_source(repo)


def test_acronyms_declared_in_a_sty_are_found(tmp_path):
    """Plenty of documents declare their acronyms in the preamble, not a separate file."""
    repo = _doc(tmp_path)
    (repo / "preamble.sty").write_text(
        "\\newacronym{htc}{HTC}{heat transfer coefficient}\n", encoding="utf-8")
    assert "htc" in flatten.glossary_source(repo)


def test_a_glossary_input_from_the_preamble_is_found(tmp_path, monkeypatch):
    """\\input{frontmatter/acronyms.tex} sits in the preamble, which inline() discards."""
    repo = tmp_path
    (repo / "frontmatter").mkdir()
    (repo / "frontmatter" / "acronyms.tex").write_text(
        "\\newacronym{htc}{HTC}{heat transfer coefficient}\n", encoding="utf-8")
    (repo / "main.tex").write_text(
        "\\documentclass{report}\n\\input{frontmatter/acronyms.tex}\n"
        "\\begin{document}\n\\chapter{Intro}\n\\end{document}\n", encoding="utf-8")
    monkeypatch.setenv("DOC_REPO", str(repo))
    assert "htc" in flatten.glossary_source(repo)


def test_an_empty_conventional_file_does_not_shadow_a_real_glossary(tmp_path, monkeypatch):
    """The regression that makes "let init create an empty one" the wrong fix.

    An empty glossary_terms.tex used to be returned alone, so a document declaring its
    acronyms elsewhere had every \\acrshort come out as [UNRESOLVED: acronym ...].
    """
    repo = _doc(tmp_path)
    (repo / "prefs.sty").write_text(
        "\\newacronym{htc}{HTC}{heat transfer coefficient}\n", encoding="utf-8")
    (repo / "glossary_terms.tex").write_text("", encoding="utf-8")
    monkeypatch.setenv("DOC_REPO", str(repo))
    assert "htc" in flatten.glossary_source(repo)


# ----------------------------------------------------------------- the bibliography

def test_addbibresource_is_followed(tmp_path):
    repo = _doc(tmp_path)
    (repo / "refs").mkdir()
    (repo / "refs" / "library.bib").write_text("@article{k, year={2020}}\n", encoding="utf-8")
    (repo / "main.tex").write_text(
        "\\addbibresource{refs/library.bib}\n" + (repo / "main.tex").read_text(encoding="utf-8"),
        encoding="utf-8")
    assert flatten.bib_files(repo) == [repo / "refs" / "library.bib"]


def test_bibliography_without_an_extension_is_followed(tmp_path):
    """\\bibliography{refs/library} omits the .bib by convention."""
    repo = _doc(tmp_path)
    (repo / "refs").mkdir()
    (repo / "refs" / "library.bib").write_text("@article{k, year={2020}}\n", encoding="utf-8")
    (repo / "main.tex").write_text(
        "\\bibliography{refs/library}\n" + (repo / "main.tex").read_text(encoding="utf-8"),
        encoding="utf-8")
    assert flatten.bib_files(repo) == [repo / "refs" / "library.bib"]


def test_a_declaration_in_a_sty_counts(tmp_path):
    repo = _doc(tmp_path)
    (repo / "one.bib").write_text("@article{k, year={2020}}\n", encoding="utf-8")
    (repo / "settings.sty").write_text("\\addbibresource{one.bib}\n", encoding="utf-8")
    assert flatten.bib_files(repo) == [repo / "one.bib"]


def test_the_conventional_location_is_the_fallback(tmp_path):
    repo = _doc(tmp_path)
    (repo / "Bibliographies").mkdir()
    conventional = repo / "Bibliographies" / "references.bib"
    conventional.write_text("@article{k, year={2020}}\n", encoding="utf-8")
    assert flatten.bib_files(repo) == [conventional]


def test_an_undeclared_bib_is_still_found_rather_than_lost(tmp_path):
    """Losing every citation silently is the worst outcome; find it and move on."""
    repo = _doc(tmp_path)
    (repo / "somewhere").mkdir()
    stray = repo / "somewhere" / "refs.bib"
    stray.write_text("@article{k, year={2020}}\n", encoding="utf-8")
    assert flatten.bib_files(repo) == [stray]


def test_a_document_with_no_bib_returns_nothing(tmp_path):
    assert flatten.bib_files(_doc(tmp_path)) == []


# --------------------------------------------------------------------------- title

def test_title_comes_from_the_source(tmp_path, monkeypatch):
    repo = _doc(tmp_path)
    (repo / "main.tex").write_text(
        "\\title{Heat Transfer in Dwellings}\n" + (repo / "main.tex").read_text(encoding="utf-8"),
        encoding="utf-8")
    monkeypatch.setenv("DOC_REPO", str(repo))
    assert config.title() == "Heat Transfer in Dwellings"


def test_doc_title_overrides_the_source(tmp_path, monkeypatch):
    repo = _doc(tmp_path)
    (repo / "main.tex").write_text(
        "\\title{Ignored}\n" + (repo / "main.tex").read_text(encoding="utf-8"),
        encoding="utf-8")
    monkeypatch.setenv("DOC_REPO", str(repo))
    monkeypatch.setenv("DOC_TITLE", "What I Actually Call It")
    assert config.title() == "What I Actually Call It"


def test_title_falls_back_to_the_source_label(tmp_path, monkeypatch):
    monkeypatch.setenv("DOC_REPO", str(_doc(tmp_path)))
    assert config.title() == tmp_path.name


# --------------------------------------------------------------- \graphicspath

@pytest.mark.parametrize("declaration,expected", [
    ("\graphicspath{{figs/}}", ["figs"]),                       # single line
    ("\graphicspath{%\n  {figs/}%\n}", ["figs"]),               # %-continued
    ("\graphicspath{{figs/}{more/}}", ["figs", "more"]),        # several, one line
    ("\graphicspath {{figs/}}", ["figs"]),                      # space before the brace
])
def test_graphicspath_is_read_however_it_is_spelled(tmp_path, monkeypatch,
                                                    declaration, expected):
    """The single-line form is the more common one and used to parse as *no* declaration,
    which silently resolved every bare-filename figure against the repo root."""
    from doc_publish.ingest import figures

    repo = _doc(tmp_path)
    (repo / "settings.sty").write_text(declaration + "\n", encoding="utf-8")
    monkeypatch.setenv("DOC_REPO", str(repo))
    assert [p.name for p in figures.graphics_roots(repo)] == expected


def test_graphicspath_in_the_root_tex_is_found(tmp_path, monkeypatch):
    """A document with no .sty at all puts it in the root file."""
    from doc_publish.ingest import figures

    repo = tmp_path
    (repo / "main.tex").write_text(
        "\documentclass{report}\n\graphicspath{{figs/}}\n"
        "\begin{document}\n\end{document}\n", encoding="utf-8")
    monkeypatch.setenv("DOC_REPO", str(repo))
    assert [p.name for p in figures.graphics_roots(repo)] == ["figs"]


def test_no_graphicspath_falls_back_to_the_repo_root(tmp_path, monkeypatch):
    from doc_publish.ingest import figures

    repo = _doc(tmp_path)
    monkeypatch.setenv("DOC_REPO", str(repo))
    assert figures.graphics_roots(repo) == [repo]
