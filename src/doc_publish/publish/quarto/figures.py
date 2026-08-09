"""Figure emission for the Quarto site, in strict preference order per figure:

  1. interactive - a plotly HTML export named in figures_manifest.json (from the
     plotly figure refactor), embedded via a relative <iframe>;
  2. static      - the PNG the ingest build resolved into build/figures/;
  3. pending     - a visible "awaiting regeneration" callout.

Never omitted silently, never described in absence - a caption with no artefact and
no warning would be a fabricated figure with a real label on it (the same rule the
Notion stream and CLAUDE.md state). Captions always come from the corpus; the
manifest supplies assets only.

figures_manifest.json is the refactor's output and does not exist yet. Expected
shape (documented guess, adapt when the refactor lands):
    {"<label>": {"interactive": "<path to .html>", "static": "<path to .png>"}}
Looked for in build/ then $DOC_REPO. Until it exists every figure falls back to
its PNG and the build report says so - the site must build before the refactor is
done.

Offline: interactive exports must be written with include_plotlyjs="directory" so
plotly.min.js sits next to them; the whole directory is copied into the site, and
nothing references a CDN.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from .qmd import float_slug


class FigureEmitter:
    def __init__(self, figures_json: list[dict], manifest_candidates: list[Path],
                 build_dir: Path, site_dir: Path):
        # live entry wins when a label appears both live and commented-out
        self.entries: dict[str, dict] = {}
        for f in figures_json:
            if f["label"] not in self.entries or self.entries[f["label"]].get("commented_out"):
                self.entries[f["label"]] = f
        self.build_dir = build_dir
        self.assets_dir = site_dir / "figures"
        self.assets_dir.mkdir(parents=True, exist_ok=True)
        self.tiers = {"interactive": [], "static": [], "pending": []}

        self.manifest: dict = {}
        self.manifest_path: Path | None = None
        for cand in manifest_candidates:
            if cand and cand.exists():
                self.manifest = json.loads(cand.read_text(encoding="utf-8"))
                self.manifest_path = cand
                break

    def emit(self, label: str, caption_md: str) -> str:
        slug = float_slug("figure", label)
        entry = self.entries.get(label)

        m = self.manifest.get(label, {})
        interactive = m.get("interactive")
        if interactive and self.manifest_path is not None:
            src = (self.manifest_path.parent / interactive).resolve()
            if src.exists():
                dest = self.assets_dir / src.name
                shutil.copy2(src, dest)
                for extra in src.parent.glob("plotly*.js"):
                    shutil.copy2(extra, self.assets_dir / extra.name)
                self.tiers["interactive"].append(label)
                return (f'::: {{#{slug}}}\n'
                        f'<iframe src="figures/{src.name}" width="100%" height="520" '
                        f'style="border:none;" loading="lazy" title="{label}"></iframe>\n\n'
                        f"{caption_md}\n:::\n")

        ok_assets = [a for a in (entry or {}).get("assets", []) if a.get("status") == "ok"]
        if ok_assets and not (entry or {}).get("commented_out"):
            self.tiers["static"].append(label)
            parts = []
            for i, asset in enumerate(ok_assets):
                src = self.build_dir / asset["path"]
                dest = self.assets_dir / src.name
                if src.exists():
                    shutil.copy2(src, dest)
                cap = caption_md if i == len(ok_assets) - 1 else ""
                ident = f"{{#{slug}}}" if i == len(ok_assets) - 1 else ""
                parts.append(f"![{cap}](figures/{src.name}){ident}\n")
            return "\n".join(parts)

        self.tiers["pending"].append(label)
        detail = caption_md or label
        return (f'::: {{#{slug} .callout-warning}}\n'
                f"**Figure {label} — awaiting regeneration.** The asset for this "
                f"figure has not been produced yet (notebook re-run pending); the "
                f"caption is shown so the reference stays meaningful.\n\n{detail}\n:::\n")

    def summary(self) -> str:
        base = (f"{len(self.tiers['interactive'])} interactive, "
                f"{len(self.tiers['static'])} static PNG, "
                f"{len(self.tiers['pending'])} awaiting regeneration")
        if self.manifest_path is None:
            base += " (figures_manifest.json not found - PNG fallback throughout, " \
                    "as expected before the plotly refactor lands)"
        return base
