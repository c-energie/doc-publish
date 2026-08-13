"""What a figure publishes as on the Notion side.

The wiki shows a figure one of three ways, and picking the wrong one is not a visible
error - it is a page that quietly has no figure on it. So the ladder is pinned here:

  * an export in figures_manifest.json  -> an embed of the hosted plot;
  * no export                           -> the static image;
  * a non-live asset, or no hosting yet -> a placeholder that says which.

The distinction earns its keep now that plotly statics are exported as PDF: Notion cannot
render a PDF in an image block, so a figure with an export MUST take the embed branch,
and a figure without one must still have a raster static to fall back to.
"""
from doc_publish.publish.emit import Fragment
from doc_publish.publish.linker import Linker

BASE = "https://example.github.io/figures"


def linker(figures, interactive=None, base_url=BASE):
    """A Linker with only what _figure_card touches; plan is unused for empty captions."""
    return Linker(plan=None, labels={}, page_ids={}, figures=figures,
                  base_url=base_url, interactive=interactive)


def figure(label, name, status="ok"):
    return {"label": label, "assets": [{"name": name, "path": f"figures/{name}",
                                        "status": status}]}


def blocks(card):
    return card.get("_deferred", [])


def kinds(card):
    return [b["type"] for b in blocks(card)]


def test_a_figure_with_an_export_embeds_the_hosted_plot():
    card = linker([figure("Fig: htc", "htc.pdf")],
                  {"Fig: htc": {"interactive": "../figures_html/htc.html"}}
                  ).\
        _figure_card(Fragment(kind="figure", label="Fig: htc"))
    assert "embed" in kinds(card)
    assert "image" not in kinds(card), "a PDF static would not render; the embed replaces it"
    embed = next(b for b in blocks(card) if b["type"] == "embed")
    # the manifest path is relative to build/, but the export is served flat
    assert embed["embed"]["url"] == f"{BASE}/htc.html"


def test_the_export_link_accompanies_the_embed():
    card = linker([figure("Fig: htc", "htc.pdf")],
                  {"Fig: htc": {"interactive": "../figures_html/htc.html"}}
                  )._figure_card(Fragment(kind="figure", label="Fig: htc"))
    links = [r["text"]["link"]["url"]
             for b in blocks(card) if b["type"] == "paragraph"
             for r in b["paragraph"]["rich_text"] if r["text"].get("link")]
    assert links == [f"{BASE}/htc.html"]


def test_a_figure_without_an_export_falls_back_to_its_static_image():
    card = linker([figure("Fig: legacy", "legacy.png")], interactive={}) \
        ._figure_card(Fragment(kind="figure", label="Fig: legacy"))
    assert kinds(card) == ["image"]
    assert blocks(card)[0]["image"]["external"]["url"] == f"{BASE}/legacy.png"


def test_no_dead_link_under_a_figure_that_has_no_export():
    """The link used to be unconditional, so every unmigrated figure carried a 404."""
    card = linker([figure("Fig: legacy", "legacy.png")], interactive={}) \
        ._figure_card(Fragment(kind="figure", label="Fig: legacy"))
    assert not [r for b in blocks(card) if b["type"] == "paragraph"
                for r in b["paragraph"]["rich_text"] if r["text"].get("link")]


def test_a_pending_asset_is_reported_not_rendered():
    card = linker([figure("Fig: todo", "todo.png", status="pending")],
                  {"Fig: todo": {"interactive": "../figures_html/todo.html"}}
                  )._figure_card(Fragment(kind="figure", label="Fig: todo"))
    # an export must not smuggle a commented-out or unregenerated figure onto the page
    assert "embed" not in kinds(card)
    assert card["callout"]["children"]


def test_without_hosting_nothing_is_published_at_all():
    card = linker([figure("Fig: htc", "htc.pdf")],
                  {"Fig: htc": {"interactive": "../figures_html/htc.html"}},
                  base_url=None)._figure_card(Fragment(kind="figure", label="Fig: htc"))
    assert "embed" not in kinds(card) and "image" not in kinds(card)
