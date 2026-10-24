"""Which figure manifest the site reads, and what it tolerates in it.

doc_analysis writes `figures_manifest.json` into the document, one per document however
many analyses feed it; `doc-publish figures` writes one into build/. The document's wins,
and the site says when it ignored the other - two manifests disagreeing is otherwise
invisible. The manifest is shared with other readers, so keys this one does not use must
never be an error.
"""
import json

from doc_publish.publish.quarto.figures import FigureEmitter


def _figures():
    return [{"label": "Fig: a", "assets": [{"status": "ok", "path": "figures/a.png"}]}]


def _emitter(tmp_path, *manifests):
    site = tmp_path / "site"
    return FigureEmitter(_figures(), list(manifests), tmp_path / "build", site)


def _write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_the_document_manifest_wins_and_the_other_is_reported(tmp_path):
    doc_manifest = _write(tmp_path / "doc" / "figures_manifest.json", {})
    build_manifest = _write(tmp_path / "build" / "figures_manifest.json", {})
    emitter = _emitter(tmp_path, doc_manifest, build_manifest)
    assert emitter.manifest_path == doc_manifest
    assert emitter.manifests_ignored == [build_manifest]


def test_one_manifest_is_used_without_comment(tmp_path):
    build_manifest = _write(tmp_path / "build" / "figures_manifest.json", {})
    emitter = _emitter(tmp_path, tmp_path / "doc" / "figures_manifest.json", build_manifest)
    assert emitter.manifest_path == build_manifest and emitter.manifests_ignored == []


def test_keys_and_fields_for_other_readers_are_ignored(tmp_path):
    """Tab:/Val: entries and an `analysis` field are doc_analysis's, for other consumers;
    an export path relative to the manifest reaches into the analysis repo directly."""
    export = tmp_path / "x" / "figures_html" / "a.html"
    export.parent.mkdir(parents=True)
    export.write_text("<html></html>", encoding="utf-8")
    manifest = _write(tmp_path / "doc" / "figures_manifest.json", {
        "Fig: a": {"interactive": "../x/figures_html/a.html", "analysis": "x"},
        "Tab: results": {"analysis": "x", "tex": "Tables/results.tex"},
        "Val: nmodels": "40",
    })
    emitter = _emitter(tmp_path, manifest)
    out = emitter.emit("Fig: a", "A caption.")
    assert 'src="figures/a.html"' in out
    assert emitter.tiers["interactive"] == ["Fig: a"]
