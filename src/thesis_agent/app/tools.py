"""Tools: figure retrieval and querying/plotting the per-record results."""
from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

import pandas as pd

from .. import config

BUILD = config.build_dir()
# Tidy per-record rows: an id column, a method column, an estimate and its
# uncertainty. Never committed - override the location with THESIS_RESULTS.
RESULTS = Path(os.environ.get("THESIS_RESULTS") or Path.cwd() / "data" / "results.parquet")

_manifest: list[dict] | None = None
_results: pd.DataFrame | None = None


def manifest() -> list[dict]:
    global _manifest
    if _manifest is None:
        _manifest = json.loads((BUILD / "figures.json").read_text())
    return _manifest


def results() -> pd.DataFrame:
    global _results
    if _results is None:
        _results = pd.read_parquet(RESULTS) if RESULTS.exists() else pd.DataFrame()
    return _results


SCHEMA = [
    {
        "name": "get_figure",
        "description": (
            "Retrieve a figure from the document by its LaTeX label (e.g. 'Fig: model fits'). "
            "Use when a figure answers better than prose, or when asked to show a result. "
            "Call list_figures first if unsure of the label."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
        },
    },
    {
        "name": "list_figures",
        "description": "List figures whose caption or section matches a search term.",
        "input_schema": {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    },
    {
        "name": "query_results",
        "description": (
            "Query the per-record estimates behind the results chapters. Returns summary "
            "statistics, never more than 50 rows. Columns: " + ", ".join(results().columns)
            if not results().empty else "Results table (not yet loaded)."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "pandas .query() expression"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "groupby": {"type": "array", "items": {"type": "string"}},
            },
        },
    },
    {
        "name": "plot_results",
        "description": (
            "Generate a plot from the per-record results. Kinds: 'scatter' (method "
            "agreement, x vs y with error bars), 'hist', 'ecdf'. Generated on the fly - "
            "not a figure from the document."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "enum": ["scatter", "hist", "ecdf"]},
                "x": {"type": "string"},
                "y": {"type": "string"},
                "filter": {"type": "string"},
                "title": {"type": "string"},
            },
            "required": ["kind", "x"],
        },
    },
]


def _image_block(path: Path) -> dict:
    media = {"png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg"}[path.suffix.lstrip(".").lower()]
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media,
                   "data": base64.b64encode(path.read_bytes()).decode()},
    }


def dispatch(name: str, args: dict) -> list[dict]:
    """Return content blocks for a tool_result."""
    if name == "list_figures":
        q = args["query"].lower()
        hits = [
            {"label": e["label"], "section": e["section"], "caption": e["caption"][:200],
             "pending": any(a["status"] == "pending" for a in e["assets"])}
            for e in manifest()
            if q in e["caption"].lower() or q in e["label"].lower() or q in str(e["section"])
        ][:20]
        return [{"type": "text", "text": json.dumps(hits, indent=2) or "no matching figures"}]

    if name == "get_figure":
        entry = next((e for e in manifest() if e["label"] == args["label"]), None)
        if not entry:
            return [{"type": "text", "text": f"No figure labelled {args['label']}."}]
        blocks: list[dict] = [
            {"type": "text", "text": f"{entry['label']} ({entry['section']}): {entry['caption']}"}
        ]
        for a in entry["assets"]:
            if a["status"] != "ok":
                blocks.append({"type": "text",
                               "text": f"asset {a['name']}: {a['status']} - do not describe it"})
                continue
            p = BUILD / a["path"]
            if p.suffix.lower() == ".pdf":
                p = _rasterise(p)
            blocks.append(_image_block(p))
        return blocks

    df = results()
    if df.empty:
        return [{"type": "text", "text": "The results table is not available in this deployment."}]

    if name == "query_results":
        if args.get("filter"):
            df = df.query(args["filter"])
        if args.get("columns"):
            df = df[args["columns"]]
        if args.get("groupby"):
            out = df.groupby(args["groupby"]).describe().round(3)
        else:
            out = df.head(50).round(3)
        return [{"type": "text", "text": f"n = {len(df)}\n\n{out.to_string()}"}]

    if name == "plot_results":
        return [_plot(df, args)]

    return [{"type": "text", "text": f"Unknown tool {name}"}]


def _rasterise(pdf: Path) -> Path:
    import fitz  # PyMuPDF
    png = pdf.with_suffix(".png")
    if not png.exists():
        page = fitz.open(pdf)[0]
        page.get_pixmap(dpi=160).save(png)
    return png


def _plot(df: pd.DataFrame, args: dict) -> dict:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if args.get("filter"):
        df = df.query(args["filter"])
    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=150)
    x, y = args["x"], args.get("y")

    if args["kind"] == "scatter" and y:
        err = df[f"{y}_sd"] if f"{y}_sd" in df else None
        ax.errorbar(df[x], df[y], yerr=err, fmt="o", ms=4, lw=0.8, capsize=2, alpha=0.8)
        lo, hi = min(df[x].min(), df[y].min()), max(df[x].max(), df[y].max())
        ax.plot([lo, hi], [lo, hi], ls="--", lw=1, c="0.4", label="1:1")
        ax.legend(frameon=False)
        ax.set_xlabel(x); ax.set_ylabel(y)
    elif args["kind"] == "hist":
        ax.hist(df[x].dropna(), bins="auto", edgecolor="white")
        ax.set_xlabel(x); ax.set_ylabel("count")
    else:
        s = df[x].dropna().sort_values()
        n = len(s)
        ax.step(s, [(i + 1) / n for i in range(n)], where="post")
        ax.set_xlabel(x); ax.set_ylabel("cumulative proportion")

    ax.set_title(args.get("title", ""), fontsize=10)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.b64encode(buf.getvalue()).decode()}}
